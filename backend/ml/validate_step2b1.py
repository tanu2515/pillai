"""Step 2B.1 -- validation-only analysis of the already-trained LSTM/GNN
(backend/ml/weights/*). Does NOT retrain or modify either model's
architecture; loads saved weights and re-evaluates on the exact same test
split already used in Step 2B (reusing train_lstm.py's/train_gnn.py's own
build_examples() so the example sets are byte-for-byte identical, not
independently reconstructed).

Risk-threshold note: engine.py's risk_level() cutoffs (LOW<=30, MODERATE<=55,
HIGH<=75, CRITICAL>75) are applied directly to occupancy_pct here as the
closest available analogue for a "pressure event". This is an approximation
of the real composite risk score (which also factors in arrival_surge,
flow_instability, resource_pressure, time_to_criticality -- none of which
this offline synthetic-only setup computes), not the literal same formula.

Run: python backend/ml/validate_step2b1.py
Output printed to stdout + saved to backend/ml/weights/validate_step2b1_report.json
"""
import json
import os
import time

import numpy as np
import torch

import features as F
import train_lstm as TL
import train_gnn as TG
from inference import load_lstm, load_gnn, forecast_zone

THRESHOLDS = {"pressure_55": 55.0, "critical_75": 75.0}
DISRUPTION_SCENARIOS = ["sudden_demand_spike", "transportation_congestion", "venue_capacity_limit", "hotel_saturation"]
REPORT_PATH = os.path.join(F.WEIGHTS_DIR, "validate_step2b1_report.json")


def confusion(actual, predicted):
    actual, predicted = np.asarray(actual, bool), np.asarray(predicted, bool)
    tp = int((actual & predicted).sum())
    fp = int((~actual & predicted).sum())
    fn = int((actual & ~predicted).sum())
    tn = int((~actual & ~predicted).sum())
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall and (precision + recall) > 0) else (0.0 if precision == 0 or recall == 0 else None)
    fpr = fp / (fp + tn) if (fp + tn) > 0 else None
    missed = (1 - recall) if recall is not None else None
    return {"n": tp + fp + fn + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1, "fpr": fpr, "missed_event_rate": missed}


def part1_pressure_detection(lstm_examples, lstm_model):
    print("\n" + "=" * 80 + "\nPART 1 -- pressure-event detection (LSTM vs persistence)\n" + "=" * 80)
    X, Y, M = TL.to_tensors(lstm_examples["test"])
    with torch.no_grad():
        pred = lstm_model(X).numpy()
    target, mask = Y.numpy(), M.numpy()
    current = X[:, -1, 0].numpy()  # occ/100 at anchor tick, same convention train_lstm.py used for its persistence baseline
    meta = lstm_examples["test"]
    scenario = np.array([e["scenario_type"] for e in meta])

    results = {}
    for thr_name, thr in THRESHOLDS.items():
        results[thr_name] = {"threshold_pct": thr, "by_horizon": {}}
        for hi, h in enumerate(TL.HORIZONS):
            m = mask[:, hi].astype(bool)
            cur_pct = current[m] * 100.0
            act_pct = target[m, hi] * 100.0
            pred_pct = pred[m, hi] * 100.0
            sc = scenario[m]

            eligible = cur_pct <= thr  # only examples not already past threshold -- there's a real "crossing" to (maybe) detect
            slices = {"overall": np.ones(len(cur_pct), bool), "normal_baseline": sc == "normal_baseline",
                      "disruption_pooled": np.isin(sc, DISRUPTION_SCENARIOS)}
            for s in DISRUPTION_SCENARIOS:
                slices[s] = sc == s

            per_horizon = {}
            for slice_name, sel in slices.items():
                sel_e = sel & eligible
                n_elig, n_total = int(sel_e.sum()), int(sel.sum())
                if n_elig == 0:
                    per_horizon[slice_name] = {"n_eligible": 0, "n_total_in_slice": n_total}
                    continue
                a = act_pct[sel_e] > thr
                p_lstm = pred_pct[sel_e] > thr
                p_persist = cur_pct[sel_e] > thr  # always False by construction (eligible => cur<=thr) -- computed for real, not assumed
                mae_lstm = float(np.mean(np.abs(pred_pct[sel_e] - act_pct[sel_e])))
                mae_persist = float(np.mean(np.abs(cur_pct[sel_e] - act_pct[sel_e])))
                per_horizon[slice_name] = {
                    "n_eligible": n_elig, "n_total_in_slice": n_total,
                    "lstm": {"mae": mae_lstm, **confusion(a, p_lstm)},
                    "persistence": {"mae": mae_persist, **confusion(a, p_persist)},
                }
            results[thr_name]["by_horizon"][f"+{h}m"] = per_horizon
    return results


