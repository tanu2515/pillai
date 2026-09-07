"""Advisory-only bridge to the trained LSTM/GNN in backend/ml/ (Step 2B/2B.1).

Imports are deliberately lazy (mirrors detector.py's pattern for optional
YOLO deps) so the app still runs with no torch/trained-weights present;
a missing piece just makes the advisory endpoints report "not available"
instead of failing.

HARD CONSTRAINT (per the product decision that closed out Step 2B.1's
validation): this module NEVER writes into Zone/risk state, and its output
is never merged into risk_factors()/zone_risk(). It only returns numbers
for the API to hand back as a clearly-labeled advisory field. The
deterministic Risk Engine in engine.py remains the sole authority for risk
levels/alerts, unchanged by anything in this file.

Two independent signals only, matching what Step 2B.1 actually validated —
deliberately NOT composing them (that composition was measured and found to
have no effect, see validate_step2b1.py's Part 4 / gate_experiment.py):
  - LSTM: per-zone future occupancy forecast (+15/+30/+60m). Works for ANY
    zone in ANY event (needs only that zone's own recent history + its
    domain + the event's category/ticket-demand signal) — no dependency on
    the fixed 9-zone graph.
  - GNN: network-propagated occupancy from CURRENT telemetry only (~1
    tick/minute ahead), for the one event whose zones map onto the 9 zone
    roles the GNN was trained on (see ZONE_ROLE_MAP below). Any other
    event's zone layout simply has no GNN signal available -- reported as
    None, not guessed.
"""
import os
import sys
from datetime import datetime, timezone

from . import models

ML_DIR = os.path.join(os.path.dirname(__file__), "..", "ml")

MIN_HISTORY_TICKS = 12  # forecast_zone()'s 12-tick lookback window

# Maps the live seeded event's canonical zone names onto the 9 zone roles the
# GNN was trained on (backend/ml/features.py's ZONE_ROLES) -- this mapping is
# only valid for an event using this exact layout (the default seeded
# "Panvel Mega Fest", or any event an operator deliberately names to match).
# A custom event with different zone names gets LSTM-only advisory (works
# for any zone) but no network-propagation signal -- not guessed, just absent.
ZONE_ROLE_MAP = {
    "Main Hall": "venue_bowl",
    "Gate 1": "gate_vinoo_mankad",
    "Gate 2": "gate_polly_umrigar",
    "Gate 3": "gate_north_illustrative",
    "Corridor A": "concourse_north",
    "Corridor B": "concourse_sea_face",
    "Transport Hub": "churchgate_station",
    "Hotel A": "hotel_intercontinental_marine_drive",
    "Hotel B": "hotel_bentley_marine_drive",
}

# backend/app's live Event.category values -> the 5 event_type categories the
# models were trained on. College Event/Workshop have no direct training
# analogue -- approximated as the closest shape (conference: flatter
# attendance curve, no single big end-of-show exit spike). Documented here,
# not hidden, since it's a real approximation.
EVENT_TYPE_MAP = {
    "Concert": "concert", "Sports": "sports_match", "Conference": "conference",
    "Festival": "festival", "College Event": "conference", "Workshop": "conference",
}

# Must match backend/ml/features.py's SPORTS/COMPETITIONS/GENDERS/STAGES
# exactly. Duplicated here (rather than imported) so validating a sports-
# details write doesn't require importing torch/the ML stack at all -- a
# value outside these lists silently becomes an all-zero feature vector at
# inference time (features._onehot()'s behavior), which previously failed
# silently; validating at write time turns that into a real 400 instead.
TRAINED_SPORTS = {"Cricket", "Football", "Hockey", "Kabaddi"}
TRAINED_COMPETITIONS = {
    "Domestic Trophy", "FIFA World Cup Qualifier", "Hockey India League", "ICC World Cup",
    "IPL", "ISL", "National Championship", "Pro Kabaddi League", "State League",
}
TRAINED_GENDERS = {"Men's", "Women's"}
TRAINED_STAGES = {"Final", "League", "Qualifier", "Semi-Final"}


def validate_sports_vocab(sport, competition=None, tournament_gender=None, stage=None):
    """Raises ValueError (caller translates to HTTP 400) if any given
    non-empty field isn't in the exact vocab the LSTM was trained on."""
    if sport not in TRAINED_SPORTS:
        raise ValueError(f"sport must be one of {sorted(TRAINED_SPORTS)}")
    if competition and competition not in TRAINED_COMPETITIONS:
        raise ValueError(f"competition must be one of {sorted(TRAINED_COMPETITIONS)}")
    if tournament_gender and tournament_gender not in TRAINED_GENDERS:
        raise ValueError(f"tournament_gender must be one of {sorted(TRAINED_GENDERS)}")
    if stage and stage not in TRAINED_STAGES:
        raise ValueError(f"stage must be one of {sorted(TRAINED_STAGES)}")


_ml_cache = {}


