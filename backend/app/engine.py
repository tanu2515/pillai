import difflib
import json
import math
import random
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from . import models
from .regions import INDIA_REGIONS

# --- risk engine (SRS Section 8.2) ------------------------------------

W_CAPACITY = 0.35
W_SURGE = 0.25
W_INSTABILITY = 0.15
W_RESOURCE = 0.15
W_TIME = 0.10

EVENT_START_HOUR = 14  # the event clock reads 14:00 -> 00:00 as tick advances
ACK_THRESHOLD = 65  # risk score at/above this can be acknowledged/escalated


def clip(value, lo, hi):
    return max(lo, min(hi, value))


def risk_level(score):
    if score <= 30:
        return "LOW"
    if score <= 55:
        return "MODERATE"
    if score <= 75:
        return "HIGH"
    return "CRITICAL"


def event_clock_label(tick):
    total_minutes = (EVENT_START_HOUR * 60 + tick) % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def risk_factors(current_count, capacity, delta, prev_delta, resource_pressure_pct):
    capacity_pressure_pct = (current_count / capacity * 100) if capacity else 0
    capacity_pressure = clip(capacity_pressure_pct, 0, 100)
    arrival_surge = clip((delta / (capacity * 0.05)) * 100, 0, 100) if capacity else 0
    flow_instability = clip((abs(delta - prev_delta) / (capacity * 0.03)) * 100, 0, 100) if capacity else 0
    resource_pressure = clip(resource_pressure_pct, 0, 100)

    remaining = capacity - current_count
    if delta > 0:
        time_to_capacity_min = max(remaining, 0) / delta
    else:
        time_to_capacity_min = 999
    time_to_criticality = clip(100 - time_to_capacity_min * (100 / 30), 0, 100)

    score = (
        W_CAPACITY * capacity_pressure
        + W_SURGE * arrival_surge
        + W_INSTABILITY * flow_instability
        + W_RESOURCE * resource_pressure
        + W_TIME * time_to_criticality
    )
    score = clip(score, 0, 100)

    return {
        "capacity_pressure_pct": round(capacity_pressure_pct, 1),
        "arrival_surge": round(arrival_surge, 1),
        "flow_instability": round(flow_instability, 1),
        "resource_pressure": round(resource_pressure, 1),
        "time_to_criticality": round(time_to_criticality, 1),
        "time_to_capacity_min": None if time_to_capacity_min == 999 else round(time_to_capacity_min, 1),
        "score": round(score, 1),
        "level": risk_level(score),
    }


def _linked_resource_pressure(zone, db):
    pressures = []
    if zone.linked_transport_zone_id:
        t = db.get(models.Zone, zone.linked_transport_zone_id)
        if t and t.capacity:
            pressures.append(t.current_count / t.capacity * 100)
    if zone.linked_hospitality_zone_id:
        h = db.get(models.Zone, zone.linked_hospitality_zone_id)
        if h and h.capacity:
            pressures.append(h.current_count / h.capacity * 100)
    if not pressures:
        return zone.current_count / zone.capacity * 100 if zone.capacity else 0
    return sum(pressures) / len(pressures)


def zone_risk(zone, db):
    delta = zone.current_count - zone.last_count
    resource_pressure_pct = _linked_resource_pressure(zone, db)
    return risk_factors(zone.current_count, zone.capacity, delta, zone.prev_delta, resource_pressure_pct)


# --- simulation clock ---------------------------------------------------

def get_live_event(db):
    """The one Event (of potentially many catalog rows) the crowd-monitoring/
    risk-engine side actually operates on. Every zone/alert/scenario/chatbot
    function in this file uses this instead of 'the only Event row' now that
    Event is a real multi-row catalog (see models.Event)."""
    return db.query(models.Event).filter(models.Event.status == "live").first()


def get_state_row(db):
    state = db.query(models.SimState).first()
    return state


def _log(db, tick, category, message, zone_domain=None):
    db.add(models.LogEntry(tick=tick, category=category, message=message, zone_domain=zone_domain))
    db.commit()


def recent_log(db, category=None, limit=50):
    q = db.query(models.LogEntry).order_by(models.LogEntry.id.desc())
    if category:
        q = q.filter(models.LogEntry.category == category)
    entries = list(reversed(q.limit(limit).all()))
    return [
        {
            "id": e.id, "tick": e.tick, "clock": event_clock_label(e.tick),
            "category": e.category, "message": e.message, "zone_domain": e.zone_domain,
        }
        for e in entries
    ]


def trigger_scenario(db, scenario_id):
    scenario = db.get(models.Scenario, scenario_id)
    state = get_state_row(db)
    if not scenario or not state:
        return None
    state.scenario_active = True
    state.active_scenario_id = scenario.id
    state.trigger_tick = state.tick
    db.commit()
    _log(db, state.tick, "scenario", f"Scenario triggered: {scenario.name}" + (f" — {scenario.description}" if scenario.description else ""))
    return state


def advance_tick(db):
    state = get_state_row(db)
    if state is None:
        return None
    state.tick += 1

    scenario = db.get(models.Scenario, state.active_scenario_id) if state.active_scenario_id else None
    effects = json.loads(scenario.effects_json) if scenario else {}
    ticks_since_trigger = state.tick - state.trigger_tick if state.scenario_active else -1
    duration = scenario.duration_ticks if scenario else 0
    ramping = state.scenario_active and 0 <= ticks_since_trigger < duration

    zones = db.query(models.Zone).all()
    old_levels = {z.id: zone_risk(z, db)["level"] for z in zones}

    for zone in zones:
        delta = effects.get(zone.name, 0) if ramping else 0
        new_count = zone.current_count + delta
        if zone.type == "hotel":
            new_count = min(zone.capacity, new_count)  # a hotel can't hold more guests than it has rooms
        # Gates/corridors/hubs are deliberately allowed past 100% — demand can
        # exceed capacity (that's congestion), unlike a hotel's physical rooms.
        new_count = max(0, new_count)
        zone.prev_delta = zone.current_count - zone.last_count
        zone.last_count = zone.current_count
        zone.current_count = new_count
        if zone.current_count > (zone.peak_count or 0):
            zone.peak_count = zone.current_count
            zone.peak_tick = state.tick

    db.commit()

    # Incident Timeline: a zone newly crossing into HIGH/CRITICAL is the
    # moment worth recording — re-scored after commit so linked-resource
    # pressure reflects every zone's post-tick state, not a half-updated one.
    for zone in zones:
        r = zone_risk(zone, db)
        new_level = r["level"]
        if old_levels[zone.id] not in ("HIGH", "CRITICAL") and new_level in ("HIGH", "CRITICAL"):
            _log(db, state.tick, "zone_critical", f"{zone.name} crossed into {new_level}.", zone_domain=zone.domain)
            _open_alert(db, zone, r)
        elif old_levels[zone.id] in ("HIGH", "CRITICAL") and new_level not in ("HIGH", "CRITICAL"):
            _resolve_alerts_for_zone(db, zone.id)

    return state


# --- alerts + notifications (persisted lifecycle) --------------------------

def _open_alert(db, zone, risk):
    """One open crowd alert per zone at a time — re-triggering while an alert
    for this zone is already open just leaves it open rather than duplicating."""
    existing = (
        db.query(models.Alert)
        .filter(models.Alert.zone_id == zone.id, models.Alert.status != "resolved", models.Alert.alert_type == "crowd")
        .first()
    )
    if existing:
        existing.severity = risk["level"]
        existing.impact_score = risk["score"]
        db.commit()
        return existing
    event = get_live_event(db)
    alert = models.Alert(
        event_id=event.id if event else None, zone_id=zone.id, alert_type="crowd",
        severity=risk["level"], impact_score=risk["score"],
        message=f"{zone.name} occupancy reached {risk['capacity_pressure_pct']}% ({risk['level']}).",
    )
    db.add(alert)
    db.flush()
    db.add(models.Notification(
        event_id=alert.event_id, alert_id=alert.id, audience_role=None, zone_domain=zone.domain,
        title=f"{risk['level']}: {zone.name}", message=alert.message, priority=risk["level"],
    ))
    if zone.type == "gate":
        db.add(models.Notification(
            event_id=alert.event_id, alert_id=alert.id, audience_role="Attendee", zone_domain=zone.domain,
            title="Gate crowded", message=f"{zone.name} is currently crowded. Please use a quieter gate if possible.",
            priority=risk["level"],
        ))
    db.commit()
    return alert


def _resolve_alerts_for_zone(db, zone_id):
    open_alerts = db.query(models.Alert).filter(models.Alert.zone_id == zone_id, models.Alert.status != "resolved").all()
    for a in open_alerts:
        a.status = "resolved"
        a.resolved_at = datetime.now(timezone.utc)
    if open_alerts:
        db.commit()


def list_alerts(db, status=None):
    q = db.query(models.Alert).order_by(models.Alert.id.desc())
    if status:
        q = q.filter(models.Alert.status == status)
    out = []
    for a in q.limit(100).all():
        zone = db.get(models.Zone, a.zone_id) if a.zone_id else None
        out.append({
            "id": a.id, "alert_type": a.alert_type, "severity": a.severity,
            "impact_score": a.impact_score, "message": a.message, "status": a.status,
            "zone_id": a.zone_id, "zone_name": zone.name if zone else None,
            "zone_domain": zone.domain if zone else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        })
    return out


def set_alert_status(db, alert_id, status):
    if status not in ("open", "acknowledged", "resolved"):
        return None
    alert = db.get(models.Alert, alert_id)
    if not alert:
        return None
    alert.status = status
    if status == "resolved":
        alert.resolved_at = datetime.now(timezone.utc)
    db.commit()
    return alert