def part2_diagnose_60m(lstm_examples, lstm_model, runs, split):
    print("\n" + "=" * 80 + "\nPART 2 -- diagnosing the +60m weakness\n" + "=" * 80)
    X, Y, M = TL.to_tensors(lstm_examples["test"])
    with torch.no_grad():
        pred = lstm_model(X).numpy()
    target, mask = Y.numpy(), M.numpy()
    current = X[:, -1, 0].numpy()
    meta = lstm_examples["test"]

    hi60 = TL.HORIZONS.index(60)
    m60 = mask[:, hi60].astype(bool)
    err60 = np.abs(pred[m60, hi60] - target[m60, hi60]) * 100.0
    actual_change60 = np.abs(target[m60, hi60] - current[m60]) * 100.0
    predicted_change60 = np.abs(pred[m60, hi60] - current[m60]) * 100.0

    # (a) run-length coverage: how many runs/examples even reach +60m
    n_ticks_by_run = {rid: run["n_ticks"] for rid, run in runs.items()}
    test_run_ids = sorted({rid for rid, s in split.items() if s == "test"})
    below_72 = sum(1 for rid in test_run_ids if n_ticks_by_run[rid] < 72)
    coverage = {"test_runs_total": len(test_run_ids), "test_runs_with_n_ticks_below_72": below_72,
                "note": "a +60m target requires n_ticks>=72 given SEQ_LEN=12/STRIDE=2 (t>=11, t<=n_ticks-61) -- runs shorter than 72 ticks contribute ZERO +60m examples at all."}
    buckets = [(60, 89), (90, 119), (120, 150)]
    by_run_length = {}
    ex60 = [e for e, mm in zip(meta, mask[:, hi60]) if mm]
    err60_list = list(err60)
    for lo, hi in buckets:
        idx = [i for i, e in enumerate(ex60) if lo <= runs[e["run_id"]]["n_ticks"] < hi]
        if idx:
            by_run_length[f"{lo}-{hi-1}"] = {"n": len(idx), "mae": float(np.mean([err60_list[i] for i in idx]))}
        else:
            by_run_length[f"{lo}-{hi-1}"] = {"n": 0, "mae": None}

    # (b) does LSTM over-predict movement vs actual movement, by scenario? (signed, not abs, to see direction)
    scenario60 = np.array([e["scenario_type"] for e in ex60])
    signed_pred_change = (pred[m60, hi60] - current[m60]) * 100.0
    signed_act_change = (target[m60, hi60] - current[m60]) * 100.0
    by_scenario_movement = {}
    for s in sorted(set(scenario60)):
        sel = scenario60 == s
        by_scenario_movement[s] = {
            "n": int(sel.sum()),
            "mean_actual_abs_change_pp": float(np.mean(np.abs(signed_act_change[sel]))),
            "mean_predicted_abs_change_pp": float(np.mean(np.abs(signed_pred_change[sel]))),
            "mean_actual_signed_change_pp": float(np.mean(signed_act_change[sel])),
            "mean_predicted_signed_change_pp": float(np.mean(signed_pred_change[sel])),
        }

    # (c) correlation between actual volatility (|actual change|) and LSTM abs error, overall and per event_type
    corr_overall = float(np.corrcoef(actual_change60, err60)[0, 1])
    event_type60 = np.array([e["event_type"] for e in ex60])
    corr_by_event = {}
    for et in sorted(set(event_type60)):
        sel = event_type60 == et
        if sel.sum() > 2 and np.std(actual_change60[sel]) > 0:
            corr_by_event[et] = {"n": int(sel.sum()), "corr_actualchange_vs_abserror": float(np.corrcoef(actual_change60[sel], err60[sel])[0, 1]),
                                  "mae": float(np.mean(err60[sel]))}

    # (d) sports_match timing variability: how much does the exit-phase START tick vary relative to run length?
    timing = {}
    for et in F.EVENT_TYPES:
        starts = []
        for rid, run in runs.items():
            if run["event_type"] != et or run["scenario_type"] == "normal_baseline":
                continue
            phases = run["zones"]["venue_bowl"]["ticks"]
            main_start = next((int(r["tick"]) for r in phases if r["event_phase"] == "main"), None)
            exit_start = next((int(r["tick"]) for r in phases if r["event_phase"] == "exit"), None)
            if exit_start is not None:
                starts.append(exit_start / run["n_ticks"])
        if starts:
            timing[et] = {"n_runs": len(starts), "mean_exit_start_frac": float(np.mean(starts)), "std_exit_start_frac": float(np.std(starts))}

    return {
        "run_length_coverage": coverage,
        "mae_by_run_length_bucket": by_run_length,
        "movement_over_vs_under_prediction_by_scenario": by_scenario_movement,
        "corr_actual_volatility_vs_lstm_error": {"overall": corr_overall, "by_event_type": corr_by_event},
        "exit_phase_start_timing_variability_by_event_type": timing,
    }


