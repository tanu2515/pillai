"""Synthetic training-data generator for the LSTM crowd forecast and GNN flow
graph (PS-8 Section 9.1: 8-12 zones, 50-200 telemetry points per scenario, 5
deterministic disruption/demand scenarios).

Standalone -- does not touch the live app, its database, engine.py, or the
seeded demo event ("Panvel Mega Fest"). Nothing here is wired into the live
product yet; this only produces CSV training data.

GROUNDING: the venue is modeled on the real Wankhede Stadium, Mumbai (MCA/
Mumbai Indians home ground) so the numbers we CAN verify are real, not
invented -- but every crowd count, entry/exit rate, occupancy curve, ticket
sell-through, booking velocity, and demand-intensity value in the output is
SIMULATED, not observed. zone_reference.csv marks exactly which zone facts
are real vs illustrative, with a source note, so this is traceable when
someone asks "where did this data come from":
    - venue_bowl capacity 33,100 -- REAL (verified, current post-2011 capacity)
    - Vinoo Mankad Gate, Polly Umrigar Gate -- REAL names (the two publicly
      documented gate names); their per-gate entry/exit throughput capacity
      is NOT public, so those numbers are illustrative
    - Churchgate Station as the transport hub -- REAL (nearest station,
      ~500-700m from the stadium); its capacity here models illustrative
      event-day surge handling near the stadium, not the station's real
      total passenger capacity
    - InterContinental Marine Drive, Bentley Hotel Marine Drive -- REAL
      hotel names near the stadium; room counts/occupancy are illustrative
    - "Gate (North) - illustrative", the two concourse zones -- entirely
      illustrative, not documented anywhere; kept so the graph has a
      plausible venue->concourse->transport_hub shape for the GNN

Run:
    python backend/scripts/generate_training_data.py

Output (backend/data/, gitignored, regenerate any time by re-running this):
    synthetic_crowd_sequences.csv  -- one row per zone per tick per run (SIMULATED)
    zone_graph_template.csv        -- the canonical zone adjacency (for the GNN)
    zone_reference.csv             -- per-zone real-vs-illustrative grounding notes
"""
from __future__ import annotations

import csv
import os

import numpy as np

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
N_RUNS = 300
SEED = 42

EVENT_TYPES = ["concert", "sports_match", "conference", "festival", "religious_gathering"]
SCENARIO_TYPES = [
    "sudden_demand_spike", "hotel_saturation", "transportation_congestion",
    "venue_capacity_limit", "normal_baseline",
]

# kind: venue_bowl | gate | concourse | transport_hub | hotel -- drives which
# curve-shape function applies (see *_curve functions below), independent of
# the display name so the shape logic never has to string-match a name.
# capacity: a fixed int for the one verified real number (venue_bowl), or a
# (lo, hi) illustrative range randomized per run for everything else.
ZONE_ROLES = [
    {"role": "venue_bowl", "display_name": "Wankhede Stadium Bowl", "kind": "venue_bowl",
     "domain": "venue", "capacity": 33100, "grounding": "real",
     "source_note": "Verified current stadium capacity (post 2010-11 renovation)."},
    {"role": "gate_vinoo_mankad", "display_name": "Vinoo Mankad Gate", "kind": "gate",
     "domain": "venue", "capacity": (9000, 12000), "grounding": "real_name_illustrative_capacity",
     "source_note": "Real, documented main entrance gate; per-gate throughput capacity is not public."},
    {"role": "gate_polly_umrigar", "display_name": "Polly Umrigar Gate", "kind": "gate",
     "domain": "venue", "capacity": (7000, 9000), "grounding": "real_name_illustrative_capacity",
     "source_note": "Real, documented gate name; per-gate throughput capacity is not public."},
    {"role": "gate_north_illustrative", "display_name": "Gate 3 (North) - illustrative", "kind": "gate",
     "domain": "venue", "capacity": (5000, 7000), "grounding": "illustrative",
     "source_note": "Not a documented gate name -- added so the venue has 3 exits like the real layout implies."},
    {"role": "concourse_north", "display_name": "North Concourse - illustrative", "kind": "concourse",
     "domain": "transport", "capacity": (4000, 7000), "grounding": "illustrative",
     "source_note": "Exit concourse behind Vinoo Mankad Gate; not an officially documented zone."},
    {"role": "concourse_sea_face", "display_name": "Sea-Face Concourse - illustrative", "kind": "concourse",
     "domain": "transport", "capacity": (4000, 7000), "grounding": "illustrative",
     "source_note": "Exit concourse behind Polly Umrigar Gate, toward Marine Drive; not an officially documented zone."},
    {"role": "churchgate_station", "display_name": "Churchgate Station", "kind": "transport_hub",
     "domain": "transport", "capacity": (6000, 10000), "grounding": "real_name_illustrative_capacity",
     "source_note": "Real nearest station (Western Line, ~500-700m/5-10min walk); capacity here is illustrative event-day surge handling near the stadium, not the station's real total throughput."},
    {"role": "hotel_intercontinental_marine_drive", "display_name": "InterContinental Marine Drive", "kind": "hotel",
     "domain": "hospitality", "capacity": (250, 400), "grounding": "real_name_illustrative_capacity",
     "source_note": "Real hotel near the stadium/Marine Drive; room count is illustrative."},
    {"role": "hotel_bentley_marine_drive", "display_name": "Bentley Hotel Marine Drive", "kind": "hotel",
     "domain": "hospitality", "capacity": (80, 150), "grounding": "real_name_illustrative_capacity",
     "source_note": "Real hotel near the stadium/Marine Drive; room count is illustrative."},
]