def _import_ml():
    if "inference" in _ml_cache:
        return _ml_cache["inference"]
    if ML_DIR not in sys.path:
        sys.path.insert(0, ML_DIR)
    import inference as ml_inference  # bare import; inference.py itself does `import features as F`
    _ml_cache["inference"] = ml_inference
    return ml_inference


def _load_models():
    if "lstm" in _ml_cache:
        return _ml_cache["lstm"], _ml_cache["capacity_scaler"], _ml_cache.get("gnn"), _ml_cache.get("gnn_cfg")
    ml_inference = _import_ml()
    lstm_model, capacity_scaler = ml_inference.load_lstm()
    _ml_cache["lstm"], _ml_cache["capacity_scaler"] = lstm_model, capacity_scaler
    try:
        gnn_model, gnn_cfg = ml_inference.load_gnn()
        _ml_cache["gnn"], _ml_cache["gnn_cfg"] = gnn_model, gnn_cfg
    except Exception:
        _ml_cache["gnn"], _ml_cache["gnn_cfg"] = None, None
    return lstm_model, capacity_scaler, _ml_cache["gnn"], _ml_cache["gnn_cfg"]


def _zone_series_from_live(db, zone, phase="main", phase_age_now=0, ticks=None, min_ticks=MIN_HISTORY_TICKS):
    """Builds the same {occ, entry_pct, exit_pct, phase, phase_age, _capacity}
    shape features.zone_series() produces, from live CrowdSnapshot rows
    instead of the training CSV.

    Deltas (entry_pct/exit_pct) are computed over the FULL history first,
    THEN truncated to `ticks` if requested -- truncating first would force
    the truncated window's oldest row to a fake zero delta even though a
    real prior reading exists just outside the window (this was a real bug,
    found during the Step 2B.1-followup inspection: it only bit the GNN's
    short 6-tick window, not the LSTM's untruncated full-history call).

    phase/phase_age_now: see _current_event_phase() -- applied UNIFORMLY
    across the whole window (a known simplification: CrowdSnapshot has no
    per-row tick reference, so a historically-accurate per-tick phase
    sequence isn't reconstructable; only the CURRENT phase is known). The
    age sequence is built to end exactly at phase_age_now for the most
    recent (last) tick, which is the one the LSTM's final hidden state
    weighs most heavily."""
    rows = (
        db.query(models.CrowdSnapshot)
        .filter(models.CrowdSnapshot.zone_id == zone.id)
        .order_by(models.CrowdSnapshot.captured_at.asc())
        .all()
    )
    if len(rows) < min_ticks:
        return None
    cap = zone.capacity or 1
    counts = [r.count for r in rows]
    occ = [c / cap * 100.0 for c in counts]
    entry_pct, exit_pct = [0.0], [0.0]
    for i in range(1, len(counts)):
        delta = counts[i] - counts[i - 1]
        entry_pct.append(max(delta, 0) / cap * 100.0)
        exit_pct.append(max(-delta, 0) / cap * 100.0)
    if ticks:
        occ, entry_pct, exit_pct = occ[-ticks:], entry_pct[-ticks:], exit_pct[-ticks:]
    n = len(occ)
    phase_age = [max(0, phase_age_now - (n - 1 - i)) for i in range(n)]
    return {
        "occ": occ, "entry_pct": entry_pct, "exit_pct": exit_pct,
        "phase": [phase] * n, "phase_age": phase_age, "_capacity": cap,
    }


def _current_event_phase(db, event):
    """Deterministic, live-state-derived phase -- not per-tick-historical
    (see _zone_series_from_live's docstring for why). A Scenario's active
    ramp *is* this app's model of "session ends, crowd exits", which is
    exactly the training data's "exit" phase concept, so:
      exit      = a scenario is currently mid-ramp
      pre_event = no scenario has ever triggered yet AND we're still very
                  early in the simulation (tick <= 5)
      main      = otherwise, including after a ramp completes (training's
                  "exit" phase is a bounded window, not permanent)
    Returns (phase, phase_age) -- phase_age is a real ticks-since-phase-began
    value for "exit" (tick - trigger_tick), else the current tick itself as
    the closest available proxy for pre_event/main (both open-ended live)."""
    from . import engine as eng
    state = eng.get_state_row(db)
    if not state:
        return "main", 0
    if state.scenario_active and state.trigger_tick >= 0:
        scenario = db.get(models.Scenario, state.active_scenario_id) if state.active_scenario_id else None
        duration = scenario.duration_ticks if scenario else 0
        since = state.tick - state.trigger_tick
        if 0 <= since < duration:
            return "exit", since
    if state.trigger_tick < 0 and state.tick <= 5:
        return "pre_event", state.tick
    return "main", state.tick


