import random
import uuid

from sqlalchemy.orm import Session

from . import models

# --- risk engine (SRS Section 8.2) ------------------------------------

W_CAPACITY = 0.35
W_SURGE = 0.25
W_INSTABILITY = 0.15
W_RESOURCE = 0.15
W_TIME = 0.10

GATE2_RAMP_PER_TICK = 273
CORRIDOR_B_RAMP_PER_TICK = 76
HOTEL_A_RAMP_PER_TICK = 9
RAMP_DURATION_TICKS = 11

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

def get_state_row(db):
    state = db.query(models.SimState).first()
    return state


def trigger_scenario(db):
    state = get_state_row(db)
    state.scenario_active = True
    state.trigger_tick = state.tick
    db.commit()
    return state


def advance_tick(db):
    state = get_state_row(db)
    state.tick += 1
    ticks_since_trigger = state.tick - state.trigger_tick if state.scenario_active else -1
    ramping = state.scenario_active and 0 <= ticks_since_trigger < RAMP_DURATION_TICKS

    zones = db.query(models.Zone).all()
    for zone in zones:
        delta = 0
        if ramping and zone.name == "Gate 2":
            delta = GATE2_RAMP_PER_TICK
        elif ramping and zone.name == "Corridor B":
            delta = CORRIDOR_B_RAMP_PER_TICK
        elif ramping and zone.name == "Hotel A":
            delta = HOTEL_A_RAMP_PER_TICK
        new_count = zone.current_count + delta
        if zone.name == "Hotel A":
            new_count = min(zone.capacity, new_count)  # a hotel can't hold more guests than it has rooms
        # Corridor B is deliberately allowed past 100% — transport *demand* can
        # exceed capacity (that's congestion), unlike a hotel's physical rooms.
        new_count = max(0, new_count)
        zone.prev_delta = zone.current_count - zone.last_count
        zone.last_count = zone.current_count
        zone.current_count = new_count

    db.commit()
    return state


def reset_simulation(db):
    db.query(models.Event).delete()
    db.query(models.Zone).delete()
    db.query(models.Resource).delete()
    db.query(models.VisitorProfile).delete()
    db.query(models.SimState).delete()
    db.commit()
    from .seed import seed_if_empty
    seed_if_empty(db)


# --- acknowledge / escalate ----------------------------------------------

def set_ack_status(db, zone_id, status):
    if status not in ("open", "acknowledged", "escalated"):
        return None
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return None
    zone.ack_status = status
    db.commit()
    return zone


# --- consolidated state ---------------------------------------------------