ZONE_EDGES_TEMPLATE = [
    ("venue_bowl", "gate_vinoo_mankad"), ("venue_bowl", "gate_polly_umrigar"), ("venue_bowl", "gate_north_illustrative"),
    ("gate_vinoo_mankad", "concourse_north"), ("gate_polly_umrigar", "concourse_sea_face"),
    ("gate_polly_umrigar", "hotel_intercontinental_marine_drive"), ("gate_north_illustrative", "churchgate_station"),
    ("concourse_north", "concourse_sea_face"), ("concourse_sea_face", "churchgate_station"),
    ("churchgate_station", "hotel_bentley_marine_drive"), ("concourse_north", "hotel_intercontinental_marine_drive"),
]

# gate -> the concourse its exiting crowd flows onto, and the hotel that
# absorbs its overflow demand (mirrors Zone.linked_transport/hospitality_zone_id
# in the live schema) -- used to derive each concourse's curve FROM its actual
# upstream gate's exit surge (lagged/damped), not as an independent curve.
GATE_DOWNSTREAM = {
    "gate_vinoo_mankad": {"concourse": "concourse_north", "hotel": "hotel_intercontinental_marine_drive"},
    "gate_polly_umrigar": {"concourse": "concourse_sea_face", "hotel": None},
    "gate_north_illustrative": {"concourse": None, "hotel": None},  # exits straight onto the station, no concourse between
}
# what feeds churchgate_station directly, each with its own propagation lag (ticks)
STATION_UPSTREAM = [("concourse_north", 3), ("concourse_sea_face", 3), ("gate_north_illustrative", 5)]

SPORTS_COMPETITIONS = {
    "Cricket": ["IPL", "ICC World Cup", "Domestic Trophy"],
    "Football": ["ISL", "FIFA World Cup Qualifier", "State League"],
    "Kabaddi": ["Pro Kabaddi League", "National Championship"],
    "Hockey": ["Hockey India League", "National Championship"],
}
TOURNAMENT_GENDERS = ["Men's", "Women's"]
STAGES = ["League", "Qualifier", "Semi-Final", "Final"]
STAGE_SELL_THROUGH_RANGE = {
    "League": (45, 75), "Qualifier": (60, 85), "Semi-Final": (75, 95), "Final": (85, 99),
}
TEAM_CITIES = ["Mumbai", "Chennai", "Bengaluru", "Kolkata", "Delhi", "Pune", "Hyderabad", "Ahmedabad", "Jaipur", "Lucknow"]
TEAM_MASCOTS = ["Warriors", "Titans", "Strikers", "Riders", "Falcons", "Panthers", "Kings", "Chargers", "Blasters", "Royals"]


def sigmoid(t, center, steepness):
    return 1.0 / (1.0 + np.exp(-steepness * (t - center)))


def demand_intensity_from(sell_through_pct, booking_velocity):
    """Same formula the live app will use later (Step 2) on real
    EventTier/VisitorProfile data -- kept identical here so training and
    live inference compute this feature the same way."""
    return float(np.clip(0.6 * sell_through_pct + 0.4 * booking_velocity, 0, 100))