def part3_gnn_alone(gnn_examples, gnn_model, runs):
    print("\n" + "=" * 80 + "\nPART 3 -- GNN alone (reconfirm + domain breakdown)\n" + "=" * 80)
    X0, LA, A, Y, M = TG.to_tensors(gnn_examples["test"])
    with torch.no_grad():
        pred = gnn_model(X0, LA, A).numpy()
    target, mask = Y.numpy(), M.numpy()
    meta = gnn_examples["test"]

    overall = TG.masked_mae_rmse(pred, target, mask)
    by_event = TG.eval_by_group(pred, target, mask, {"event_type": [e["event_type"] for e in meta]}, "event_type")
    by_scenario = TG.eval_by_group(pred, target, mask, {"scenario_type": [e["scenario_type"] for e in meta]}, "scenario_type")

    zone_domain = {z: runs[next(iter(runs))]["zones"][z]["domain"] for z in F.ZONE_ROLES}
    by_domain = {}
    for dom in sorted(set(zone_domain.values())):
        node_idx = [F.ZONE_INDEX[z] for z, d in zone_domain.items() if d == dom]
        dom_mask = np.zeros_like(mask)
        dom_mask[:, node_idx] = mask[:, node_idx]
        by_domain[dom] = TG.masked_mae_rmse(pred, target, dom_mask)

    nb_sel = np.array([s == "normal_baseline" for s in (e["scenario_type"] for e in meta)])
    nb_mask = mask * nb_sel[:, None]
    nb_bool = nb_mask.astype(bool)
    residual_pp = np.abs(pred - target) * 100.0
    n_nb = int(nb_bool.sum())
    n_flagged = int((residual_pp[nb_bool] > TG.ANOMALY_THRESHOLD_PP).sum()) if n_nb else 0

    heldout = TG.build_heldout_examples(runs, F.load_json(os.path.join(F.WEIGHTS_DIR, "gnn_split.json")))
    X0h, LAh, Ah, Yh, Mh = TG.to_tensors(heldout)
    with torch.no_grad():
        predh = gnn_model(X0h, LAh, Ah).numpy()
    heldout_metrics = TG.masked_mae_rmse(predh, Yh.numpy(), Mh.numpy())

    return {
        "overall_test": overall, "by_event_type_test": by_event, "by_scenario_type_test": by_scenario,
        "by_domain_test": by_domain,
        "normal_baseline_false_positive_rate": {"n": n_nb, "n_flagged": n_flagged, "rate": (n_flagged / n_nb if n_nb else None), "threshold_pp": TG.ANOMALY_THRESHOLD_PP},
        "held_out_structural_graph": {"removed_nodes": sorted(F.HELD_OUT_REMOVED_NODES), **heldout_metrics},
        "matches_step2b_saved_metrics": _compare_to_saved(overall, by_event, by_scenario, heldout_metrics),
    }


