"""Shared data loading / feature engineering for the LSTM and GNN training
scripts. Reads the validated Step 1 dataset (backend/data/*.csv) and builds
example tensors. No dependency on the live app.

FEATURE DESIGN (agreed in Step 2A/2B review -- do not add fields without
re-checking the leakage/generalization notes below):

Per-tick LSTM feature vector (39 dims, see FEATURE_DIM_LSTM):
  occupancy_pct/100, entry_rate_pct/100, exit_rate_pct/100 (capacity-relative,
  not raw counts), event_phase one-hot(3), event_type one-hot(5),
  domain one-hot(3), sport one-hot(4, zero vector if blank/non-sports),
  competition one-hot(9, zero if blank), tournament_gender one-hot(2, zero if
  blank), stage one-hot(4, zero if blank), has_ticket_data flag,
  sell_through_pct/100 (0 if blank), booking_velocity/100 (0 if blank),
  demand_intensity/100, capacity_scaled (log1p + train-fit min-max),
  ticks_since_phase_start / n_ticks.

Per-tick GNN node feature vector (6 dims, see FEATURE_DIM_GNN):
  occupancy_pct/100, entry_rate_pct/100, exit_rate_pct/100, domain one-hot(3).

EXCLUDED from every tensor, on purpose:
  - scenario_type: the generative oracle label. Kept only as an eval-time
    slicing key (see `slice_keys` in each example dict).
  - team_home/team_away: high-cardinality identity fields -- would cause
    memorization, not generalization. demand_intensity already carries the
    "how in-demand is this match" signal.
  - zone_role (the 9 Wankhede-specific zone names): kept only as a bookkeeping
    key for building sequences / eval slicing. NEVER one-hot/embedded as a
    model input -- that is the key generalization guardrail for both models.
  - run_id, raw tick: bookkeeping only. Time-in-event is represented via
    event_phase + ticks_since_phase_start/n_ticks instead of an absolute tick
    index, which doesn't generalize across variable-length runs.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import defaultdict

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CSV_PATH = os.path.join(DATA_DIR, "synthetic_crowd_sequences.csv")
GRAPH_PATH = os.path.join(DATA_DIR, "zone_graph_template.csv")
WEIGHTS_DIR = os.path.join(os.path.dirname(__file__), "weights")

SEED = 1234

EVENT_TYPES = ["concert", "conference", "festival", "religious_gathering", "sports_match"]
SCENARIO_TYPES = ["hotel_saturation", "normal_baseline", "sudden_demand_spike", "transportation_congestion", "venue_capacity_limit"]
EVENT_PHASES = ["pre_event", "main", "exit"]
DOMAINS = ["hospitality", "transport", "venue"]
SPORTS = ["Cricket", "Football", "Hockey", "Kabaddi"]
COMPETITIONS = ["Domestic Trophy", "FIFA World Cup Qualifier", "Hockey India League", "ICC World Cup", "IPL", "ISL", "National Championship", "Pro Kabaddi League", "State League"]
GENDERS = ["Men's", "Women's"]
STAGES = ["Final", "League", "Qualifier", "Semi-Final"]

ZONE_ROLES = [
    "venue_bowl", "gate_vinoo_mankad", "gate_polly_umrigar", "gate_north_illustrative",
    "concourse_north", "concourse_sea_face", "churchgate_station",
    "hotel_intercontinental_marine_drive", "hotel_bentley_marine_drive",
]
ZONE_INDEX = {z: i for i, z in enumerate(ZONE_ROLES)}
N_ZONES = len(ZONE_ROLES)

# Undirected edge -> propagation lag in ticks, matching
# backend/scripts/generate_training_data.py's GATE_DOWNSTREAM (lag 3,
# gate->concourse) and STATION_UPSTREAM (concourses lag 3, the illustrative
# north gate's direct-to-station edge lag 5). Every other edge defaults to 1.
_EXPLICIT_LAGS = {
    frozenset({"gate_vinoo_mankad", "concourse_north"}): 3,
    frozenset({"gate_polly_umrigar", "concourse_sea_face"}): 3,
    frozenset({"concourse_north", "churchgate_station"}): 3,
    frozenset({"concourse_sea_face", "churchgate_station"}): 3,
    frozenset({"gate_north_illustrative", "churchgate_station"}): 5,
}
MAX_LAG = 5

# The single deterministic held-out structural graph for GNN generalization
# evaluation (never used in training/tuning). Removing these 2 nodes drops:
# gate_north_illustrative's 2 edges (venue_bowl link, and its lag-5 direct
# link to churchgate_station -- the one edge that isn't a gate->concourse
# style link) and hotel_bentley_marine_drive's 1 edge (from churchgate_station)
# -- leaving a 7-node/8-edge induced subgraph with a different shape (no
# direct gate->hub edge, only 1 hotel) from anything seen in training.
HELD_OUT_REMOVED_NODES = {"gate_north_illustrative", "hotel_bentley_marine_drive"}


def load_zone_edges():
    edges = []
    with open(GRAPH_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a, b = row["from_zone_role"], row["to_zone_role"]
            lag = _EXPLICIT_LAGS.get(frozenset({a, b}), 1)
            edges.append((a, b, lag))
    return edges


ZONE_EDGES = load_zone_edges()  # list of (zone_a, zone_b, lag_ticks), 11 entries


def _onehot(value, vocab):
    vec = [0.0] * len(vocab)
    if value in vocab:
        vec[vocab.index(value)] = 1.0
    return vec


def load_runs():
    """Returns {run_id: {meta..., 'zones': {zone_role: {'domain','capacity','ticks':[rowdict,...]}}}}."""
    runs = {}
    with open(CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rid = row["run_id"]
            if rid not in runs:
                runs[rid] = {
                    "event_type": row["event_type"], "scenario_type": row["scenario_type"],
                    "sport": row["sport"], "competition": row["competition"],
                    "tournament_gender": row["tournament_gender"], "stage": row["stage"],
                    "sell_through_pct": row["sell_through_pct"], "booking_velocity": row["booking_velocity"],
                    "demand_intensity": float(row["demand_intensity"]),
                    "zones": defaultdict(lambda: {"domain": None, "capacity": None, "ticks": []}),
                }
            zone = runs[rid]["zones"][row["zone_role"]]
            zone["domain"] = row["domain"]
            zone["capacity"] = int(row["capacity"])
            zone["ticks"].append(row)
    for rid, run in runs.items():
        for zone_role, zone in run["zones"].items():
            zone["ticks"].sort(key=lambda r: int(r["tick"]))
        run["n_ticks"] = len(next(iter(run["zones"].values()))["ticks"])
    return runs


def compute_phase_age(ticks):
    """ticks_since_this_phase_started, per tick (0-indexed)."""
    ages = []
    age = 0
    prev_phase = None
    for r in ticks:
        phase = r["event_phase"]
        age = 0 if phase != prev_phase else age + 1
        ages.append(age)
        prev_phase = phase
    return ages


def zone_series(zone):
    """Precompute per-tick derived arrays for one zone: occ_pct, entry_pct,
    exit_pct (both capacity-relative), phase, phase_age."""
    cap = zone["capacity"]
    occ = [float(r["occupancy_pct"]) for r in zone["ticks"]]
    entry_pct = [float(r["entry_rate"]) / cap * 100.0 for r in zone["ticks"]]
    exit_pct = [float(r["exit_rate"]) / cap * 100.0 for r in zone["ticks"]]
    phase = [r["event_phase"] for r in zone["ticks"]]
    phase_age = compute_phase_age(zone["ticks"])
    return {"occ": occ, "entry_pct": entry_pct, "exit_pct": exit_pct, "phase": phase, "phase_age": phase_age, "_capacity": cap}


FEATURE_DIM_LSTM = 3 + 3 + 5 + 3 + 4 + 9 + 2 + 4 + 1 + 1 + 1 + 1 + 1 + 1  # = 39
FEATURE_DIM_GNN = 3 + 3  # = 6


def lstm_feature_vector(run, zone_domain, series, t, n_ticks, capacity_scaler):
    sell = run["sell_through_pct"]
    booking = run["booking_velocity"]
    has_ticket = 0.0 if sell == "" else 1.0
    sell_v = 0.0 if sell == "" else float(sell) / 100.0
    booking_v = 0.0 if booking == "" else float(booking) / 100.0
    cap_scaled = capacity_scaler(series["_capacity"])
    vec = (
        [series["occ"][t] / 100.0, series["entry_pct"][t] / 100.0, series["exit_pct"][t] / 100.0]
        + _onehot(series["phase"][t], EVENT_PHASES)
        + _onehot(run["event_type"], EVENT_TYPES)
        + _onehot(zone_domain, DOMAINS)
        + _onehot(run["sport"], SPORTS)
        + _onehot(run["competition"], COMPETITIONS)
        + _onehot(run["tournament_gender"], GENDERS)
        + _onehot(run["stage"], STAGES)
        + [has_ticket, sell_v, booking_v, run["demand_intensity"] / 100.0, cap_scaled, series["phase_age"][t] / max(n_ticks, 1)]
    )
    assert len(vec) == FEATURE_DIM_LSTM, len(vec)
    return vec


def gnn_node_feature_vector(zone_domain, series, t):
    vec = [series["occ"][t] / 100.0, series["entry_pct"][t] / 100.0, series["exit_pct"][t] / 100.0] + _onehot(zone_domain, DOMAINS)
    assert len(vec) == FEATURE_DIM_GNN, len(vec)
    return vec


def fit_capacity_scaler(runs, split, split_name="train"):
    """log1p(capacity) then min-max, fit on the given split's zones only."""
    values = []
    for rid, run in runs.items():
        if split.get(rid) != split_name:
            continue
        for zone in run["zones"].values():
            values.append(np.log1p(zone["capacity"]))
    lo, hi = float(min(values)), float(max(values))
    scaler_cfg = {"log1p_min": lo, "log1p_max": hi}

    def scale(capacity):
        v = np.log1p(capacity)
        return float((v - lo) / (hi - lo)) if hi > lo else 0.0

    return scale, scaler_cfg


def stratified_run_split(runs, seed=SEED):
    """70/15/15 split by run_id, stratified by (event_type, scenario_type)."""
    groups = defaultdict(list)
    for rid, run in runs.items():
        groups[(run["event_type"], run["scenario_type"])].append(rid)
    rng = np.random.default_rng(seed)
    split = {}
    for key, rids in groups.items():
        rids = sorted(rids)  # deterministic order before shuffling
        rng.shuffle(rids)
        n = len(rids)
        n_train = round(0.7 * n)
        n_val = round(0.15 * n)
        n_train = min(n_train, n)
        n_val = min(n_val, n - n_train)
        for rid in rids[:n_train]:
            split[rid] = "train"
        for rid in rids[n_train:n_train + n_val]:
            split[rid] = "val"
        for rid in rids[n_train + n_val:]:
            split[rid] = "test"
    return split


def save_json(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]