def list_notifications(db, role=None, limit=50):
    q = db.query(models.Notification).order_by(models.Notification.id.desc())
    if role:
        q = q.filter((models.Notification.audience_role == role) | (models.Notification.audience_role.is_(None)))
    return [
        {
            "id": n.id, "title": n.title, "message": n.message, "priority": n.priority,
            "audience_role": n.audience_role, "zone_domain": n.zone_domain, "is_read": n.is_read,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in q.limit(limit).all()
    ]


def mark_notification_read(db, notification_id):
    n = db.get(models.Notification, notification_id)
    if not n:
        return None
    n.is_read = True
    db.commit()
    return n


def reset_simulation(db):
    """Wipes only the live event and its zones/bookings — catalog events
    (upcoming/completed, browsable via the event listing) are untouched."""
    live = get_live_event(db)
    if live:
        db.query(models.Zone).filter(models.Zone.event_id == live.id).delete()
        db.query(models.VisitorProfile).filter(
            (models.VisitorProfile.event_id == live.id) | (models.VisitorProfile.event_id.is_(None))
        ).delete(synchronize_session=False)
        db.query(models.Event).filter(models.Event.id == live.id).delete()
    db.query(models.Resource).delete()
    db.query(models.SimState).delete()
    db.query(models.LogEntry).delete()
    db.commit()
    from .seed import seed_if_empty
    seed_if_empty(db)


# --- scenario authoring (Administrator) ------------------------------------

def list_scenarios(db):
    scenarios = db.query(models.Scenario).order_by(models.Scenario.id).all()
    return [
        {"id": s.id, "name": s.name, "description": s.description,
         "duration_ticks": s.duration_ticks, "effects": json.loads(s.effects_json)}
        for s in scenarios
    ]


def create_scenario(db, name, description, duration_ticks, effects):
    scenario = models.Scenario(
        name=name, description=description, duration_ticks=duration_ticks,
        effects_json=json.dumps(effects),
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


def update_scenario(db, scenario_id, name, description, duration_ticks, effects):
    scenario = db.get(models.Scenario, scenario_id)
    if not scenario:
        return None
    scenario.name = name
    scenario.description = description
    scenario.duration_ticks = duration_ticks
    scenario.effects_json = json.dumps(effects)
    db.commit()
    return scenario


def delete_scenario(db, scenario_id):
    scenario = db.get(models.Scenario, scenario_id)
    if not scenario:
        return False
    state = get_state_row(db)
    if state and state.active_scenario_id == scenario_id:
        state.scenario_active = False
        state.active_scenario_id = None
    db.delete(scenario)
    db.commit()
    return True


# --- acknowledge / escalate ----------------------------------------------

def set_ack_status(db, zone_id, status):
    if status not in ("open", "acknowledged", "escalated"):
        return None
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return None
    zone.ack_status = status
    db.commit()
    if status in ("acknowledged", "escalated"):
        state = get_state_row(db)
        _log(db, state.tick if state else 0, status, f"{zone.name} {status}.", zone_domain=zone.domain)
    return zone


# --- consolidated state ---------------------------------------------------

def full_state(db):
    state = get_state_row(db)
    if state is None:
        return {"configured": False, "tick": 0, "clock": event_clock_label(0),
                "scenario_active": False, "zones": [], "resources": []}

    zones = db.query(models.Zone).all()
    resources = db.query(models.Resource).all()

    zone_out = []
    for z in zones:
        r = zone_risk(z, db)
        zone_out.append({
            "id": z.id, "name": z.name, "type": z.type, "domain": z.domain,
            "location_note": z.location_note, "lat": z.lat, "lng": z.lng,
            "capacity": z.capacity, "current_count": z.current_count,
            "linked_transport_zone_id": z.linked_transport_zone_id,
            "linked_hospitality_zone_id": z.linked_hospitality_zone_id,
            # HIGH/CRITICAL is the operator-facing signal (color-coded, stable);
            # the raw score can dip briefly right when a ramp's per-tick delta
            # hits zero (flow-instability spike), so a HIGH/CRITICAL zone stays
            # acknowledgeable even if its score is momentarily just under 65.
            "ack_status": z.ack_status,
            "can_ack": r["score"] >= ACK_THRESHOLD or r["level"] in ("HIGH", "CRITICAL"),
            **r,
        })
    zone_out.sort(key=lambda x: x["score"], reverse=True)

    resource_out = [{
        "type": res.type, "quantity_total": res.quantity_total,
        "quantity_available": res.quantity_available,
    } for res in resources]

    return {
        "tick": state.tick,
        "clock": event_clock_label(state.tick),
        "scenario_active": state.scenario_active,
        "zones": zone_out,
        "resources": resource_out,
    }


# --- Event Health Score (Section 29) ---------------------------------------
# One composite number per domain + overall, built from the same per-zone
# risk scores everything else already computes — a pure aggregation, no new
# inputs, so there's nothing to keep in sync with the risk engine above.

def event_health_score(db):
    state = full_state(db)
    zones = state["zones"]
    if not zones:
        return {"overall": 100, "domains": {}, "safety": 100}

    by_domain = {}
    for z in zones:
        by_domain.setdefault(z["domain"], []).append(z["score"])

    domain_health = {d: round(100 - sum(scores) / len(scores), 1) for d, scores in by_domain.items()}
    open_alerts = list_alerts(db, status="open") + list_alerts(db, status="acknowledged")
    critical_open = sum(1 for a in open_alerts if a["severity"] == "CRITICAL")
    safety = round(max(0, 100 - critical_open * 20 - len(open_alerts) * 5), 1)

    weights = {"venue": 0.4, "transport": 0.3, "hospitality": 0.3}
    weighted_sum = sum(domain_health.get(d, 100) * w for d, w in weights.items())
    overall = round(0.85 * weighted_sum + 0.15 * safety, 1)

    return {
        "overall": clip(overall, 0, 100),
        "domains": domain_health,
        "safety": safety,
        "open_alerts": len(open_alerts),
        "critical_alerts": critical_open,
    }


def causal_chain(db):
    """Walks whichever zones the *active* scenario's own effects_json names,
    in the order the scenario author listed them — generic to any scenario,
    not hardcoded to Gate 2/Corridor B/Hotel A."""
    state = get_state_row(db)
    if not state or not state.scenario_active or not state.active_scenario_id:
        return ["No active scenario. Trigger a scenario to see the causal chain build up live."]
    scenario = db.get(models.Scenario, state.active_scenario_id)
    effects = json.loads(scenario.effects_json)
    chain = [scenario.description or f"{scenario.name} begins"]
    for zone_name in effects:
        zone = db.query(models.Zone).filter(models.Zone.name == zone_name).first()
        if not zone:
            continue
        r = zone_risk(zone, db)
        chain.append(f"{zone.name} rises to {r['capacity_pressure_pct']}% ({r['level']})")
    return chain


def zone_causal_chain(db, zone_id):
    """Per-zone Risk Detail: if this zone is one the active scenario's
    effects target, show the full scenario chain; otherwise a one-step
    breakdown of just this zone's own risk factors."""
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return []
    state = get_state_row(db)
    if state and state.scenario_active and state.active_scenario_id:
        scenario = db.get(models.Scenario, state.active_scenario_id)
        effects = json.loads(scenario.effects_json)
        if zone.name in effects:
            return causal_chain(db)
    r = zone_risk(zone, db)
    return [
        f"{zone.name} is at {r['capacity_pressure_pct']}% of capacity ({r['level']})",
        f"Arrival surge factor: {r['arrival_surge']} · Flow instability: {r['flow_instability']} · "
        f"Linked-resource pressure: {r['resource_pressure']}",
    ]


# --- what-if simulator (recomputes live, never pre-baked) -----------------

BUS_CAPACITY_EACH = 50


def run_whatif(db, redirect_count=0, open_gate3=False, add_buses=0, move_staff=0, from_zone="Gate 2", to_zone="Gate 3"):
    """Generalized to any (from_zone, to_zone) pair — defaults to Gate 2/Gate 3
    so the existing dashboard keeps working unchanged, but any operator- or
    Administrator-added zone pair can now be previewed the same way."""
    gate2 = db.query(models.Zone).filter(models.Zone.name == from_zone).first()
    gate3 = db.query(models.Zone).filter(models.Zone.name == to_zone).first()
    if not gate2 or not gate3:
        return None
    corridor_b = db.get(models.Zone, gate2.linked_transport_zone_id)
    hotel_a = db.get(models.Zone, gate2.linked_hospitality_zone_id)

    before = {
        "gate2": zone_risk(gate2, db),
        "gate3": zone_risk(gate3, db),
        "corridor_b_pct": round(corridor_b.current_count / corridor_b.capacity * 100, 1),
        "hotel_a_pct": round(hotel_a.current_count / hotel_a.capacity * 100, 1),
    }

    warnings = []
    actual_redirect = redirect_count
    if redirect_count > 0 and not open_gate3:
        warnings.append("Redirect requires Gate 3 to be open — no visitors were actually moved.")
        actual_redirect = 0

    new_gate3_count = gate3.current_count
    if open_gate3 and actual_redirect > 0:
        room = max(gate3.capacity - gate3.current_count, 0)
        if actual_redirect > room:
            warnings.append(
                f"Gate 3 only has room for {room} more visitors — capping redirect there "
                f"instead of pushing it over capacity."
            )
            actual_redirect = room
        new_gate3_count = gate3.current_count + actual_redirect

    new_gate2_count = max(0, gate2.current_count - actual_redirect)

    new_corridor_b_capacity = corridor_b.capacity + add_buses * BUS_CAPACITY_EACH
    new_corridor_b_pct = round(corridor_b.current_count / new_corridor_b_capacity * 100, 1) if new_corridor_b_capacity else 0

    staff_relief = min(move_staff * 1.5, 20)

    # Use each zone's own observed (natural) delta, not a delta derived from the
    # redirected total — a planned, staged redirect is not the same thing as a
    # sudden arrival surge, and scoring it as one would wrongly flag the
    # *receiving* gate as newly critical right after an intervention that is
    # supposed to reduce overall risk.
    gate2_natural_delta = gate2.current_count - gate2.last_count
    gate3_natural_delta = gate3.current_count - gate3.last_count

    gate2_resource_pressure = (new_corridor_b_pct + before["hotel_a_pct"]) / 2
    gate2_after = risk_factors(
        new_gate2_count, gate2.capacity, gate2_natural_delta, gate2.prev_delta,
        clip(gate2_resource_pressure - staff_relief, 0, 100),
    )

    gate3_resource_pressure = (
        _linked_resource_pressure(gate3, db)
        if (gate3.linked_transport_zone_id or gate3.linked_hospitality_zone_id)
        else (new_gate3_count / gate3.capacity * 100 if gate3.capacity else 0)
    )
    gate3_after = risk_factors(
        new_gate3_count, gate3.capacity, gate3_natural_delta, gate3.prev_delta,
        clip(gate3_resource_pressure - staff_relief, 0, 100),
    )

    after = {
        "gate2": gate2_after,
        "gate3": gate3_after,
        "corridor_b_pct": new_corridor_b_pct,
        "hotel_a_pct": before["hotel_a_pct"],
        "actual_redirect_applied": actual_redirect,
    }

    return {"before": before, "after": after, "warnings": warnings}


def compare_plans(db, plans):
    """Runs run_whatif once per named plan against the same live baseline.
    run_whatif never mutates state, so every plan previews against an
    identical 'now' — that's what makes the results directly comparable
    side by side instead of each one drifting against a different moment."""
    results = []
    for plan in plans:
        r = run_whatif(db, plan.redirect_count, plan.open_gate3, plan.add_buses, plan.move_staff)
        if r is None:
            continue
        results.append({"name": plan.name, **r})

    if results:
        best = min(results, key=lambda r: (r["after"]["gate2"]["score"], r["after"]["gate3"]["score"]))
        for r in results:
            r["recommended"] = r is best

    return results


# --- orchestration / action optimizer (Section 16) -------------------------

# domain = which operator role this action belongs to (Event Command Operator
# always sees/approves every domain; the other three operator roles only see
# and approve actions scoped to their own resource, per the shared
# approve/override use case).
CANDIDATE_ACTIONS = [
    dict(id="redirect", label="Redirect 2,000 visitors to Gate 3", domain="venue", resource_type=None, required=0,
         risk_reduction=40, capacity_balance=70, visitor_experience=55, time_to_impact=95, cost_efficiency=100,
         target_zones=["Gate 2"]),
    dict(id="open_gate3", label="Open Gate 3 additional lane", domain="venue", resource_type="staff", required=2,
         risk_reduction=15, capacity_balance=60, visitor_experience=80, time_to_impact=85, cost_efficiency=80,
         target_zones=["Gate 2"]),
    dict(id="dispatch_buses", label="Dispatch 4 buses to Corridor B", domain="transport", resource_type="bus", required=4,
         risk_reduction=25, capacity_balance=50, visitor_experience=70, time_to_impact=60, cost_efficiency=50,
         target_zones=["Corridor B"]),
    dict(id="move_staff", label="Move 6 staff to Gate 2/Gate 3", domain="venue", resource_type="staff", required=6,
         risk_reduction=10, capacity_balance=40, visitor_experience=65, time_to_impact=70, cost_efficiency=60,
         target_zones=["Gate 2", "Gate 3"]),
    dict(id="recommend_hotel_b", label="Recommend Hotel B for new demand", domain="hospitality", resource_type=None, required=0,
         risk_reduction=12, capacity_balance=55, visitor_experience=75, time_to_impact=90, cost_efficiency=100,
         target_zones=["Hotel A"]),
]


def _action_urgency(db, action):
    """How hot the zone(s) this action actually affects are right now — lets
    ranking reflect live state instead of only the action's fixed properties,
    so e.g. dispatching buses ranks higher once Corridor B is actually under
    pressure, not just because it's generically a decent action."""
    zones_by_name = {z.name: z for z in db.query(models.Zone).all()}
    scores = [zone_risk(zones_by_name[n], db)["score"] for n in action["target_zones"] if n in zones_by_name]
    return max(scores) if scores else 0


def generate_recommendations(db):
    resources = {r.type: r for r in db.query(models.Resource).all()}
    ranked = []
    for action in CANDIDATE_ACTIONS:
        if action["resource_type"]:
            res = resources.get(action["resource_type"])
            available = res.quantity_available if res else 0
            if available < action["required"]:
                continue  # hard constraint: never recommend a resource you don't have
            feasibility = 100
        else:
            feasibility = 100

        urgency = _action_urgency(db, action)
        score = (
            0.28 * action["risk_reduction"]
            + 0.16 * action["capacity_balance"]
            + 0.12 * action["visitor_experience"]
            + 0.12 * feasibility
            + 0.08 * action["time_to_impact"]
            + 0.04 * action["cost_efficiency"]
            + 0.20 * urgency
        )
        ranked.append({**action, "feasibility": feasibility, "urgency": round(urgency, 1), "action_score": round(score, 1)})

    ranked.sort(key=lambda a: a["action_score"], reverse=True)
    return ranked


PREVENTIVE_LOOKAHEAD_MIN = 15  # flag zones on track to hit capacity within this window, before they're already HIGH/CRITICAL


def preventive_alerts(db):
    """'Act before it's critical': a MODERATE zone climbing fast toward capacity
    looks identical to a MODERATE zone holding steady if you only look at the
    current score — this surfaces the ones with a short time_to_capacity_min
    so operators can intervene ahead of the surge instead of reacting to it."""
    zones_by_name = {z.name: z for z in db.query(models.Zone).all()}
    recs = generate_recommendations(db)

    alerts = []
    for zone in zones_by_name.values():
        r = zone_risk(zone, db)
        if r["level"] in ("HIGH", "CRITICAL"):
            continue  # already surfaced by the live Risk Register — nothing "preventive" left to say
        ttc = r["time_to_capacity_min"]
        if ttc is None or ttc > PREVENTIVE_LOOKAHEAD_MIN:
            continue

        # Projected risk if this zone is left alone until it hits capacity —
        # same delta/prev_delta/resource pressure, just current_count at 100%.
        projected = risk_factors(
            zone.capacity, zone.capacity,
            zone.current_count - zone.last_count, zone.prev_delta,
            r["resource_pressure"],
        )
        recs_for_zone = [rec for rec in recs if zone.name in rec["target_zones"]]

        alerts.append({
            "zone_id": zone.id,
            "zone_name": zone.name,
            "zone_domain": zone.domain,
            "minutes_to_capacity": ttc,
            "current_score": r["score"],
            "current_level": r["level"],
            "projected_score": projected["score"],
            "projected_level": projected["level"],
            "recommended_actions": [
                {"id": rec["id"], "label": rec["label"], "action_score": rec["action_score"]}
                for rec in recs_for_zone[:2]
            ],
        })

    alerts.sort(key=lambda a: a["minutes_to_capacity"])
    return alerts


# --- off-peak travel recommendations ----------------------------------------
# "Recommend off-peak travel" (PS-8's own wording) — distinct from redirecting
# a visitor sideways to a quieter gate: this tells them WHEN to arrive.
# For the live event we reason from the actual scenario ramp (a surge has a
# known start/duration, so "after the surge ends" is a real, grounded ETA,
# not a guess). For an event that hasn't gone live yet there's no arrival-rate
# data at all, so event_detail() falls back to a schedule-based heuristic
# instead of calling this.

def _occupancy_level(pct):
    """SRS Section 10's own thresholds — deliberately NOT the composite risk
    score (which blends in short-term velocity and can read LOW/MODERATE even
    at 90%+ occupancy if arrivals happen to be flat at that exact tick). Off-
    peak advice is about how full the gate physically is right now, so it
    reasons from raw occupancy directly."""
    if pct >= 90:
        return "CRITICAL"
    if pct >= 80:
        return "HIGH"
    if pct >= 60:
        return "MODERATE"
    return "NORMAL"


def offpeak_recommendations(db):
    state = get_state_row(db)
    if not state:
        return []
    gates = db.query(models.Zone).filter(models.Zone.type == "gate").all()

    scenario = db.get(models.Scenario, state.active_scenario_id) if state.active_scenario_id else None
    ticks_since_trigger = state.tick - state.trigger_tick if state.scenario_active else -1
    duration = scenario.duration_ticks if scenario else 0
    ramping = state.scenario_active and scenario and 0 <= ticks_since_trigger < duration
    ramp_ticks_remaining = (duration - ticks_since_trigger) if ramping else 0

    out = []
    for zone in gates:
        r = zone_risk(zone, db)
        occ_level = _occupancy_level(r["capacity_pressure_pct"])
        if occ_level in ("HIGH", "CRITICAL") and ramp_ticks_remaining > 0:
            eta_tick = state.tick + ramp_ticks_remaining
            out.append({
                "zone_id": zone.id, "zone_name": zone.name, "current_level": occ_level,
                "current_pct": r["capacity_pressure_pct"],
                "best_time_clock": event_clock_label(eta_tick), "minutes_to_better": ramp_ticks_remaining,
                "recommendation": f"{zone.name} is busy right now ({r['capacity_pressure_pct']}%). The current surge is "
                                   f"expected to ease by {event_clock_label(eta_tick)} (~{ramp_ticks_remaining} min) — "
                                   f"arriving after that will mean a shorter wait.",
            })
        elif occ_level in ("HIGH", "CRITICAL"):
            out.append({
                "zone_id": zone.id, "zone_name": zone.name, "current_level": occ_level,
                "current_pct": r["capacity_pressure_pct"],
                "best_time_clock": None, "minutes_to_better": None,
                "recommendation": f"{zone.name} is busy ({r['capacity_pressure_pct']}%) and isn't expected to ease on "
                                   f"its own soon — a different gate is a better bet than waiting.",
            })
        elif r["arrival_surge"] > 40:
            out.append({
                "zone_id": zone.id, "zone_name": zone.name, "current_level": occ_level,
                "current_pct": r["capacity_pressure_pct"],
                "best_time_clock": event_clock_label(state.tick), "minutes_to_better": 0,
                "recommendation": f"{zone.name} is filling up quickly ({r['capacity_pressure_pct']}%) — arriving in the "
                                   f"next few minutes beats waiting.",
            })
        else:
            out.append({
                "zone_id": zone.id, "zone_name": zone.name, "current_level": occ_level,
                "current_pct": r["capacity_pressure_pct"],
                "best_time_clock": event_clock_label(state.tick), "minutes_to_better": 0,
                "recommendation": f"{zone.name} is currently a good time to arrive ({occ_level}, {r['capacity_pressure_pct']}%).",
            })
    out.sort(key=lambda a: {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "NORMAL": 3}[a["current_level"]])
    return out


def schedule_offpeak_hint(event_time):
    """Heuristic used only for an event that hasn't gone live yet (no real
    arrival-rate data exists). Grounded in one honest, well-known pattern —
    entry queues peak right around the stated start time — not a fabricated
    prediction; framed as a general tip, not a live measurement."""
    if not event_time:
        return None
    try:
        h, m = map(int, event_time.split(":"))
    except (ValueError, AttributeError):
        return None
    def fmt(hh, mm):
        period = "PM" if hh >= 12 else "AM"
        hr12 = ((hh + 11) % 12) + 1
        return f"{hr12}:{mm:02d} {period}"
    early = fmt((h + 22) % 24, m)  # ~2 hours before start
    late = fmt((h + 1) % 24, m)    # ~1 hour after start
    return (f"Entry queues are typically busiest right around the {fmt(h, m)} start time. "
            f"Arriving before {early} or after {late} usually means a shorter wait at the gate.")


GATE3_LANE_CAPACITY_BOOST = 500  # "open an additional lane" as a persisted capacity increase
STAFF_PROCESSING_RELIEF = 150  # per zone queue drained faster once staff arrive — demo-scale, not physically derived
HOTEL_B_REDIRECT_FRACTION = 0.10  # cap how much of Hotel A's current guests get moved in one approval


def _rebaseline(zone):
    """A planned, approved move isn't an organic arrival surge — without this,
    the risk engine would read the jump in current_count as a huge delta next
    time it scores the zone and immediately flag it CRITICAL right after the
    intervention that was supposed to relieve it (same reasoning run_whatif
    already applies via its 'natural delta', just persisted here instead of
    only previewed)."""
    zone.last_count = zone.current_count
    zone.prev_delta = 0


def _execute_action_effect(db, action):
    """Applies a candidate action's effect to the *live* simulation state —
    this is what makes 'Approve' real orchestration instead of only a
    resource-ledger deduction. Mirrors the same math run_whatif already
    previews, just persisted instead of discarded after the request."""
    gate2 = db.query(models.Zone).filter(models.Zone.name == "Gate 2").first()
    gate3 = db.query(models.Zone).filter(models.Zone.name == "Gate 3").first()
    corridor_b = db.get(models.Zone, gate2.linked_transport_zone_id) if gate2 else None
    hotel_a = db.get(models.Zone, gate2.linked_hospitality_zone_id) if gate2 else None
    hotel_b = db.query(models.Zone).filter(models.Zone.name == "Hotel B").first()

    if action["id"] == "redirect" and gate2 and gate3:
        room = max(gate3.capacity - gate3.current_count, 0)
        moved = max(min(2000, gate2.current_count, room), 0)
        gate2.current_count -= moved
        gate3.current_count += moved
        _rebaseline(gate2)
        _rebaseline(gate3)
        return f"Moved {moved} visitors from Gate 2 to Gate 3."

    if action["id"] == "open_gate3" and gate3:
        gate3.capacity += GATE3_LANE_CAPACITY_BOOST
        return f"Gate 3 capacity increased by {GATE3_LANE_CAPACITY_BOOST} (additional lane opened)."

    if action["id"] == "dispatch_buses" and corridor_b:
        added = action["required"] * BUS_CAPACITY_EACH
        corridor_b.capacity += added
        return f"Corridor B transport capacity increased by {added} ({action['required']} buses dispatched)."

    if action["id"] == "move_staff":
        targets = [z for z in (gate2, gate3) if z]
        for z in targets:
            z.current_count = max(0, z.current_count - STAFF_PROCESSING_RELIEF)
            _rebaseline(z)
        return "Staff moved to Gate 2/Gate 3 — faster processing drained the queue backlog." if targets else "No target zones found."

    if action["id"] == "recommend_hotel_b" and hotel_a and hotel_b:
        room = max(hotel_b.capacity - hotel_b.current_count, 0)
        moved = max(min(hotel_a.current_count, room, round(hotel_a.capacity * HOTEL_B_REDIRECT_FRACTION)), 0)
        hotel_a.current_count -= moved
        hotel_b.current_count += moved
        _rebaseline(hotel_a)
        _rebaseline(hotel_b)
        return f"Redirected {moved} guests from Hotel A to Hotel B."

    return "No live-state effect model for this action — resource reserved only."


def approve_actions(db, action_ids):
    resources = {r.type: r for r in db.query(models.Resource).all()}
    state = get_state_row(db)
    applied = []
    for action in CANDIDATE_ACTIONS:
        if action["id"] not in action_ids:
            continue
        if action["resource_type"]:
            res = resources.get(action["resource_type"])
            if not res or res.quantity_available < action["required"]:
                continue
            res.quantity_available -= action["required"]
        effect_note = _execute_action_effect(db, action)
        db.commit()
        _log(db, state.tick if state else 0, "action_executed", f"{action['label']} — {effect_note}", zone_domain=action["domain"])
        applied.append(action["id"])
    return applied


def escalations(db):
    """Zones already HIGH/CRITICAL with zero feasible recommended action
    targeting them — surfaces the 'nothing safe left to suggest, this needs a
    human call' case instead of the UI silently showing an empty list."""
    state = full_state(db)
    recs = generate_recommendations(db)
    covered = {zn for rec in recs for zn in rec["target_zones"]}
    return [
        {"zone_id": z["id"], "zone_name": z["name"], "zone_domain": z["domain"], "score": z["score"], "level": z["level"]}
        for z in state["zones"]
        if z["level"] in ("HIGH", "CRITICAL") and z["name"] not in covered
    ]


# --- registration & check-in (Section 7.2) ---------------------------------

def register_visitor(db, name, gate_zone_id, walk_in=False):
    visitor = models.VisitorProfile(
        name=name, gate_zone_id=gate_zone_id, code=uuid.uuid4().hex[:8], walk_in=walk_in,
    )
    db.add(visitor)
    db.commit()
    db.refresh(visitor)
    return visitor


def checkin(db, code):
    """Resolves either the original direct-gate flow (gate_zone_id set) or a
    catalog booking (tier_id set) — a tier booking only resolves to an actual
    Zone once its event is the live one (gate assignment tied to tier, per
    the tier's gate_name label); otherwise there's nothing to check into yet."""
    visitor = db.query(models.VisitorProfile).filter(models.VisitorProfile.code == code).first()
    if not visitor:
        return {"status": "not_found"}
    if visitor.checked_in:
        return {"status": "already_checked_in"}

    zone = None
    if visitor.gate_zone_id:
        zone = db.get(models.Zone, visitor.gate_zone_id)
    elif visitor.tier_id:
        tier = db.get(models.EventTier, visitor.tier_id)
        live = get_live_event(db)
        if not tier or not live or tier.event_id != live.id:
            return {"status": "not_live", "message": "This event hasn't gone live yet — check-in opens on the event day."}
        if tier.gate_name:
            zone = db.query(models.Zone).filter(
                models.Zone.event_id == live.id, models.Zone.name == tier.gate_name,
            ).first()

    if not zone:
        return {"status": "not_found"}

    remaining = zone.capacity - zone.current_count
    if remaining <= 0:
        alternate = (
            db.query(models.Zone)
            .filter(models.Zone.type == "gate", models.Zone.id != zone.id)
            .order_by((models.Zone.capacity - models.Zone.current_count).desc())
            .first()
        )
        return {
            "status": "redirect",
            "message": f"{zone.name} is at capacity.",
            "suggested_zone": alternate.name if alternate else None,
        }

    zone.current_count += 1
    visitor.checked_in = True
    db.commit()
    return {"status": "ok", "zone": zone.name, "remaining_capacity": zone.capacity - zone.current_count}


# --- multi-event attendee registration (Sections 4-5) -----------------------
# Attendee <-> Event is many-to-many (event_attendees), independent of the
# single "live simulated" Event row create_event/delete_event manage — an
# attendee's registration history survives even after that event is later
# deleted or replaced, since event_name/event_date are snapshotted at
# registration time rather than following the live Event row's lifecycle.

def _get_or_create_attendee(db, email):
    account = db.query(models.UserAccount).filter(models.UserAccount.email == email, models.UserAccount.role == "Attendee").first()
    if not account:
        account = models.UserAccount(email=email, role="Attendee")
        db.add(account)
        db.commit()
        db.refresh(account)
    attendee = db.query(models.Attendee).filter(models.Attendee.user_account_id == account.id).first()
    if not attendee:
        attendee = models.Attendee(user_account_id=account.id, name=email.split("@")[0])
        db.add(attendee)
        db.commit()
        db.refresh(attendee)
    return account, attendee


def register_attendee_for_event(db, email):
    event = get_live_event(db)
    if not event:
        return None
    account, attendee = _get_or_create_attendee(db, email)
    existing = db.query(models.EventAttendee).filter(
        models.EventAttendee.attendee_id == attendee.id, models.EventAttendee.event_name == event.name,
    ).first()
    if existing:
        ea = existing
    else:
        ea = models.EventAttendee(
            attendee_id=attendee.id, event_id=event.id, event_name=event.name,
            event_date=event.region,
        )
        db.add(ea)
    account.current_event_id = ea.id if not existing else existing.id
    db.commit()
    db.refresh(ea)
    return {"event_attendee_id": ea.id, "event_name": ea.event_name}


def list_my_events(db, email):
    account, attendee = _get_or_create_attendee(db, email)
    live_event = get_live_event(db)
    regs = db.query(models.EventAttendee).filter(models.EventAttendee.attendee_id == attendee.id).order_by(models.EventAttendee.id.desc()).all()
    return [
        {
            "event_attendee_id": r.id, "event_name": r.event_name,
            "is_current": r.id == account.current_event_id,
            "is_live": bool(live_event and r.event_name == live_event.name),
            "registration_status": r.registration_status,
        }
        for r in regs
    ]


def set_current_event(db, email, event_attendee_id):
    account, attendee = _get_or_create_attendee(db, email)
    ea = db.get(models.EventAttendee, event_attendee_id)
    if not ea or ea.attendee_id != attendee.id:
        return None
    account.current_event_id = ea.id
    db.commit()
    return {"current_event_id": ea.id, "event_name": ea.event_name}


def current_event_context(db, email):
    """What the chatbot/dashboard should scope to for this attendee — asks
    the spec's own question ('which event do you mean?') when there's more
    than one registration and none has been explicitly selected yet."""
    account, attendee = _get_or_create_attendee(db, email)
    regs = list_my_events(db, email)
    if not regs:
        return {"status": "no_events"}
    if len(regs) == 1:
        return {"status": "ok", "event_name": regs[0]["event_name"]}
    current = next((r for r in regs if r["is_current"]), None)
    if current:
        return {"status": "ok", "event_name": current["event_name"]}
    return {"status": "ambiguous", "message": "You are registered for multiple events. Which event do you mean?",
            "events": [r["event_name"] for r in regs]}


# --- event catalog: browse, book a tier/seat (BookMyShow-style) ------------
# Independent of the live simulation's Zone model — an "upcoming" event can
# be listed, searched, and booked long before it ever goes live. A tier's
# gate_name is only resolved to a real Zone at check-in time (see checkin()),
# once that event is actually the live one.

SEAT_TIER_THRESHOLD = 10_000  # capacity at/above this also gets a bounded numbered-seat block
SEATS_PER_LARGE_TIER = 200  # Row A1..D50 — a representative numbered block, not one row per physical seat
SEAT_ROWS = "ABCD"
SEAT_COLS = 50


def _tier_available(tier):
    return max(tier.capacity - tier.booked_count, 0)


CATEGORIES = ["College Event", "Concert", "Conference", "Sports", "Festival", "Workshop"]


def _event_summary(db, e, tiers=None):
    tiers = tiers if tiers is not None else db.query(models.EventTier).filter(models.EventTier.event_id == e.id).all()
    prices = [t.price for t in tiers]
    total_available = sum(_tier_available(t) for t in tiers)
    return {
        "id": e.id, "name": e.name, "description": e.description, "event_date": e.event_date,
        "event_time": e.event_time, "category": e.category, "city": e.city, "venue_name": e.venue_name,
        "banner_emoji": e.banner_emoji or "🎉", "is_featured": e.is_featured,
        "region": e.region, "status": e.status,
        "min_price": min(prices) if prices else None, "max_price": max(prices) if prices else None,
        "total_available": total_available,
        "registration_status": "Sold Out" if tiers and total_available <= 0 else "Registration Open",
    }


def list_events(db, search=None, category=None, section=None):
    """section: None (all) | "popular" (featured) | "recommended" (featured,
    different slice) | "near_you" (same region as whichever event is live —
    a stand-in for real geolocation) | "upcoming" (status=upcoming, soonest first)."""
    q = db.query(models.Event)
    if search:
        q = q.filter(models.Event.name.ilike(f"%{search}%"))
    if category:
        q = q.filter(models.Event.category == category)

    if section == "near_you":
        live = get_live_event(db)
        if live and live.region:
            q = q.filter(models.Event.region == live.region)
    elif section == "upcoming":
        q = q.filter(models.Event.status == "upcoming").order_by(models.Event.event_date.is_(None), models.Event.event_date)
    elif section in ("popular", "recommended"):
        q = q.filter(models.Event.is_featured.is_(True))

    events = q.all()
    if section not in ("upcoming",):
        events.sort(key=lambda e: (e.event_date is None, e.event_date or ""))
    return [_event_summary(db, e) for e in events]


def event_detail(db, event_id):
    event = db.get(models.Event, event_id)
    if not event:
        return None
    tiers = db.query(models.EventTier).filter(models.EventTier.event_id == event_id).all()
    live = get_live_event(db)
    is_live = bool(live and live.id == event.id)

    gates, crowd_status, transport_info, hotels, announcements = [], None, None, [], []
    off_peak = None
    if is_live:
        state = full_state(db)
        gates = [
            {"name": z["name"], "occupancy_pct": z["capacity_pressure_pct"], "level": z["level"]}
            for z in state["zones"] if z["type"] == "gate"
        ]
        if gates:
            worst = max(gates, key=lambda g: g["occupancy_pct"])
            crowd_status = worst["level"]
        transport_info = transport_demand_prediction(db)
        hotels = hotel_recommendations(db)
        announcements = [
            {"severity": a["severity"], "message": a["message"], "created_at": a["created_at"]}
            for a in list_alerts(db)[:5]
        ]
        off_peak = offpeak_recommendations(db)
    else:
        hint = schedule_offpeak_hint(event.event_time)
        off_peak = [{"recommendation": hint}] if hint else []

    return {
        "id": event.id, "name": event.name, "description": event.description, "event_date": event.event_date,
        "event_time": event.event_time, "category": event.category, "city": event.city,
        "venue_name": event.venue_name, "venue_address": event.venue_address,
        "banner_emoji": event.banner_emoji or "🎉",
        "region": event.region, "status": event.status, "expected_attendance": event.expected_attendance,
        "safe_capacity": event.safe_capacity,
        "is_live": is_live,
        "gates": gates, "crowd_status": crowd_status, "transport_info": transport_info,
        "hotels": hotels, "announcements": announcements, "off_peak": off_peak,
        "tiers": [
            {
                "id": t.id, "name": t.name, "price": t.price, "capacity": t.capacity,
                "available": _tier_available(t), "uses_seats": t.uses_seats, "gate_name": t.gate_name,
            }
            for t in tiers
        ],
    }


def tier_seats(db, tier_id):
    tier = db.get(models.EventTier, tier_id)
    if not tier or not tier.uses_seats:
        return None
    seats = db.query(models.EventSeat).filter(models.EventSeat.tier_id == tier_id).order_by(models.EventSeat.id).all()
    return [{"id": s.id, "seat_label": s.seat_label, "status": s.status} for s in seats]


def create_event_listing(
    db, name, description, event_date, region, expected_attendance, safe_capacity, tiers,
    event_time=None, category=None, city=None, venue_name=None, venue_address=None,
    banner_emoji=None, is_featured=False,
):
    """Adds a new browsable/bookable event to the catalog — status defaults
    to 'upcoming', so it never interferes with whichever event is live.
    tiers: [{"name", "price", "capacity", "gate_name"}, ...]"""
    event = models.Event(
        name=name, description=description, event_date=event_date, event_time=event_time,
        category=category, city=city, venue_name=venue_name, venue_address=venue_address,
        banner_emoji=banner_emoji, is_featured=is_featured, region=region,
        expected_attendance=expected_attendance, safe_capacity=safe_capacity, status="upcoming",
    )
    db.add(event)
    db.flush()

    for t in tiers:
        uses_seats = t["capacity"] >= SEAT_TIER_THRESHOLD
        tier = models.EventTier(
            event_id=event.id, name=t["name"], price=t["price"], capacity=t["capacity"],
            gate_name=t.get("gate_name"), uses_seats=uses_seats,
        )
        db.add(tier)
        db.flush()
        if uses_seats:
            seat_count = 0
            for row in SEAT_ROWS:
                for col in range(1, SEAT_COLS + 1):
                    if seat_count >= SEATS_PER_LARGE_TIER:
                        break
                    db.add(models.EventSeat(tier_id=tier.id, seat_label=f"{row}{col}"))
                    seat_count += 1
    db.commit()
    return event


def book_tier(db, event_id, tier_id, name, seat_id=None, email=None, quantity=1, hotel_zone_id=None, wants_transport=False):
    """seat_id is optional even for a uses_seats tier — most of a large
    tier's capacity is general admission (only SEATS_PER_LARGE_TIER seats are
    individually numbered), so booking without one just draws from the same
    shared capacity pool. Passing seat_id reserves that specific numbered
    seat (always quantity 1); quantity only applies to general admission."""
    tier = db.get(models.EventTier, tier_id)
    if not tier or tier.event_id != event_id:
        return {"status": "not_found"}
    quantity = max(1, quantity)

    seat = None
    if seat_id is not None:
        if not tier.uses_seats:
            return {"status": "not_found", "message": "This tier doesn't offer numbered seats."}
        seat = db.get(models.EventSeat, seat_id)
        if not seat or seat.tier_id != tier_id:
            return {"status": "not_found"}
        if seat.status == "booked":
            return {"status": "seat_taken", "message": f"Seat {seat.seat_label} is already booked."}
        quantity = 1

    if _tier_available(tier) < quantity:
        return {"status": "sold_out", "message": f"Only {_tier_available(tier)} left in {tier.name}."}

    booking = models.VisitorProfile(
        name=name, email=email, event_id=event_id, tier_id=tier_id, seat_id=seat.id if seat else None,
        quantity=quantity, hotel_zone_id=hotel_zone_id, wants_transport=wants_transport,
    )
    db.add(booking)
    if seat:
        seat.status = "booked"
    tier.booked_count += quantity  # a seat still draws from the tier's overall capacity, not tracked separately
    db.commit()
    db.refresh(booking)
    return {
        "status": "ok", "code": booking.code, "tier": tier.name, "quantity": quantity,
        "seat_label": seat.seat_label if seat else None, "price": tier.price, "total_price": tier.price * quantity,
    }


def list_my_bookings(db, email):
    """My Events: every catalog booking (tier_id set) for this email, split
    into Upcoming (event not live/completed yet), Active (event is live),
    and Past (event completed) — mirrors the spec's three tabs directly."""
    bookings = (
        db.query(models.VisitorProfile)
        .filter(models.VisitorProfile.email == email, models.VisitorProfile.tier_id.isnot(None))
        .order_by(models.VisitorProfile.id.desc())
        .all()
    )
    live = get_live_event(db)
    out = {"upcoming": [], "active": [], "past": []}
    for b in bookings:
        tier = db.get(models.EventTier, b.tier_id)
        event = db.get(models.Event, b.event_id) if b.event_id else None
        if not tier or not event:
            continue
        seat = db.get(models.EventSeat, b.seat_id) if b.seat_id else None
        hotel = db.get(models.Zone, b.hotel_zone_id) if b.hotel_zone_id else None
        row = {
            "code": b.code, "event_id": event.id, "event_name": event.name, "event_date": event.event_date,
            "venue_name": event.venue_name, "tier_name": tier.name, "seat_label": seat.seat_label if seat else None,
            "quantity": b.quantity, "checked_in": b.checked_in, "gate_name": tier.gate_name,
            "hotel_name": hotel.name if hotel else None, "wants_transport": b.wants_transport,
        }
        if event.status == "completed":
            out["past"].append(row)
        elif live and live.id == event.id:
            out["active"].append(row)
        else:
            out["upcoming"].append(row)
    return out


def zone_capacity(db, zone_id):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return None
    return {
        "zone": zone.name,
        "capacity": zone.capacity,
        "current_count": zone.current_count,
        "remaining": max(zone.capacity - zone.current_count, 0),
    }


# --- hotel recommendation engine (Section 16) -------------------------------
# Ranks every hotel zone by availability, distance from the venue, and price
# tier — generalizes the single hardcoded recommend_hotel_b action into a
# real ranker over however many hotel zones exist.

def _haversine_km(lat1, lng1, lat2, lng2):
    if None in (lat1, lng1, lat2, lng2):
        return None
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def hotel_recommendations(db):
    hotels = db.query(models.Zone).filter(models.Zone.type == "hotel").all()
    venue = db.query(models.Zone).filter(models.Zone.type == "arena").first()
    out = []
    for h in hotels:
        available_pct = round(max(0, h.capacity - h.current_count) / h.capacity * 100, 1) if h.capacity else 0
        distance_km = _haversine_km(venue.lat, venue.lng, h.lat, h.lng) if venue else None
        score = (
            0.5 * available_pct
            + 0.3 * (100 - clip((distance_km or 0) * 10, 0, 100))
            + 0.2 * (100 - (h.price_tier or 3) * 20)
        )
        reasons = [f"{available_pct}% rooms available"]
        if distance_km is not None:
            reasons.append(f"{round(distance_km, 1)} km from the venue")
        if h.price_tier:
            reasons.append(f"price tier {h.price_tier}/5")
        out.append({
            "zone_id": h.id, "name": h.name, "available_pct": available_pct,
            "distance_km": round(distance_km, 1) if distance_km is not None else None,
            "price_tier": h.price_tier, "score": round(score, 1),
            "reason": ", ".join(reasons),
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    manual = next((h for h in hotels if h.manual_recommended), None)
    for o in out:
        o["recommended"] = (o["zone_id"] == manual.id) if manual else (o is out[0])
    return out


# --- transport demand prediction (Section 14) --------------------------------
# Compares each transport zone's live arrival rate against its remaining
# capacity to flag likely shortfall before it happens, same "predict before
# critical" pattern preventive_alerts already uses for gates.

def transport_demand_prediction(db):
    transport_zones = db.query(models.Zone).filter(models.Zone.domain == "transport").all()
    resources = {r.type: r for r in db.query(models.Resource).all()}
    bus = resources.get("bus")
    out = []
    for z in transport_zones:
        delta = z.current_count - z.last_count
        remaining = max(z.capacity - z.current_count, 0)
        minutes_to_full = round(remaining / delta, 1) if delta > 0 else None
        projected_15min = z.current_count + delta * 15
        shortfall = max(0, projected_15min - z.capacity)
        extra_buses_needed = math.ceil(shortfall / BUS_CAPACITY_EACH) if shortfall > 0 else 0
        out.append({
            "zone_id": z.id, "zone_name": z.name,
            "current_pct": round(z.current_count / z.capacity * 100, 1) if z.capacity else 0,
            "arrival_rate_per_tick": delta,
            "minutes_to_full": minutes_to_full,
            "projected_15min_pct": round(projected_15min / z.capacity * 100, 1) if z.capacity else 0,
            "extra_buses_needed": extra_buses_needed,
            "buses_available": bus.quantity_available if bus else 0,
            "recommendation": (
                f"Additional transport may be required at {z.name} within the next 15 minutes "
                f"— dispatch ~{extra_buses_needed} more bus(es)."
                if extra_buses_needed > 0 else f"{z.name} transport capacity looks sufficient for now."
            ),
        })
    out.sort(key=lambda x: x["extra_buses_needed"], reverse=True)
    return out


# --- dynamic staff allocation (Section 13), generalized ---------------------
# Not tied to Gate 2/Gate 3 — looks at every venue zone's live risk and
# suggests moving staff from whichever zone is quietest to whichever is
# hottest, same "surplus -> deficit" logic the spec describes for any event.

STAFF_MOVE_UNIT = 2  # staff moved per suggestion, matches the existing move_staff action's granularity


def staff_allocation_suggestions(db):
    zones = db.query(models.Zone).filter(models.Zone.type.in_(["gate", "arena", "vip"])).all()
    if len(zones) < 2:
        return []
    scored = [(z, zone_risk(z, db)) for z in zones]
    hottest = max(scored, key=lambda zr: zr[1]["score"])
    quietest = min(scored, key=lambda zr: zr[1]["score"])
    if hottest[0].id == quietest[0].id or hottest[1]["level"] not in ("HIGH", "CRITICAL"):
        return []
    return [{
        "from_zone": quietest[0].name, "from_score": quietest[1]["score"],
        "to_zone": hottest[0].name, "to_score": hottest[1]["score"],
        "staff_to_move": STAFF_MOVE_UNIT,
        "reason": (
            f"{hottest[0].name} is {hottest[1]['level']} ({hottest[1]['score']}) while "
            f"{quietest[0].name} is comparatively idle ({quietest[1]['score']}) — moving "
            f"{STAFF_MOVE_UNIT} staff there can speed up processing without under-staffing {quietest[0].name}."
        ),
    }]


# --- emergency mode (Section 28) --------------------------------------------

def trigger_emergency(db, zone_id, message):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return None
    state = get_state_row(db)
    if not state:
        return None
    state.emergency_active = True
    state.emergency_zone_id = zone_id
    state.emergency_message = message
    db.commit()

    event = get_live_event(db)
    alert = models.Alert(
        event_id=event.id if event else None, zone_id=zone_id, alert_type="emergency",
        severity="CRITICAL", impact_score=100, message=f"EMERGENCY at {zone.name}: {message}",
    )
    db.add(alert)
    db.flush()
    for role in ("Event Command Operator", "Venue Manager", "Transport Operator", "Hospitality Operator", "Administrator"):
        db.add(models.Notification(
            event_id=alert.event_id, alert_id=alert.id, audience_role=role, zone_domain=None,
            title=f"EMERGENCY — {zone.name}", message=message, priority="CRITICAL",
        ))
    db.add(models.Notification(
        event_id=alert.event_id, alert_id=alert.id, audience_role="Attendee", zone_domain=zone.domain,
        title="Emergency in progress", message=f"An emergency has been reported near {zone.name}. Please follow staff instructions and avoid the area.",
        priority="CRITICAL",
    ))
    db.commit()
    _log(db, state.tick, "emergency", f"EMERGENCY declared at {zone.name}: {message}", zone_domain=zone.domain)
    return {"zone": zone.name, "message": message, "alert_id": alert.id}


def clear_emergency(db):
    state = get_state_row(db)
    if not state:
        return None
    zone = db.get(models.Zone, state.emergency_zone_id) if state.emergency_zone_id else None
    if zone:
        _resolve_alerts_for_zone(db, zone.id)
        _log(db, state.tick, "emergency_cleared", f"Emergency at {zone.name} cleared.", zone_domain=zone.domain)
    state.emergency_active = False
    state.emergency_zone_id = None
    state.emergency_message = None
    db.commit()
    return {"cleared": True}


def emergency_status(db):
    state = get_state_row(db)
    if not state or not state.emergency_active:
        return {"active": False}
    zone = db.get(models.Zone, state.emergency_zone_id) if state.emergency_zone_id else None
    return {
        "active": True, "zone_id": state.emergency_zone_id,
        "zone_name": zone.name if zone else None, "message": state.emergency_message,
    }


# --- post-event analytics / historical learning (Sections 31-32) ----------

def event_analytics(db):
    event = get_live_event(db)
    zones = db.query(models.Zone).all()
    visitors = db.query(models.VisitorProfile).all()
    checked_in = [v for v in visitors if v.checked_in]

    peak_overall = max((z.peak_count / z.capacity * 100 if z.capacity else 0) for z in zones) if zones else 0
    most_problematic = max(zones, key=lambda z: (z.peak_count / z.capacity if z.capacity else 0)) if zones else None

    all_alerts = list_alerts(db)
    critical_incidents = sum(1 for a in all_alerts if a["severity"] == "CRITICAL")
    resolved_incidents = sum(1 for a in all_alerts if a["status"] == "resolved")

    action_log = recent_log(db, category="action_executed", limit=200)
    tally = {}
    for entry in action_log:
        label = entry["message"].split(" — ")[0]
        tally[label] = tally.get(label, 0) + 1
    most_effective_action = max(tally, key=tally.get) if tally else None

    transport_zones = [z for z in zones if z.domain == "transport"]
    hospitality_zones = [z for z in zones if z.domain == "hospitality"]
    transport_util = round(sum(z.current_count / z.capacity * 100 for z in transport_zones if z.capacity) / len(transport_zones), 1) if transport_zones else 0
    hotel_util = round(sum(z.current_count / z.capacity * 100 for z in hospitality_zones if z.capacity) / len(hospitality_zones), 1) if hospitality_zones else 0

    return {
        "event_name": event.name if event else None,
        "total_registered": len(visitors),
        "total_checked_in": len(checked_in),
        "peak_occupancy_pct": round(peak_overall, 1),
        "most_problematic_zone": most_problematic.name if most_problematic else None,
        "critical_incidents": critical_incidents,
        "resolved_incidents": resolved_incidents,
        "most_effective_action": most_effective_action,
        "transport_utilization_pct": transport_util,
        "hotel_utilization_pct": hotel_util,
        "event_health_score": event_health_score(db)["overall"],
        "zone_peaks": [
            {"zone_name": z.name, "domain": z.domain, "peak_count": z.peak_count, "capacity": z.capacity,
             "peak_pct": round(z.peak_count / z.capacity * 100, 1) if z.capacity else 0, "peak_tick": z.peak_tick}
            for z in sorted(zones, key=lambda z: (z.peak_count / z.capacity if z.capacity else 0), reverse=True)
        ],
    }


# --- map: add/move/remove gates from the What-If Simulator's map -----------
# Gate 2, Corridor B and Hotel A are protected from deletion — the canonical
# SRS-Appendix scenario chain (and run_whatif's hardcoded Gate 2/Gate 3 logic)
# depends on those exact zones existing. Everything else can be freely added,
# repositioned, or removed — this is deliberately just map metadata (lat/lng)
# plus the same Zone row shape already used everywhere else, not a parallel
# "gates" system.
PROTECTED_ZONE_NAMES = {"Gate 2", "Gate 3", "Corridor B", "Hotel A", "Main Hall"}


def create_zone(db, name, lat, lng, capacity, domain="venue", type_="gate"):
    event = get_live_event(db)
    if not event:
        return None
    zone = models.Zone(
        event_id=event.id, name=name, type=type_, domain=domain,
        lat=lat, lng=lng, capacity=capacity, current_count=0, last_count=0,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


def update_zone_location(db, zone_id, lat, lng):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return None
    zone.lat = lat
    zone.lng = lng
    db.commit()
    return zone


def delete_zone(db, zone_id):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return {"ok": False, "error": "zone not found"}
    if zone.name in PROTECTED_ZONE_NAMES:
        return {"ok": False, "error": f"{zone.name} can't be deleted — the built-in scenario depends on it."}
    # a gate pointing visitors here shouldn't be left dangling
    for gate in db.query(models.Zone).filter(models.Zone.linked_transport_zone_id == zone_id).all():
        gate.linked_transport_zone_id = None
    for gate in db.query(models.Zone).filter(models.Zone.linked_hospitality_zone_id == zone_id).all():
        gate.linked_hospitality_zone_id = None
    db.delete(zone)
    db.commit()
    return {"ok": True}


# --- Event Setup Form (Event Command Operator): gates, hotels, transport ---
# One consolidated view over the live event's zones, grouped by domain, with
# every field an operator would actually configure — not just capacity like
# the plain admin zone list. Hotels/transport zones created here are regular
# Zone rows (same table everything else already uses), just with the fuller
# field set (staff, price, distance, contact, amenities, manual override)
# actually filled in through this form instead of left null.

def event_setup_summary(db):
    event = get_live_event(db)
    if not event:
        return {"configured": False}
    zones = db.query(models.Zone).filter(models.Zone.event_id == event.id).all()
    venue = next((z for z in zones if z.type == "arena"), None)

    gates = [
        {
            "id": z.id, "name": z.name, "capacity": z.capacity, "current_count": z.current_count,
            "staff_assigned": z.staff_assigned,
        }
        for z in zones if z.type == "gate"
    ]

    hotels_ranked = {h["zone_id"]: h for h in hotel_recommendations(db)}
    hotels = []
    for z in zones:
        if z.type != "hotel":
            continue
        ranked = hotels_ranked.get(z.id, {})
        hotels.append({
            "id": z.id, "name": z.name, "total_rooms": z.capacity, "occupied_rooms": z.current_count,
            "available_rooms": max(z.capacity - z.current_count, 0),
            "distance_km": ranked.get("distance_km"), "price_tier": z.price_tier,
            "contact": z.contact, "amenities": z.amenities,
            "manual_recommended": z.manual_recommended, "recommended": ranked.get("recommended", False),
            "lat": z.lat, "lng": z.lng,
        })

    transport = [
        {
            "id": z.id, "name": z.name, "type": z.type, "capacity": z.capacity, "current_count": z.current_count,
            "occupied_pct": round(z.current_count / z.capacity * 100, 1) if z.capacity else 0,
            "contact": z.contact, "lat": z.lat, "lng": z.lng,
        }
        for z in zones if z.domain == "transport"
    ]

    bus = db.query(models.Resource).filter(models.Resource.type == "bus").first()

    return {
        "configured": True, "event_name": event.name,
        "venue": {"id": venue.id, "name": venue.name, "lat": venue.lat, "lng": venue.lng} if venue else None,
        "gates": gates, "hotels": hotels, "transport": transport,
        "buses": {"total": bus.quantity_total, "available": bus.quantity_available} if bus else None,
    }


def update_gate_setup(db, zone_id, capacity, staff_assigned):
    zone = db.get(models.Zone, zone_id)
    if not zone or zone.type != "gate":
        return None
    zone.capacity = capacity
    zone.staff_assigned = staff_assigned
    db.commit()
    return zone


def create_hotel(db, name, capacity, lat, lng, price_tier=None, contact=None, amenities=None):
    event = get_live_event(db)
    if not event:
        return None
    zone = models.Zone(
        event_id=event.id, name=name, type="hotel", domain="hospitality", lat=lat, lng=lng,
        capacity=capacity, current_count=0, last_count=0, price_tier=price_tier, contact=contact, amenities=amenities,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


def update_hotel(db, zone_id, capacity=None, occupied_rooms=None, price_tier=None, contact=None, amenities=None, manual_recommended=None):
    zone = db.get(models.Zone, zone_id)
    if not zone or zone.type != "hotel":
        return None
    if capacity is not None:
        zone.capacity = capacity
    if occupied_rooms is not None:
        zone.current_count = occupied_rooms
    if price_tier is not None:
        zone.price_tier = price_tier
    if contact is not None:
        zone.contact = contact
    if amenities is not None:
        zone.amenities = amenities
    if manual_recommended is not None:
        if manual_recommended:
            # only one hotel can be the manual pick at a time
            for other in db.query(models.Zone).filter(models.Zone.type == "hotel", models.Zone.id != zone_id).all():
                other.manual_recommended = False
        zone.manual_recommended = manual_recommended
    db.commit()
    return zone


def create_transport_zone(db, name, capacity, lat, lng, type_="corridor", contact=None):
    event = get_live_event(db)
    if not event:
        return None
    zone = models.Zone(
        event_id=event.id, name=name, type=type_, domain="transport", lat=lat, lng=lng,
        capacity=capacity, current_count=0, last_count=0, contact=contact,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


def update_transport_zone(db, zone_id, capacity=None, current_count=None, contact=None):
    zone = db.get(models.Zone, zone_id)
    if not zone or zone.domain != "transport":
        return None
    if capacity is not None:
        zone.capacity = capacity
    if current_count is not None:
        zone.current_count = current_count
    if contact is not None:
        zone.contact = contact
    db.commit()
    return zone


# --- Event lifecycle (Administrator: create / edit / delete) ---------------
# One live event at a time (same architecture as before) — but the
# Administrator can now actually wipe it and start a fresh one, instead of
# only ever being able to edit the single event seeding created.

def create_event(db, name, region, expected_attendance, safe_capacity):
    """Replaces only the current live event — any other catalog events
    (upcoming/completed) are untouched."""
    old_live = get_live_event(db)
    if old_live:
        db.query(models.Zone).filter(models.Zone.event_id == old_live.id).delete()
        db.query(models.VisitorProfile).filter(
            (models.VisitorProfile.event_id == old_live.id) | (models.VisitorProfile.event_id.is_(None))
        ).delete(synchronize_session=False)
        db.query(models.Event).filter(models.Event.id == old_live.id).delete()
    db.query(models.Resource).delete()
    db.query(models.LogEntry).delete()
    db.commit()

    event = models.Event(
        name=name, region=region,
        expected_attendance=expected_attendance, safe_capacity=safe_capacity,
        status="live",
    )
    db.add(event)
    db.flush()

    from .seed import create_zones_and_resources
    create_zones_and_resources(db, event)

    state = get_state_row(db)
    if state:
        state.tick = 0
        state.scenario_active = False
        state.active_scenario_id = None
        state.trigger_tick = -1
    else:
        db.add(models.SimState(tick=0, scenario_active=False, trigger_tick=-1))
    db.commit()

    apply_region(db, region, rename=False)
    return event


def delete_event(db):
    """Wipes only the live event — the app falls back to its already-built
    'no event configured yet' state (login/attendee/command-center all
    handle {"configured": false} gracefully) until a new one goes live.
    Scenarios, user accounts, and other catalog events are untouched."""
    live = get_live_event(db)
    if live:
        db.query(models.Zone).filter(models.Zone.event_id == live.id).delete()
        db.query(models.VisitorProfile).filter(
            (models.VisitorProfile.event_id == live.id) | (models.VisitorProfile.event_id.is_(None))
        ).delete(synchronize_session=False)
        db.query(models.Event).filter(models.Event.id == live.id).delete()
    db.query(models.Resource).delete()
    db.query(models.SimState).delete()
    db.query(models.LogEntry).delete()
    db.commit()


# --- India region grounding (Administrator: Event Configuration) -----------
# Re-anchors the fixed 10-zone template to a real venue/transport hub/airport/
# hotel for whichever Indian state/UT is selected — one real-world skin over
# the same structure and risk engine, not a separate simulation per state.

def apply_region(db, state_name, rename=True):
    region = INDIA_REGIONS.get(state_name)
    if not region:
        return None

    event = get_live_event(db)
    city = region["city"]
    venue = region["venue"] or "the venue"

    # rename=False when called right after create_event() — the Administrator
    # already chose a name there, region-grounding shouldn't silently replace it.
    if rename:
        event.name = f"{city} Mega Fest"
    event.region = state_name
    db.flush()

    zones = {z.name: z for z in db.query(models.Zone).all()}

    # venue strings already carry their own locality (e.g. "D Y Patil Stadium,
    # Nerul"), so appending ", {city}" again produced redundant/awkward text
    # like "D Y Patil Stadium, Nerul Gate 2, Navi Mumbai / Panvel" — an em-dash
    # separator instead of a second city clause reads cleanly for every region.
    if "Main Hall" in zones:
        mh = zones["Main Hall"]
        mh.location_note = venue if region["venue"] else f"Local venue, {city} (not independently confirmed)"
        if region["venue_capacity"]:
            ratio = mh.current_count / mh.capacity if mh.capacity else 0
            mh.capacity = region["venue_capacity"]
            mh.current_count = round(mh.capacity * ratio)
            mh.last_count = mh.current_count
    if "VIP Zone" in zones:
        zones["VIP Zone"].location_note = f"{venue} — VIP Stand"
    for gate_name in ("Gate 1", "Gate 2", "Gate 3"):
        if gate_name in zones:
            zones[gate_name].location_note = f"{venue} — {gate_name}"
    if "Corridor A" in zones:
        zones["Corridor A"].location_note = f"Route to {region['airport']}"
    if "Corridor B" in zones:
        hub_note = region["transport_hub"] or f"{city}'s transport hub"
        zones["Corridor B"].location_note = f"Route to {hub_note}"
    if "Transport Hub" in zones:
        zones["Transport Hub"].location_note = (
            region["transport_hub"] if region["transport_hub"] else f"{city} transport hub (not independently confirmed)"
        )
    if "Hotel A" in zones:
        zones["Hotel A"].location_note = region["hotel"] or f"A hotel in {city} (not independently confirmed)"
    if "Hotel B" in zones:
        zones["Hotel B"].location_note = (
            f"{region['hotel']} — second property, {city} (only one hotel independently verified for this region)"
            if region["hotel"] else f"A second hotel in {city} (not independently confirmed)"
        )

    db.commit()
    return {"region": state_name, "event_name": event.name}


def gate_advisory(db):
    """Visitor Advisory: gate-by-gate status plus an alternate-gate suggestion
    if the fullest gate is under real pressure — always live-computed, not
    cached, so it reflects whatever the operators/simulator just did."""
    gates = db.query(models.Zone).filter(models.Zone.type == "gate").all()
    out = []
    for g in gates:
        r = zone_risk(g, db)
        out.append({
            "id": g.id, "name": g.name,
            "current_count": g.current_count, "capacity": g.capacity,
            "remaining": max(g.capacity - g.current_count, 0),
            **r,
        })
    out.sort(key=lambda z: z["capacity_pressure_pct"], reverse=True)
    suggestion = None
    if out and out[0]["level"] in ("HIGH", "CRITICAL") and len(out) > 1:
        quieter = min(out[1:], key=lambda z: z["capacity_pressure_pct"])
        suggestion = {
            "crowded_gate": out[0]["name"],
            "crowded_pct": out[0]["capacity_pressure_pct"],
            "suggested_gate": quieter["name"],
            "suggested_pct": quieter["capacity_pressure_pct"],
        }
    return {"gates": out, "suggestion": suggestion}


# --- AI layer: our own narrator, not a third-party LLM ---------------------
# This is a deterministic, rule-based text generator we built ourselves — it
# never calls an external API and never can invent a number, because every
# sentence is assembled directly from the structured fields already computed
# above (risk_factors/zone_risk/gate_advisory). "Grounded in" always names the
# exact zone(s)/state the sentence was built from.

URGENCY_TAG = {"CRITICAL": "URGENT", "HIGH": "WATCH", "MODERATE": "MONITOR", "LOW": "OK"}


def _dominant_factor(r):
    """Which weighted term is actually driving this zone's score — narrating
    around that, rather than always leading with capacity, is what makes the
    explanation feel specific to the zone instead of a generic template."""
    weighted = {
        "capacity_pressure": W_CAPACITY * r["capacity_pressure_pct"],
        "arrival_surge": W_SURGE * r["arrival_surge"],
        "flow_instability": W_INSTABILITY * r["flow_instability"],
        "resource_pressure": W_RESOURCE * r["resource_pressure"],
        "time_to_criticality": W_TIME * r["time_to_criticality"],
    }
    return max(weighted, key=weighted.get)


def _factor_sentence(zone, r, dominant):
    if dominant == "capacity_pressure":
        return (f"{zone.name} is at {r['capacity_pressure_pct']}% of its {zone.capacity:,}-person capacity — "
                f"sheer occupancy ({zone.current_count:,} people) is the main driver right now.")
    if dominant == "arrival_surge":
        return (f"{zone.name}'s risk is being pushed up mainly by how fast people are arriving right now — "
                f"inflow is spiking well above its normal pace.")
    if dominant == "flow_instability":
        return (f"{zone.name}'s flow just shifted sharply compared to the previous reading — that instability "
                f"is often an early warning sign, even before occupancy itself looks critical.")
    if dominant == "resource_pressure":
        return (f"{zone.name}'s risk is elevated by pressure on what it feeds into — the linked zone it "
                f"depends on is itself under heavy load ({r['resource_pressure']}%).")
    if r["time_to_capacity_min"] is not None:
        return (f"At the current rate, {zone.name} is projected to reach capacity in about "
                f"{r['time_to_capacity_min']} minutes if nothing changes.")
    return f"{zone.name} is currently stable — no single factor is driving its score up."


def explain_zone(db, zone_id):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return {"text": None, "grounded_in": [], "error": "zone not found"}
    r = zone_risk(zone, db)
    dominant = _dominant_factor(r)
    sentence = _factor_sentence(zone, r, dominant)
    text = f"{sentence} Current score: {r['score']} ({r['level']})."
    return {"text": text, "grounded_in": [f"zone:{zone.name}"], "error": None}


def ai_advisor(db):
    """Event-wide 'what should I do right now' — top zones by score, one
    urgency-tagged line each, built the same way as explain_zone."""
    state = full_state(db)
    top = [z for z in state["zones"] if z["level"] != "LOW"][:5] or state["zones"][:3]
    lines = []
    for z in top:
        zone = db.get(models.Zone, z["id"])
        r = zone_risk(zone, db)
        dominant = _dominant_factor(r)
        lines.append(f"{URGENCY_TAG[z['level']]}: {_factor_sentence(zone, r, dominant)}")
    text = "\n".join(lines) if lines else "All zones are currently LOW risk — nothing needs attention right now."
    grounded = [f"zone:{z['name']}" for z in top] or ["state:all_zones"]
    return {"text": text, "grounded_in": grounded, "error": None}


def attendee_advisory(db):
    advisory = gate_advisory(db)
    if not advisory["suggestion"]:
        return {"text": None, "grounded_in": ["state:gates"], "error": None}
    s = advisory["suggestion"]
    text = (f"{s['crowded_gate']} is filling up ({s['crowded_pct']}%) — {s['suggested_gate']} is quieter "
            f"({s['suggested_pct']}%) and just as good a way in right now.")
    return {"text": text, "grounded_in": ["state:gates"], "error": None}


# --- Chatbot: keyword-routed Q&A over our own grounded data ----------------
# Not a third-party/paid LLM call — a small intent router over the exact same
# structured fields explain_zone/ai_advisor already use, so it carries the
# same "never invent a number" guarantee while answering free-form questions.

def _fuzzy_word_match(q, keywords, cutoff=0.78):
    """Tolerates spelling/typo mistakes (SRS Section 6) — e.g. 'evnt'/'pepole'
    still route to the right intent even though the exact substring check
    above wouldn't catch them."""
    words = [w.strip(".,!?") for w in q.split()]
    for w in words:
        if len(w) < 3:
            continue
        if difflib.get_close_matches(w, keywords, n=1, cutoff=cutoff):
            return True
    return False


def _word_close(a, b, cutoff=0.6):
    # Requiring similar length alongside the ratio stops a totally different
    # second word ("near", "evnt") from fuzzy-matching a short zone-name
    # suffix ("a", "2") just because a SequenceMatcher ratio over the whole
    # phrase would otherwise be inflated by the first word matching exactly.
    if abs(len(a) - len(b)) > 2:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= cutoff


def _fuzzy_zone_match(q, zones):
    """Tolerates a misspelled/partial gate or hotel name ('gat 2', 'main hal')
    — every zone name here is exactly two words, so this checks each
    consecutive word pair in the question against both words of each zone
    name independently (not the phrase as a whole — see _word_close)."""
    tokens = [t.strip(".,!?") for t in q.split()]
    for zone in zones:
        zwords = zone.name.lower().split()
        if len(zwords) != 2:
            continue
        for i in range(len(tokens) - 1):
            if _word_close(tokens[i], zwords[0]) and _word_close(tokens[i + 1], zwords[1]):
                return zone
    return None


def chatbot_answer(db, question):
    q = (question or "").strip().lower()
    if not q:
        return {"text": "Ask me about a gate, a hotel, or a corridor by name, or try \"what should I do\" or \"what happened\".", "grounded_in": []}

    zones = db.query(models.Zone).all()
    for zone in zones:
        if zone.name.lower() in q:
            r = zone_risk(zone, db)
            dominant = _dominant_factor(r)
            sentence = _factor_sentence(zone, r, dominant)
            return {"text": f"{sentence} Current score: {r['score']} ({r['level']}).", "grounded_in": [f"zone:{zone.name}"]}

    fuzzy_zone = _fuzzy_zone_match(q, zones)
    if fuzzy_zone:
        r = zone_risk(fuzzy_zone, db)
        dominant = _dominant_factor(r)
        sentence = _factor_sentence(fuzzy_zone, r, dominant)
        return {"text": f"{sentence} Current score: {r['score']} ({r['level']}).", "grounded_in": [f"zone:{fuzzy_zone.name}"]}

    if any(k in q for k in ("where is", "venue", "located", "location", "my event")) or \
            _fuzzy_word_match(q, ["where", "venue", "located", "location"]):
        event = get_live_event(db)
        main_hall = db.query(models.Zone).filter(models.Zone.type == "arena").first()
        if event:
            text = (
                f"{event.name} is being held at {main_hall.location_note or main_hall.name}."
                if main_hall else f"{event.name} is the current event."
            )
            return {"text": text, "grounded_in": ["state:event"]}

    if any(k in q for k in ("what should i do", "recommend", "action", "plan")) or _fuzzy_word_match(q, ["recommend", "action", "plan"]):
        recs = generate_recommendations(db)
        if not recs:
            return {"text": "No feasible actions right now — every candidate action is blocked by a resource or capacity constraint.", "grounded_in": ["state:recommendations"]}
        top = recs[0]
        return {
            "text": (f"Top recommendation: {top['label']} (score {top['action_score']}, targets "
                     f"{', '.join(top['target_zones'])}, expected risk reduction +{top['risk_reduction']})."),
            "grounded_in": [f"action:{top['id']}"],
        }

    if any(k in q for k in ("hotel", "occupancy", "room", "accommodation")) or _fuzzy_word_match(q, ["hotel", "occupancy", "room", "accommodation", "event"]):
        hotels = [z for z in zones if z.type == "hotel"]
        if not hotels:
            return {"text": "No hotel zones are configured for this event.", "grounded_in": []}
        lines = [f"{h.name}: {round(h.current_count / h.capacity * 100, 1)}% occupied ({h.current_count}/{h.capacity})" for h in hotels]
        return {"text": " · ".join(lines), "grounded_in": [f"zone:{h.name}" for h in hotels]}

    if any(k in q for k in ("long distance", "express train", "outstation train", "konkan")):
        local = local_transit_feed(db)
        if not local["available"]:
            return {"text": local["note"], "grounded_in": []}
        lines = [f"{t['train']} → {t['destination']} ({t['via']}) in {t['arrives_in_min']} min" for t in local["trains"]["long_distance"]]
        return {"text": "Next long-distance trains: " + " · ".join(lines), "grounded_in": ["state:local_transit"]}

    if any(k in q for k in ("train", "railway", "harbour line", "local train")):
        local = local_transit_feed(db)
        if not local["available"]:
            return {"text": local["note"], "grounded_in": []}
        lines = [f"{t['line']} → {t['destination']} in {t['arrives_in_min']} min" for t in local["trains"]["suburban"]]
        return {"text": "Next local trains: " + " · ".join(lines), "grounded_in": ["state:local_transit"]}

    if any(k in q for k in ("village", "msrtc", "outstation bus", "state transport", "st bus")):
        local = local_transit_feed(db)
        if not local["available"]:
            return {"text": local["note"], "grounded_in": []}
        lines = [f"{b['route']} ({b['description']}) in {b['arrives_in_min']} min" for b in local["buses"]["village"]]
        return {"text": "Next MSRTC village/outstation buses: " + " · ".join(lines), "grounded_in": ["state:local_transit"]}

    if any(k in q for k in ("bus", "transport", "corridor")) or _fuzzy_word_match(q, ["bus", "transport", "corridor"]):
        corridors = [z for z in zones if z.type == "corridor"]
        resources = {r.type: r for r in db.query(models.Resource).all()}
        bus = resources.get("bus")
        bus_line = f"Buses available: {bus.quantity_available}/{bus.quantity_total}. " if bus else ""
        local = local_transit_feed(db)
        if local["available"] and local["buses"]["city"]:
            next_bus = local["buses"]["city"][0]
            bus_line += f"Next NMMT bus: Route {next_bus['route']} in {next_bus['arrives_in_min']} min. "
        lines = [f"{c.name}: {round(c.current_count / c.capacity * 100, 1)}% loaded" for c in corridors]
        grounded = [f"zone:{c.name}" for c in corridors] + (["resource:bus"] if bus else [])
        return {"text": bus_line + " · ".join(lines), "grounded_in": grounded}

    if any(k in q for k in ("escalat", "no feasible", "stuck", "human")):
        esc = escalations(db)
        if not esc:
            return {"text": "No zone is currently stuck without a feasible action.", "grounded_in": ["state:escalations"]}
        return {
            "text": "Needs a human call: " + ", ".join(f"{e['zone_name']} ({e['level']})" for e in esc),
            "grounded_in": [f"zone:{e['zone_name']}" for e in esc],
        }

    if any(k in q for k in ("happen", "timeline", "history", "log")):
        log = recent_log(db, limit=5)
        if not log:
            return {"text": "Nothing has happened yet — trigger a scenario to see activity build up.", "grounded_in": ["state:timeline"]}
        return {"text": " | ".join(f"[{e['clock']}] {e['message']}" for e in log), "grounded_in": ["state:timeline"]}

    if any(k in q for k in ("off-peak", "off peak", "best time", "when should i", "when to arrive", "avoid crowd", "avoid rush")) or \
            _fuzzy_word_match(q, ["offpeak", "arrive", "rush"]):
        event = get_live_event(db)
        if not event:
            return {"text": "No live event right now to base timing advice on.", "grounded_in": []}
        recs = offpeak_recommendations(db)
        if not recs:
            return {"text": "No gates configured to give timing advice on.", "grounded_in": []}
        best = min(recs, key=lambda r: {"NORMAL": 0, "MODERATE": 1, "HIGH": 2, "CRITICAL": 3}[r["current_level"]])
        return {"text": best["recommendation"], "grounded_in": [f"zone:{best['zone_name']}"]}

    if any(k in q for k in ("gate", "queue", "wait", "entry")) or _fuzzy_word_match(q, ["gate", "queue", "wait", "entry", "people"]):
        advisory = gate_advisory(db)
        text = " · ".join(f"{g['name']}: {g['capacity_pressure_pct']}% ({g['level']})" for g in advisory["gates"])
        if advisory["suggestion"]:
            s = advisory["suggestion"]
            text += f". {s['crowded_gate']} is busier — try {s['suggested_gate']} instead."
        return {"text": text, "grounded_in": [f"zone:{g['name']}" for g in advisory["gates"]]}

    # Fallback: the same event-wide summary the AI Advisor button gives.
    advisor = ai_advisor(db)
    return {
        "text": advisor["text"] + "\n\n(Try asking about a specific gate/hotel/corridor by name, or \"what should I do\".)",
        "grounded_in": advisor["grounded_in"],
    }


# --- Transport Hub: illustrative arrivals feeds (region-gated, DB-backed) --
# Panvel Railway Station (our Transport Hub zone) really does interconnect to
# Navi Mumbai International Airport (opened 25 Dec 2025, IndiGo/Air India
# Express/Akasa Air), sits on the real Central Railway Mumbai Suburban
# network as a terminus of both the Harbour Line (to CSMT via Vashi/Nerul)
# and the Trans-Harbour Line (to Thane), and is served locally by NMMT
# (Navi Mumbai Municipal Transport) buses — verified via web search, not
# invented. None of these are live feeds, so every schedule below is
# illustrative and deterministic (seeded by tick, not random each call),
# same honest synthetic-vs-real framing as the rest of the crowd data.
#
# The actual route rows live in models.TransitRoute (seeded once in seed.py)
# rather than as Python constants — same content, just queryable/editable as
# real data instead of code. Only Maharashtra/Navi Mumbai has been researched
# to this level of detail — every other region gets an explicit "not
# available" note instead of a guessed schedule for a station not looked up.

def _region_gated(db):
    event = get_live_event(db)
    return bool(event and event.region == "Maharashtra")


def _routes_for(db, region, mode):
    return db.query(models.TransitRoute).filter(
        models.TransitRoute.region == region, models.TransitRoute.mode == mode,
    ).order_by(models.TransitRoute.id).all()


def transport_hub_arrivals(db):
    if not _region_gated(db):
        return {"available": False, "arrivals": [],
                "note": "No independently verified flight schedule for this region's airport yet."}
    state = get_state_row(db)
    routes = _routes_for(db, "Maharashtra", "flight")
    rnd = random.Random(state.tick)  # deterministic per tick, not per request
    picks = rnd.sample(routes, k=min(4, len(routes)))
    arrivals = [
        {"airline": r.name, "origin": r.destination, "arrives_in_min": r.base_arrival_min + i * 12}
        for i, r in enumerate(picks)
    ]
    return {
        "available": True,
        "note": "Illustrative schedule, not live tracking — reflects NMIA's real Dec-2025 launch "
                "route network (IndiGo, Air India Express, Akasa Air).",
        "arrivals": arrivals,
    }


def local_transit_feed(db):
    if not _region_gated(db):
        return {
            "available": False,
            "trains": {"suburban": [], "long_distance": []},
            "buses": {"city": [], "village": []},
            "note": "No independently verified local train/bus schedule for this region yet — "
                    "only Maharashtra/Navi Mumbai (Panvel) has been researched in detail.",
        }
    state = get_state_row(db)
    rnd = random.Random((state.tick if state else 0) + 1)  # offset from the flight feed's seed so picks differ

    suburban_routes = _routes_for(db, "Maharashtra", "suburban_train")
    suburban_picks = rnd.sample(suburban_routes, k=len(suburban_routes))
    suburban = [
        {"line": r.name, "destination": r.destination, "via": r.description, "arrives_in_min": r.base_arrival_min + i * 6}
        for i, r in enumerate(suburban_picks)
    ]
    long_distance_routes = _routes_for(db, "Maharashtra", "long_distance_train")
    long_distance_picks = rnd.sample(long_distance_routes, k=len(long_distance_routes))
    long_distance = [
        {"train": r.name, "destination": r.destination, "via": r.description, "arrives_in_min": r.base_arrival_min + i * 25}
        for i, r in enumerate(long_distance_picks)
    ]

    city_routes = _routes_for(db, "Maharashtra", "city_bus")
    city_picks = rnd.sample(city_routes, k=len(city_routes))
    city_buses = [
        {"route": r.name, "description": r.description, "arrives_in_min": r.base_arrival_min + i * 5}
        for i, r in enumerate(city_picks)
    ]
    village_routes = _routes_for(db, "Maharashtra", "village_bus")
    village_picks = rnd.sample(village_routes, k=len(village_routes))
    village_buses = [
        {"route": r.name, "description": r.description, "arrives_in_min": r.base_arrival_min + i * 15}
        for i, r in enumerate(village_picks)
    ]

    return {
        "available": True,
        "note": "Illustrative schedule, not live tracking — reflects Panvel's real Central Railway "
                "Harbour/Trans-Harbour Line and Konkan Railway/mainline services, NMMT's real city "
                "route network, and MSRTC's real outstation routes from Panvel ST Depot into Raigad "
                "district's villages and towns.",
        "trains": {"suburban": suburban, "long_distance": long_distance},
        "buses": {"city": city_buses, "village": village_buses},
    }