def sample_match_metadata(rng: np.random.Generator):
    sport = rng.choice(list(SPORTS_COMPETITIONS))
    competition = rng.choice(SPORTS_COMPETITIONS[sport])
    stage = rng.choice(STAGES)
    lo, hi = STAGE_SELL_THROUGH_RANGE[stage]
    sell_through = rng.uniform(lo, hi)
    if "World Cup" in competition:
        sell_through = min(99, sell_through + 10)
    booking_velocity = float(np.clip(sell_through * rng.uniform(0.6, 1.0) + rng.normal(0, 5), 0, 100))
    home_city, away_city = rng.choice(TEAM_CITIES, size=2, replace=False)
    home_mascot, away_mascot = rng.choice(TEAM_MASCOTS, size=2, replace=False)
    return {
        "sport": sport, "competition": competition, "tournament_gender": rng.choice(TOURNAMENT_GENDERS),
        "stage": stage, "team_home": f"{home_city} {home_mascot}", "team_away": f"{away_city} {away_mascot}",
        "sell_through_pct": round(sell_through, 1), "booking_velocity": round(booking_velocity, 1),
    }


def sample_ticketed_metadata(rng: np.random.Generator):
    """Non-sports ticketed events (concert/conference/festival) still have a
    sell-through/booking-velocity signal, just no sport/team fields."""
    sell_through = rng.uniform(40, 99)
    booking_velocity = float(np.clip(sell_through * rng.uniform(0.5, 1.0) + rng.normal(0, 8), 0, 100))
    return {"sell_through_pct": round(sell_through, 1), "booking_velocity": round(booking_velocity, 1)}


NO_MATCH_FIELDS = {"sport": "", "competition": "", "tournament_gender": "", "stage": "", "team_home": "", "team_away": ""}

EXIT_STEEPNESS_BY_EVENT_TYPE = {"concert": 4.0, "sports_match": 9.0, "conference": 2.0, "festival": 4.5, "religious_gathering": 1.5}


def venue_bowl_curve(capacity, scenario_type, event_type, intensity, n_ticks, phases, rng):
    pre_end, main_end = phases
    t = np.arange(n_ticks)
    arrival = sigmoid(t, pre_end * 0.6, 6.0 / max(pre_end, 1))
    if scenario_type == "venue_capacity_limit":
        peak = capacity * (1.0 + 0.05 * intensity)
        hold = np.where(t < main_end, 1.0, 0.0)
        exit_drop = np.where(t >= main_end, sigmoid(t, main_end + (n_ticks - main_end) * 0.3, 6.0 / max(n_ticks - main_end, 1)), 0.0)
        counts = arrival * peak * (hold * 0.97 + (1 - hold)) - exit_drop * peak * 0.9
    else:
        peak = capacity * (0.55 + 0.25 * intensity)
        exit_curve = np.where(t >= main_end, sigmoid(t, main_end + (n_ticks - main_end) * 0.4, 5.0 / max(n_ticks - main_end, 1)), 0.0)
        counts = arrival * peak - exit_curve * peak * 0.7
    noise = rng.normal(0, capacity * 0.01, n_ticks)
    return np.clip(counts + noise, 0, capacity * 1.3)


def gate_curve(capacity, scenario_type, event_type, intensity, n_ticks, phases, rng):
    pre_end, main_end = phases
    t = np.arange(n_ticks)
    arrival = sigmoid(t, pre_end * 0.55, 6.0 / max(pre_end, 1)) * capacity * (0.35 + 0.2 * intensity)
    interval_bump = 0.0
    if event_type == "sports_match":
        mid_main = (pre_end + main_end) / 2
        interval_bump = capacity * 0.08 * intensity * np.exp(-((t - mid_main) ** 2) / (2 * (n_ticks * 0.03) ** 2))
    exit_steepness = EXIT_STEEPNESS_BY_EVENT_TYPE[event_type]
    exit_span = max(n_ticks - main_end, 1)
    exit_center = main_end + exit_span * (0.25 if scenario_type == "sudden_demand_spike" else 0.5)
    exit_peak_mult = 1.0 + (0.35 if scenario_type == "sudden_demand_spike" else 0.1) * intensity
    exit_curve = sigmoid(t, exit_center, exit_steepness / max(exit_span, 1)) * capacity * exit_peak_mult
    main_end_i = int(main_end)
    counts = np.where(t < main_end, arrival + interval_bump, arrival[main_end_i - 1] + exit_curve - exit_curve[main_end_i])
    noise = rng.normal(0, capacity * 0.01, n_ticks)
    return np.clip(counts + noise, 0, capacity * 1.5)