def _run_meta_for_event(db, event):
    from . import engine as eng  # local import: avoids a hard engine<->ml_advisory import cycle at module load
    demand = eng.event_ticket_demand(db, event)
    sports = eng.get_event_sports_details(db, event.id)
    event_type = EVENT_TYPE_MAP.get(event.category, "conference")
    return {
        "event_type": event_type,
        "sport": (sports or {}).get("sport") or "",
        "competition": (sports or {}).get("competition") or "",
        "tournament_gender": (sports or {}).get("tournament_gender") or "",
        "stage": (sports or {}).get("stage") or "",
        "sell_through_pct": demand["sell_through_pct"],
        "booking_velocity": demand["booking_velocity"],
        "demand_intensity": demand["demand_intensity"],
    }


def zone_forecast(db, zone_id):
    """LSTM advisory forecast for one zone. Returns None if the model/weights
    aren't available or this zone doesn't have enough history yet -- never a
    guessed number. Works for any zone in any event (no graph dependency)."""
    zone = db.get(models.Zone, zone_id)
    if not zone:
        return None
    event = db.get(models.Event, zone.event_id) if zone.event_id else None
    if not event:
        return None

    phase, phase_age_now = _current_event_phase(db, event)
    series = _zone_series_from_live(db, zone, phase=phase, phase_age_now=phase_age_now)
    if series is None:
        return {"available": False, "reason": f"needs at least {MIN_HISTORY_TICKS} crowd readings for this zone; not enough history yet"}

    try:
        lstm_model, capacity_scaler, _, _ = _load_models()
        ml_inference = _import_ml()
    except Exception as exc:
        return {"available": False, "reason": f"ML models not available ({exc})"}

    run_meta = _run_meta_for_event(db, event)
    t = len(series["occ"]) - 1
    forecast = ml_inference.forecast_zone(lstm_model, capacity_scaler, run_meta, zone.domain, series, t, len(series["occ"]))
    return {
        "available": True,
        "zone_id": zone.id, "zone_name": zone.name,
        "as_of": datetime.now(timezone.utc).isoformat(),
        # Clip at 0: model error can push a raw prediction slightly negative
        # on a near-flat zone (the same "phantom drift" tendency the Step
        # 2B.1 validation found) -- never a physically meaningful value.
        # No upper clip: >100% is legitimate (congestion, allowed elsewhere
        # in this app too, see advance_tick's own comment on gates/corridors).
        **{k: round(max(v, 0.0), 1) for k, v in forecast.items()},
        "note": "Advisory forecast — the deterministic Risk Engine remains authoritative.",
    }


def event_network_pressure(db, event_id):
    """GNN advisory network view for the live event, using CURRENT telemetry
    only (no LSTM composition — that path was measured as having no effect,
    see backend/ml/gate_experiment.py's Part 4, and must not be presented as
    a working capability). Returns None if this event's zones don't map onto
    the trained 9-zone graph (see ZONE_ROLE_MAP), or if any of them lack
    enough history yet -- never a partial/guessed graph."""
    event = db.get(models.Event, event_id)
    if not event:
        return None
    zones_by_name = {z.name: z for z in db.query(models.Zone).filter(models.Zone.event_id == event_id).all()}
    if not all(name in zones_by_name for name in ZONE_ROLE_MAP):
        return {"available": False, "reason": "this event's zones don't match the layout the network model was trained on (Main Hall/Gate 1-3/Corridor A-B/Transport Hub/Hotel A-B)"}

    try:
        _, _, gnn_model, _ = _load_models()
        ml_inference = _import_ml()
        import features as ml_features  # already on sys.path via _import_ml()/_load_models() above
    except Exception as exc:
        return {"available": False, "reason": f"ML models not available ({exc})"}
    if gnn_model is None:
        return {"available": False, "reason": "GNN weights not available"}

    current_feats, history_feats = {}, {}
    for live_name, role in ZONE_ROLE_MAP.items():
        zone = zones_by_name[live_name]
        series = _zone_series_from_live(db, zone, ticks=ml_features.MAX_LAG + 1, min_ticks=ml_features.MAX_LAG + 1)
        if series is None:
            return {"available": False, "reason": f"'{live_name}' needs at least {MIN_HISTORY_TICKS} crowd readings"}
        t = len(series["occ"]) - 1
        current_feats[role] = ml_features.gnn_node_feature_vector(zone.domain, series, t)
        history_feats[role] = [ml_features.gnn_node_feature_vector(zone.domain, series, tt) for tt in range(len(series["occ"]))]

    pressure = ml_inference.network_pressure(gnn_model, current_feats, history_feats, lstm_forecast_15m_by_zone=None)
    role_to_live_name = {v: k for k, v in ZONE_ROLE_MAP.items()}
    return {
        "available": True,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "zones": {
            role_to_live_name[role]: {"network_expected_occupancy_pct": round(v["network_expected_occupancy_pct"], 1)}
            for role, v in pressure.items()
        },
        "note": "Advisory network-propagation view (~1 tick ahead) — the deterministic Risk Engine remains authoritative.",
    }