def _compare_to_saved(overall, by_event, by_scenario, heldout_metrics):
    try:
        saved = F.load_json(os.path.join(F.WEIGHTS_DIR, "gnn_metrics.json"))
    except FileNotFoundError:
        return "no saved gnn_metrics.json found to compare against"
    close = lambda a, b: a is not None and b is not None and abs(a - b) < 1e-4
    ok = (close(overall["mae"], saved["overall_test"]["mae"]) and
          close(heldout_metrics["mae"], saved["held_out_structural_graph"]["mae"]))
    return "MATCHES Step 2B saved metrics (same weights/test split, as expected)" if ok else f"DIFFERS from saved metrics -- saved={saved['overall_test']}, recomputed={overall}"


def part4_composition_experiment(gnn_examples, gnn_model, lstm_model, capacity_scaler, runs):
    print("\n" + "=" * 80 + "\nPART 4 -- GNN+LSTM composition experiment (this takes a bit: per-zone LSTM forecasts)\n" + "=" * 80)
    test_meta = gnn_examples["test"]
    neighbors = {z: set() for z in F.ZONE_ROLES}
    for a, b, _ in F.ZONE_EDGES:
        neighbors[a].add(b)
        neighbors[b].add(a)

    # cache per (run_id, t, zone) LSTM +15m forecast
    forecast_cache = {}

    def get_forecast(run, rid, t, zone_role):
        key = (rid, t, zone_role)
        if key in forecast_cache:
            return forecast_cache[key]
        zone = run["zones"][zone_role]
        series = F.zone_series(zone)
        fc = forecast_zone(lstm_model, capacity_scaler, run, zone["domain"], series, t, run["n_ticks"])["occupancy_pct_15m"]
        forecast_cache[key] = fc
        return fc

    neighbor_err_A_by_source = {z: [] for z in F.ZONE_ROLES}
    neighbor_err_B_by_source = {z: [] for z in F.ZONE_ROLES}

    with torch.no_grad():
        for ex in test_meta:
            rid, t = ex["run_id"], ex["tick"]
            run = runs[rid]
            x0 = torch.tensor(ex["x0"][None, ...], dtype=torch.float32)
            lag_agg = torch.tensor(ex["lag_agg"][None, ...], dtype=torch.float32)
            a_hat = torch.tensor(ex["a_hat"][None, ...], dtype=torch.float32)
            target = ex["target"]
            pred_A = gnn_model(x0, lag_agg, a_hat).numpy()[0]

            for src in F.ZONE_ROLES:
                nbrs = neighbors[src]
                if not nbrs:
                    continue
                fc = get_forecast(run, rid, t, src)
                x0_b = ex["x0"].copy()
                x0_b[F.ZONE_INDEX[src], 0] = fc / 100.0
                x0_bt = torch.tensor(x0_b[None, ...], dtype=torch.float32)
                pred_B = gnn_model(x0_bt, lag_agg, a_hat).numpy()[0]
                for nbr in nbrs:
                    ni = F.ZONE_INDEX[nbr]
                    err_a = abs(pred_A[ni] - target[ni]) * 100.0
                    err_b = abs(pred_B[ni] - target[ni]) * 100.0
                    neighbor_err_A_by_source[src].append(err_a)
                    neighbor_err_B_by_source[src].append(err_b)

    per_source = {}
    all_a, all_b = [], []
    for z in F.ZONE_ROLES:
        a_list, b_list = neighbor_err_A_by_source[z], neighbor_err_B_by_source[z]
        if not a_list:
            continue
        per_source[z] = {"n_neighbor_predictions": len(a_list), "mae_A_current_telemetry": float(np.mean(a_list)), "mae_B_lstm_forecast_substituted": float(np.mean(b_list))}
        all_a.extend(a_list)
        all_b.extend(b_list)

    overall = {"n": len(all_a), "mae_A_current_telemetry": float(np.mean(all_a)), "mae_B_lstm_forecast_substituted": float(np.mean(all_b)),
               "B_better_than_A": bool(np.mean(all_b) < np.mean(all_a))}
    return {"per_source_zone": per_source, "overall": overall,
            "note": "A = GNN's native trained-on input (current real telemetry). B = one source zone's occupancy channel replaced with its LSTM +15m forecast, rest unchanged. Measures accuracy on B's/A's prediction of the SOURCE zone's direct graph NEIGHBORS' next-tick occupancy (not the source zone itself)."}