def full_state(db):
    zones = db.query(models.Zone).all()
    resources = db.query(models.Resource).all()
    state = get_state_row(db)

    zone_out = []
    for z in zones:
        r = zone_risk(z, db)
        zone_out.append({
            "id": z.id, "name": z.name, "type": z.type, "domain": z.domain,
            "location_note": z.location_note,
            "capacity": z.capacity, "current_count": z.current_count,
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


def causal_chain(db):
    state = get_state_row(db)
    gate2 = db.query(models.Zone).filter(models.Zone.name == "Gate 2").first()
    chain = []
    if not state.scenario_active:
        return ["No active scenario. Trigger the session-release scenario to see the causal chain build up live."]
    chain.append("Headline session ends — attendees begin exiting toward Gate 2")
    g2r = zone_risk(gate2, db)
    chain.append(f"Gate 2 inflow rises — utilization now {g2r['capacity_pressure_pct']}% ({g2r['level']})")
    corridor_b = db.get(models.Zone, gate2.linked_transport_zone_id)
    cb_pct = round(corridor_b.current_count / corridor_b.capacity * 100, 1)
    chain.append(f"{corridor_b.name} demand rises to {cb_pct}% as exiting attendees seek transport")
    hotel_a = db.get(models.Zone, gate2.linked_hospitality_zone_id)
    ha_pct = round(hotel_a.current_count / hotel_a.capacity * 100, 1)
    chain.append(f"{hotel_a.name} occupancy rises to {ha_pct}% from new same-night demand")
    return chain


def zone_causal_chain(db, zone_id):
    """Per-zone Risk Detail: for Gate 2 this is the full canonical chain;
    for every other zone it's a one-step breakdown of its own risk factors."""
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return []
    if zone.name == "Gate 2":
        return causal_chain(db)
    r = zone_risk(zone, db)
    return [
        f"{zone.name} is at {r['capacity_pressure_pct']}% of capacity ({r['level']})",
        f"Arrival surge factor: {r['arrival_surge']} · Flow instability: {r['flow_instability']} · "
        f"Linked-resource pressure: {r['resource_pressure']}",
    ]


# --- what-if simulator (recomputes live, never pre-baked) -----------------

BUS_CAPACITY_EACH = 50


def run_whatif(db, redirect_count=0, open_gate3=False, add_buses=0, move_staff=0):
    gate2 = db.query(models.Zone).filter(models.Zone.name == "Gate 2").first()
    gate3 = db.query(models.Zone).filter(models.Zone.name == "Gate 3").first()
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


# --- orchestration / action optimizer (Section 16) -------------------------

# domain = which operator role this action belongs to (Event Command Operator
# always sees/approves every domain; the other three operator roles only see
# and approve actions scoped to their own resource, per the shared
# approve/override use case).
CANDIDATE_ACTIONS = [
    dict(id="redirect", label="Redirect 2,000 visitors to Gate 3", domain="venue", resource_type=None, required=0,
         risk_reduction=40, capacity_balance=70, visitor_experience=55, time_to_impact=95, cost_efficiency=100),
    dict(id="open_gate3", label="Open Gate 3 additional lane", domain="venue", resource_type="staff", required=2,
         risk_reduction=15, capacity_balance=60, visitor_experience=80, time_to_impact=85, cost_efficiency=80),
    dict(id="dispatch_buses", label="Dispatch 4 buses to Corridor B", domain="transport", resource_type="bus", required=4,
         risk_reduction=25, capacity_balance=50, visitor_experience=70, time_to_impact=60, cost_efficiency=50),
    dict(id="move_staff", label="Move 6 staff to Gate 2/Gate 3", domain="venue", resource_type="staff", required=6,
         risk_reduction=10, capacity_balance=40, visitor_experience=65, time_to_impact=70, cost_efficiency=60),
    dict(id="recommend_hotel_b", label="Recommend Hotel B for new demand", domain="hospitality", resource_type=None, required=0,
         risk_reduction=12, capacity_balance=55, visitor_experience=75, time_to_impact=90, cost_efficiency=100),
]


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

        score = (
            0.35 * action["risk_reduction"]
            + 0.20 * action["capacity_balance"]
            + 0.15 * action["visitor_experience"]
            + 0.15 * feasibility
            + 0.10 * action["time_to_impact"]
            + 0.05 * action["cost_efficiency"]
        )
        ranked.append({**action, "feasibility": feasibility, "action_score": round(score, 1)})

    ranked.sort(key=lambda a: a["action_score"], reverse=True)
    return ranked


def approve_actions(db, action_ids):
    resources = {r.type: r for r in db.query(models.Resource).all()}
    applied = []
    for action in CANDIDATE_ACTIONS:
        if action["id"] not in action_ids:
            continue
        if action["resource_type"]:
            res = resources.get(action["resource_type"])
            if not res or res.quantity_available < action["required"]:
                continue
            res.quantity_available -= action["required"]
        applied.append(action["id"])
    db.commit()
    return applied


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
    visitor = db.query(models.VisitorProfile).filter(models.VisitorProfile.code == code).first()
    if not visitor:
        return {"status": "not_found"}
    if visitor.checked_in:
        return {"status": "already_checked_in"}

    zone = db.get(models.Zone, visitor.gate_zone_id)
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


# --- Transport Hub: illustrative arrivals feed ------------------------------
# Panvel Railway Station (our Transport Hub zone) really does interconnect to
# Navi Mumbai International Airport, which opened 25 Dec 2025 with IndiGo, Air
# India Express and Akasa Air flying real routes to these destinations. We
# have no live aviation-data feed here, so this schedule is illustrative and
# deterministic (seeded by tick, not random each call) — clearly not a claim
# of live flight tracking, same honest synthetic-vs-real framing as the rest
# of the crowd data.
NMIA_ROUTES = [
    ("IndiGo", "Bengaluru"), ("IndiGo", "Jaipur"), ("IndiGo", "Nagpur"), ("IndiGo", "Patna"),
    ("IndiGo", "Indore"), ("IndiGo", "Ahmedabad"), ("Air India Express", "Delhi"),
    ("Air India Express", "Bengaluru"), ("Akasa Air", "Goa"), ("Akasa Air", "Kochi"),
    ("Akasa Air", "Delhi"), ("Akasa Air", "Ahmedabad"),
]


def transport_hub_arrivals(db):
    state = get_state_row(db)
    rnd = random.Random(state.tick)  # deterministic per tick, not per request
    picks = rnd.sample(NMIA_ROUTES, k=4)
    arrivals = [
        {"airline": airline, "origin": origin, "arrives_in_min": 8 + i * 12}
        for i, (airline, origin) in enumerate(picks)
    ]
    return {
        "note": "Illustrative schedule, not live tracking — reflects NMIA's real Dec-2025 launch "
                "route network (IndiGo, Air India Express, Akasa Air).",
        "arrivals": arrivals,
    }