def downstream_flow_curve(capacity, scenario_type, upstream_deltas, lags, n_ticks, rng):
    """A concourse/transport_hub's count = a baseline occupancy plus the
    (lagged, damped) exit-flow delta of whichever gate/concourse actually
    feeds it -- not an independent curve, so a spike here is traceable to
    its real upstream cause."""
    baseline = capacity * rng.uniform(0.25, 0.4)
    boost = 1.6 if scenario_type == "transportation_congestion" else 1.0
    inflow = np.zeros(n_ticks)
    for delta, lag in zip(upstream_deltas, lags):
        shifted = np.zeros(n_ticks)
        if lag < n_ticks:
            shifted[lag:] = np.clip(delta[: n_ticks - lag], 0, None)
        inflow += shifted * 0.6
    counts = baseline + inflow * boost
    noise = rng.normal(0, capacity * 0.01, n_ticks)
    cap_mult = 1.6 if scenario_type == "transportation_congestion" else 1.2
    return np.clip(counts + noise, 0, capacity * cap_mult)


def hotel_curve(capacity, scenario_type, event_type, intensity, n_ticks, rng):
    if scenario_type == "hotel_saturation":
        ramp_len = n_ticks * (1.3 if event_type == "sports_match" else 0.8)
        counts = sigmoid(np.arange(n_ticks), ramp_len * 0.5, 5.0 / max(ramp_len, 1)) * capacity * (0.9 + 0.1 * intensity)
    else:
        counts = capacity * rng.uniform(0.5, 0.7) + rng.normal(0, capacity * 0.02, n_ticks)
    return np.clip(counts, 0, capacity)


def normal_baseline_curve(capacity, n_ticks, rng):
    baseline = capacity * rng.uniform(0.2, 0.45)
    t = np.arange(n_ticks)
    drift = capacity * 0.05 * np.sin(t / max(n_ticks, 1) * np.pi)
    noise = rng.normal(0, capacity * 0.01, n_ticks)
    return np.clip(baseline + drift + noise, 0, capacity * 0.9)