def part5_run(gnn_examples, runs):
    print("\n" + "=" * 80 + "\nPART 5 -- is the GNN result trivial? (simple deterministic baseline)\n" + "=" * 80)
    meta = gnn_examples["test"]
    lag_by_pair = {frozenset({a, b}): lag for a, b, lag in F.ZONE_EDGES}
    upstream_of = {z: [] for z in F.ZONE_ROLES}
    for a, b, lag in F.ZONE_EDGES:
        upstream_of[a].append((b, lag))
        upstream_of[b].append((a, lag))

    series_cache = {}

    def get_series(rid, zone_role):
        key = (rid, zone_role)
        if key not in series_cache:
            series_cache[key] = F.zone_series(runs[rid]["zones"][zone_role])
        return series_cache[key]

    mom_errs, graph_errs, gnn_errs_same_pop = [], [], []
    for ex in meta:
        rid, t = ex["run_id"], ex["tick"]
        target, target_mask = ex["target"], ex["target_mask"]
        for zi, z in enumerate(F.ZONE_ROLES):
            if target_mask[zi] == 0:
                continue
            s = get_series(rid, z)
            occ_t, occ_t1_actual = s["occ"][t], target[zi] * 100.0
            occ_tm1 = s["occ"][t - 1]
            momentum_pred = occ_t + (occ_t - occ_tm1)  # naive linear extrapolation, no graph info

            graph_term = 0.0
            n_terms = 0
            for nbr, lag in upstream_of[z]:
                ns = get_series(rid, nbr)
                ta = t - lag
                if ta - 1 < 0:
                    continue
                nbr_delta = ns["occ"][ta] - ns["occ"][ta - 1]
                graph_term += nbr_delta
                n_terms += 1
            graph_term = 0.3 * (graph_term / n_terms) if n_terms else 0.0
            graph_pred = momentum_pred + graph_term

            mom_errs.append(abs(momentum_pred - occ_t1_actual))
            graph_errs.append(abs(graph_pred - occ_t1_actual))

    return {
        "n": len(mom_errs),
        "baseline_momentum_only_mae": float(np.mean(mom_errs)),
        "baseline_graph_lag_aware_mae": float(np.mean(graph_errs)),
        "formula_momentum": "pred(t+1) = occ(t) + (occ(t) - occ(t-1))",
        "formula_graph_lag_aware": "pred(t+1) = momentum_pred + 0.3 * mean_over_upstream_neighbors( occ(neighbor, t-lag) - occ(neighbor, t-lag-1) )  -- hand-specified coefficient 0.3, not fit",
    }


def main():
    t0 = time.time()
    print("Loading runs + saved split + models...")
    runs = F.load_runs()
    split = F.load_json(os.path.join(F.WEIGHTS_DIR, "lstm_split.json"))
    gnn_split = F.load_json(os.path.join(F.WEIGHTS_DIR, "gnn_split.json"))
    assert split == gnn_split, "lstm_split.json and gnn_split.json differ -- test sets would not be comparable"

    lstm_model, capacity_scaler = load_lstm()
    gnn_model, gnn_cfg = load_gnn()

    print("Rebuilding LSTM test examples (identical construction to train_lstm.py)...")
    lstm_examples = TL.build_examples(runs, split, capacity_scaler)
    print({k: len(v) for k, v in lstm_examples.items()})

    print("Rebuilding GNN test examples (identical construction to train_gnn.py)...")
    gnn_examples = TG.build_examples(runs, split)
    print({k: len(v) for k, v in gnn_examples.items()})

    report = {}
    report["part1_pressure_event_detection"] = part1_pressure_detection(lstm_examples, lstm_model)
    report["part2_60m_diagnosis"] = part2_diagnose_60m(lstm_examples, lstm_model, runs, split)
    report["part3_gnn_alone"] = part3_gnn_alone(gnn_examples, gnn_model, runs)
    report["part4_composition_experiment"] = part4_composition_experiment(gnn_examples, gnn_model, lstm_model, capacity_scaler, runs)
    report["part5_deterministic_baseline"] = part5_run(gnn_examples, runs)

    F.save_json(report, REPORT_PATH)
    print(f"\nSaved full report -> {REPORT_PATH}")
    print(f"Done in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    os.chdir(os.path.dirname(__file__))
    main()