def generate_run(run_id, rng: np.random.Generator):
    event_type = rng.choice(EVENT_TYPES)
    scenario_type = rng.choice(SCENARIO_TYPES)
    n_ticks = int(rng.integers(60, 150))
    pre_end = n_ticks * rng.uniform(0.2, 0.35)
    main_end = pre_end + (n_ticks - pre_end) * rng.uniform(0.55, 0.75)
    phases = (pre_end, main_end)
    t = np.arange(n_ticks)
    phase_labels = np.where(t < pre_end, "pre_event", np.where(t < main_end, "main", "exit"))

    match_meta = dict(NO_MATCH_FIELDS)
    if event_type == "sports_match":
        sampled = sample_match_metadata(rng)
        match_meta.update({k: sampled.pop(k) for k in list(NO_MATCH_FIELDS)})
        sell_through_pct, booking_velocity = sampled["sell_through_pct"], sampled["booking_velocity"]
    elif event_type == "religious_gathering":
        sell_through_pct, booking_velocity = "", ""
    else:
        sampled = sample_ticketed_metadata(rng)
        sell_through_pct, booking_velocity = sampled["sell_through_pct"], sampled["booking_velocity"]

    demand_intensity = round(
        demand_intensity_from(sell_through_pct, booking_velocity)
        if sell_through_pct != "" else rng.uniform(30, 95), 1
    )
    intensity = demand_intensity / 100.0

    capacities = {}
    for zone in ZONE_ROLES:
        cap = zone["capacity"]
        capacities[zone["role"]] = cap if isinstance(cap, int) else int(rng.integers(*cap))

    counts_by_role = {}
    if scenario_type == "normal_baseline":
        for zone in ZONE_ROLES:
            counts_by_role[zone["role"]] = normal_baseline_curve(capacities[zone["role"]], n_ticks, rng)
    else:
        counts_by_role["venue_bowl"] = venue_bowl_curve(capacities["venue_bowl"], scenario_type, event_type, intensity, n_ticks, phases, rng)
        for role in ["gate_vinoo_mankad", "gate_polly_umrigar", "gate_north_illustrative"]:
            counts_by_role[role] = gate_curve(capacities[role], scenario_type, event_type, intensity, n_ticks, phases, rng)

        gate_deltas = {role: np.diff(counts_by_role[role], prepend=counts_by_role[role][0]) for role in GATE_DOWNSTREAM}
        for gate_role, downstream in GATE_DOWNSTREAM.items():
            concourse_role = downstream["concourse"]
            if concourse_role:
                counts_by_role[concourse_role] = downstream_flow_curve(
                    capacities[concourse_role], scenario_type, [gate_deltas[gate_role]], [3], n_ticks, rng,
                )

        station_deltas, station_lags = [], []
        for upstream_role, lag in STATION_UPSTREAM:
            source = counts_by_role.get(upstream_role, gate_deltas.get(upstream_role))
            delta = np.diff(source, prepend=source[0]) if upstream_role not in gate_deltas else gate_deltas[upstream_role]
            station_deltas.append(delta)
            station_lags.append(lag)
        counts_by_role["churchgate_station"] = downstream_flow_curve(
            capacities["churchgate_station"], scenario_type, station_deltas, station_lags, n_ticks, rng,
        )

        for role in ["hotel_intercontinental_marine_drive", "hotel_bentley_marine_drive"]:
            counts_by_role[role] = hotel_curve(capacities[role], scenario_type, event_type, intensity, n_ticks, rng)

    rows = []
    for zone in ZONE_ROLES:
        role = zone["role"]
        counts = counts_by_role[role]
        capacity = capacities[role]
        prev = max(0, round(float(counts[0])))  # match the rounding `count` uses below, so tick 0's delta is exactly 0
        for tick in range(n_ticks):
            count = max(0, round(float(counts[tick])))
            delta = count - prev
            rows.append({
                "run_id": run_id, "event_type": event_type, "scenario_type": scenario_type,
                **match_meta,
                "sell_through_pct": sell_through_pct, "booking_velocity": booking_velocity,
                "demand_intensity": demand_intensity,
                "zone_role": role, "domain": zone["domain"], "capacity": capacity,
                "tick": tick, "event_phase": phase_labels[tick],
                "crowd_count": count,
                "entry_rate": max(delta, 0), "exit_rate": max(-delta, 0),
                "occupancy_pct": round(count / capacity * 100, 2),
            })
            prev = count
    return rows


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = np.random.default_rng(SEED)

    columns = [
        "run_id", "event_type", "scenario_type",
        "sport", "competition", "tournament_gender", "stage", "team_home", "team_away",
        "sell_through_pct", "booking_velocity", "demand_intensity",
        "zone_role", "domain", "capacity", "tick", "event_phase",
        "crowd_count", "entry_rate", "exit_rate", "occupancy_pct",
    ]
    seq_path = os.path.join(OUT_DIR, "synthetic_crowd_sequences.csv")
    with open(seq_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        total_rows = 0
        for run_id in range(N_RUNS):
            rows = generate_run(run_id, rng)
            writer.writerows(rows)
            total_rows += len(rows)
    print(f"Wrote {total_rows} rows across {N_RUNS} runs -> {seq_path}")

    graph_path = os.path.join(OUT_DIR, "zone_graph_template.csv")
    role_domain = {z["role"]: z["domain"] for z in ZONE_ROLES}
    with open(graph_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["from_zone_role", "to_zone_role", "from_domain", "to_domain"])
        for a, b in ZONE_EDGES_TEMPLATE:
            writer.writerow([a, b, role_domain[a], role_domain[b]])
    print(f"Wrote {len(ZONE_EDGES_TEMPLATE)} edges -> {graph_path}")

    ref_path = os.path.join(OUT_DIR, "zone_reference.csv")
    with open(ref_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["role", "display_name", "kind", "domain", "capacity_note", "grounding", "source_note"])
        writer.writeheader()
        for zone in ZONE_ROLES:
            cap = zone["capacity"]
            cap_note = str(cap) if isinstance(cap, int) else f"{cap[0]}-{cap[1]} (randomized per run)"
            writer.writerow({
                "role": zone["role"], "display_name": zone["display_name"], "kind": zone["kind"],
                "domain": zone["domain"], "capacity_note": cap_note,
                "grounding": zone["grounding"], "source_note": zone["source_note"],
            })
    print(f"Wrote zone grounding reference -> {ref_path}")


if __name__ == "__main__":
    main()
