"""Evaluation-only calibration/gating experiment on top of the EXISTING
trained LSTM (backend/ml/weights/lstm.pt). Does NOT retrain or modify the
LSTM/GNN, does NOT touch the live app. Targets the exact failure mode found
in Step 2B.1 Part 2: on calm zones, the LSTM predicts phantom upward drift,
which crosses the pressure threshold and fires a false "incoming pressure"
alert. Three gates are tested; none change the model, only whether its
threshold-crossing alert is SUPPRESSED for a given example.

Thresholds are derived from the TRAIN split only (never test), via a small
transparent grid -- every value tried is reported, not just the winner.

Run: python backend/ml/gate_experiment.py
Output: printed to stdout + backend/ml/weights/gate_experiment_report.json
"""
import os
import time

import numpy as np
import torch

import features as F
import train_lstm as TL
from validate_step2b1 import confusion, THRESHOLDS, DISRUPTION_SCENARIOS
from inference import load_lstm

SEQ_LEN, HORIZONS, STRIDE = TL.SEQ_LEN, TL.HORIZONS, TL.STRIDE
REPORT_PATH = os.path.join(F.WEIGHTS_DIR, "gate_experiment_report.json")

# Gate window/margin definitions -- fixed, documented here, not tuned:
CALM_WINDOW_A = 3   # ticks: "recent" = last 3 consecutive deltas (last 4 ticks incl. anchor)
CALM_WINDOW_B = 6   # ticks: "sustained" = last 6 consecutive deltas (last 7 ticks) -- same threshold, longer required window
ACCEL_MARGIN_PP = 1.0  # pp: Gate C's "consistent acceleration" = each horizon step >= this much higher than the last


def build_examples_with_tick(runs, split, capacity_scaler):
    """Same construction as train_lstm.build_examples, plus the anchor tick
    (needed here to report concrete false-suppression examples; train_lstm's
    version doesn't keep it since it never needed to)."""
    examples = {"train": [], "val": [], "test": []}
    for rid, run in runs.items():
        n_ticks = run["n_ticks"]
        split_name = split[rid]
        for zone_role in F.ZONE_ROLES:
            zone = run["zones"][zone_role]
            series = F.zone_series(zone)
            domain = zone["domain"]
            for t in range(SEQ_LEN - 1, n_ticks, STRIDE):
                seq = [F.lstm_feature_vector(run, domain, series, tt, n_ticks, capacity_scaler) for tt in range(t - SEQ_LEN + 1, t + 1)]
                target, mask = [], []
                for h in HORIZONS:
                    tt = t + h
                    if tt <= n_ticks - 1:
                        target.append(series["occ"][tt] / 100.0)
                        mask.append(1.0)
                    else:
                        target.append(0.0)
                        mask.append(0.0)
                examples[split_name].append({
                    "seq": seq, "target": target, "mask": mask, "tick": t,
                    "event_type": run["event_type"], "scenario_type": run["scenario_type"],
                    "domain": domain, "zone_role": zone_role, "run_id": rid,
                })
    return examples


def recent_movement(occ_seq_pct, window):
    """occ_seq_pct: (N,12) occupancy pct across the 12-tick input window.
    Returns (N,) max absolute single-tick delta over the last `window` deltas."""
    deltas = occ_seq_pct[:, 1:] - occ_seq_pct[:, :-1]
    recent = deltas[:, -window:]
    return np.max(np.abs(recent), axis=1)


def alert_matrix(pred_pct, cur_pct, thr):
    """pred_pct/cur_pct: (N,3). Returns (N,3) bool -- would-alert (predicted crosses thr from at/below it)."""
    return (pred_pct > thr) & (cur_pct[:, None] <= thr)


def derive_thresholds(train_ex, lstm_model):
    """Small transparent grid search on TRAIN only. Objective: maximize
    normal_baseline FPR reduction (threshold 55, all horizons pooled) subject
    to disruption-pooled recall dropping by no more than RECALL_TOLERANCE
    (documented). Uses Gate A's definition as the search proxy; the winning
    (calm_threshold, meaningful_threshold) pair is then reused, unchanged, for
    Gates A/B/C alike -- only the structural condition differs between gates."""
    RECALL_TOLERANCE = 0.03  # 3 percentage points, documented, not tuned away from
    X, Y, M = TL.to_tensors(train_ex)
    with torch.no_grad():
        pred = lstm_model(X).numpy()
    occ_seq_pct = (X[:, :, 0] * 100.0).numpy()
    cur_pct = occ_seq_pct[:, -1]
    act_pct = Y.numpy() * 100.0
    pred_pct = pred * 100.0
    mask = M.numpy().astype(bool)
    scenario = np.array([e["scenario_type"] for e in train_ex])
    nb_sel = scenario == "normal_baseline"
    dis_sel = np.isin(scenario, DISRUPTION_SCENARIOS)

    nb_deltas3 = recent_movement(occ_seq_pct[nb_sel], CALM_WINDOW_A)
    calm_candidates = sorted(set(round(float(np.percentile(nb_deltas3, p)), 3) for p in (75, 85, 90, 95)))
    meaningful_candidates = [5.0, 8.0, 10.0, 15.0, 20.0]

    thr55 = THRESHOLDS["pressure_55"]
    alert_all = alert_matrix(pred_pct, cur_pct, thr55)  # (N,3)
    grid = []
    best = None
    for calm_thr in calm_candidates:
        move3_all = recent_movement(occ_seq_pct, CALM_WINDOW_A)
        calm_all = move3_all <= calm_thr
        for meaningful_thr in meaningful_candidates:
            meaningful_all = (pred_pct - cur_pct[:, None]) > meaningful_thr  # (N,3)
            suppress = calm_all[:, None] & meaningful_all  # Gate A logic
            gated_alert = alert_all & ~suppress

            # normal_baseline FPR (pooled horizons, eligible only, i.e. mask & cur<=thr which alert_matrix already encodes via cur_pct<=thr; here "eligible" is any mask==1 row since alert_matrix already zeros out non-crossing rows)
            nb_m = mask[nb_sel] & (cur_pct[nb_sel, None] <= thr55)
            nb_before = alert_all[nb_sel][nb_m]
            nb_after = gated_alert[nb_sel][nb_m]
            fpr_before = float(nb_before.mean()) if nb_before.size else None
            fpr_after = float(nb_after.mean()) if nb_after.size else None

            dis_m = mask[dis_sel] & (cur_pct[dis_sel, None] <= thr55)
            act_dis = act_pct[dis_sel][dis_m] > thr55
            before_dis = alert_all[dis_sel][dis_m]
            after_dis = gated_alert[dis_sel][dis_m]
            rec_before = confusion(act_dis, before_dis)["recall"]
            rec_after = confusion(act_dis, after_dis)["recall"]
            recall_drop = (rec_before - rec_after) if (rec_before is not None and rec_after is not None) else None
            fpr_reduction = (fpr_before - fpr_after) if (fpr_before is not None and fpr_after is not None) else None

            row = {"calm_threshold_pp": calm_thr, "meaningful_threshold_pp": meaningful_thr,
                   "train_normal_baseline_fpr_before": fpr_before, "train_normal_baseline_fpr_after": fpr_after,
                   "train_fpr_reduction": fpr_reduction,
                   "train_disruption_recall_before": rec_before, "train_disruption_recall_after": rec_after,
                   "train_recall_drop": recall_drop}
            grid.append(row)
            if fpr_reduction is not None and recall_drop is not None and recall_drop <= RECALL_TOLERANCE:
                if best is None or fpr_reduction > best["train_fpr_reduction"]:
                    best = row
    if best is None:  # nothing met the tolerance -- fall back to the combo with the smallest recall drop
        best = min(grid, key=lambda r: (r["train_recall_drop"] if r["train_recall_drop"] is not None else 1.0))
    return best["calm_threshold_pp"], best["meaningful_threshold_pp"], grid, RECALL_TOLERANCE


def gate_suppression(occ_seq_pct, pred_pct, calm_thr, meaningful_thr, mask):
    """Returns dict gate_name -> (N,3) bool suppress-mask."""
    cur_pct = occ_seq_pct[:, -1]
    meaningful = (pred_pct - cur_pct[:, None]) > meaningful_thr
    calm_A = recent_movement(occ_seq_pct, CALM_WINDOW_A) <= calm_thr
    calm_B = recent_movement(occ_seq_pct, CALM_WINDOW_B) <= calm_thr

    all3 = mask.all(axis=1)
    p15, p30, p60 = pred_pct[:, 0], pred_pct[:, 1], pred_pct[:, 2]
    consistent_accel = (p30 - p15 >= ACCEL_MARGIN_PP) & (p60 - p30 >= ACCEL_MARGIN_PP)
    gate_C_condition = calm_A[:, None] & meaningful & (~consistent_accel)[:, None]
    gate_C_condition = gate_C_condition & all3[:, None]  # undefined -> no suppression when not all 3 horizons exist

    return {
        "gate_A_strict_calm": calm_A[:, None] & meaningful,
        "gate_B_sustained_calm": calm_B[:, None] & meaningful,
        "gate_C_calm_plus_consistency": gate_C_condition,
    }


def evaluate_gate(gate_name, suppress, pred_pct, act_pct, cur_pct, mask, scenario, thr, ungated_alert):
    gated_alert = ungated_alert & ~suppress
    slices = {"overall": np.ones(len(cur_pct), bool), "normal_baseline": scenario == "normal_baseline",
              "disruption_pooled": np.isin(scenario, DISRUPTION_SCENARIOS)}
    for s in DISRUPTION_SCENARIOS:
        slices[s] = scenario == s

    out = {}
    for hi, h in enumerate(HORIZONS):
        m_h = mask[:, hi].astype(bool)
        cur_h, act_h = cur_pct, act_pct[:, hi]
        eligible = (cur_h <= thr) & m_h
        per_h = {}
        for slice_name, sel in slices.items():
            sel_e = sel & eligible
            n = int(sel_e.sum())
            if n == 0:
                per_h[slice_name] = {"n": 0}
                continue
            a = act_h[sel_e] > thr
            g = gated_alert[sel_e, hi]
            u = ungated_alert[sel_e, hi]
            c_g, c_u = confusion(a, g), confusion(a, u)
            n_suppressed_tp = int((u & ~g & a).sum())  # was correctly alerting (TP), now suppressed
            per_h[slice_name] = {
                "n": n, "gated": c_g, "ungated": c_u,
                "abs_fpr_reduction": (c_u["fpr"] - c_g["fpr"]) if (c_u["fpr"] is not None and c_g["fpr"] is not None) else None,
                "pct_fpr_reduction": ((c_u["fpr"] - c_g["fpr"]) / c_u["fpr"] * 100.0) if (c_u["fpr"] not in (None, 0) and c_g["fpr"] is not None) else None,
                "recall_change": (c_g["recall"] - c_u["recall"]) if (c_g["recall"] is not None and c_u["recall"] is not None) else None,
                "f1_change": (c_g["f1"] - c_u["f1"]) if (c_g["f1"] is not None and c_u["f1"] is not None) else None,
                "n_true_positives_suppressed": n_suppressed_tp,
            }
        out[f"+{h}m"] = per_h
    return out


def phantom_drift_analysis(pred_pct, act_pct, cur_pct, mask, scenario, run_ids, zone_roles, ticks, suppression_by_gate, thr):
    hi60 = HORIZONS.index(60)
    m = mask[:, hi60].astype(bool) & (cur_pct <= thr) & (scenario == "normal_baseline")
    is_fp = m & (pred_pct[:, hi60] > thr) & (act_pct[:, hi60] <= thr)
    idx = np.where(is_fp)[0]
    examples = [{"run_id": run_ids[i], "zone_role": zone_roles[i], "tick": int(ticks[i]),
                 "current_pct": round(float(cur_pct[i]), 2), "predicted_60m_pct": round(float(pred_pct[i, hi60]), 2),
                 "actual_60m_pct": round(float(act_pct[i, hi60]), 2)} for i in idx]
    removed_by_gate = {}
    for gate_name, sup in suppression_by_gate.items():
        removed = sup[idx, hi60] if len(idx) else np.array([], bool)
        removed_by_gate[gate_name] = {"n_total_phantom_fps": len(idx), "n_removed": int(removed.sum()),
                                        "pct_removed": float(removed.mean() * 100.0) if len(idx) else None}
    return {"n_phantom_fps_at_60m_normal_baseline": len(idx), "example_cases": examples[:15], "removed_by_gate": removed_by_gate}


def false_suppression_analysis(pred_pct, act_pct, cur_pct, mask, scenario, run_ids, zone_roles, ticks, suppression_by_gate, thr):
    out = {}
    for gate_name, sup in suppression_by_gate.items():
        cases = []
        for hi, h in enumerate(HORIZONS):
            m = mask[:, hi].astype(bool) & (cur_pct <= thr) & np.isin(scenario, DISRUPTION_SCENARIOS)
            was_tp = m & (pred_pct[:, hi] > thr) & (act_pct[:, hi] > thr)
            now_suppressed = was_tp & sup[:, hi]
            idx = np.where(now_suppressed)[0]
            for i in idx[:10]:
                cases.append({"horizon": f"+{h}m", "run_id": run_ids[i], "zone_role": zone_roles[i], "tick": int(ticks[i]),
                              "scenario_type": scenario[i], "current_pct": round(float(cur_pct[i]), 2),
                              "predicted_pct": round(float(pred_pct[i, hi]), 2), "actual_pct": round(float(act_pct[i, hi]), 2)})
        out[gate_name] = {"n_false_suppressions_all_horizons": len(cases), "example_cases": cases[:15]}
    return out


def main():
    t0 = time.time()
    os.chdir(os.path.dirname(__file__))
    print("Loading runs + saved split + LSTM...")
    runs = F.load_runs()
    split = F.load_json(os.path.join(F.WEIGHTS_DIR, "lstm_split.json"))
    lstm_model, capacity_scaler = load_lstm()

    print("Building examples (train, for threshold derivation; test, for evaluation)...")
    examples = build_examples_with_tick(runs, split, capacity_scaler)
    print({k: len(v) for k, v in examples.items()})

    print("Deriving calm/meaningful thresholds from TRAIN only (grid search)...")
    calm_thr, meaningful_thr, grid, tolerance = derive_thresholds(examples["train"], lstm_model)
    print(f"Chosen: calm_threshold={calm_thr}pp (max delta over last {CALM_WINDOW_A} ticks), "
          f"meaningful_threshold={meaningful_thr}pp, recall_tolerance={tolerance}")

    print("Evaluating on TEST...")
    test_ex = examples["test"]
    X, Y, M = TL.to_tensors(test_ex)
    with torch.no_grad():
        pred = lstm_model(X).numpy()
    occ_seq_pct = (X[:, :, 0] * 100.0).numpy()
    cur_pct = occ_seq_pct[:, -1]
    act_pct = Y.numpy() * 100.0
    pred_pct = pred * 100.0
    mask = M.numpy()
    scenario = np.array([e["scenario_type"] for e in test_ex])
    run_ids = np.array([e["run_id"] for e in test_ex])
    zone_roles = np.array([e["zone_role"] for e in test_ex])
    ticks = np.array([e["tick"] for e in test_ex])

    suppression = gate_suppression(occ_seq_pct, pred_pct, calm_thr, meaningful_thr, mask.astype(bool))

    report = {
        "gate_definitions": {
            "calm_threshold_pp": calm_thr, "meaningful_threshold_pp": meaningful_thr,
            "calm_window_A_ticks": CALM_WINDOW_A, "calm_window_B_ticks": CALM_WINDOW_B,
            "accel_margin_pp_gate_C": ACCEL_MARGIN_PP, "recall_tolerance_used_for_derivation": tolerance,
            "gate_A_rule": f"suppress if max(|delta|, last {CALM_WINDOW_A} ticks) <= {calm_thr}pp AND (predicted-current) > {meaningful_thr}pp",
            "gate_B_rule": f"suppress if max(|delta|, last {CALM_WINDOW_B} ticks) <= {calm_thr}pp AND (predicted-current) > {meaningful_thr}pp",
            "gate_C_rule": f"suppress if Gate-A calm AND (predicted-current) > {meaningful_thr}pp AND NOT(p30-p15>={ACCEL_MARGIN_PP} AND p60-p30>={ACCEL_MARGIN_PP}); only defined where all 3 horizons exist, otherwise behaves as ungated",
        },
        "threshold_grid_searched_on_train": grid,
        "results_by_threshold": {},
    }

    for thr_name, thr in THRESHOLDS.items():
        ungated_alert = alert_matrix(pred_pct, cur_pct, thr)
        by_gate = {}
        for gate_name, sup in suppression.items():
            by_gate[gate_name] = evaluate_gate(gate_name, sup, pred_pct, act_pct, cur_pct, mask, scenario, thr, ungated_alert)
        # ungated-as-baseline row too (gate = never suppress)
        by_gate["ungated"] = evaluate_gate("ungated", np.zeros_like(ungated_alert, bool), pred_pct, act_pct, cur_pct, mask, scenario, thr, ungated_alert)
        report["results_by_threshold"][thr_name] = by_gate

    report["phantom_drift_analysis"] = phantom_drift_analysis(
        pred_pct, act_pct, cur_pct, mask.astype(bool), scenario, run_ids, zone_roles, ticks, suppression, THRESHOLDS["pressure_55"])
    report["false_suppression_analysis"] = false_suppression_analysis(
        pred_pct, act_pct, cur_pct, mask.astype(bool), scenario, run_ids, zone_roles, ticks, suppression, THRESHOLDS["pressure_55"])

    F.save_json(report, REPORT_PATH)
    print(f"\nSaved -> {REPORT_PATH}")
    print(f"Done in {time.time()-t0:.1f}s")

    # compact console summary
    print("\n=== SUMMARY (threshold 55, overall, pooled horizons via +30m as representative) ===")
    for gate_name in ["ungated", "gate_A_strict_calm", "gate_B_sustained_calm", "gate_C_calm_plus_consistency"]:
        r = report["results_by_threshold"]["pressure_55"][gate_name]["+30m"]["overall"]
        c = r.get("gated", r.get("ungated"))
        print(f"  {gate_name}: n={r['n']} precision={c['precision']} recall={c['recall']} f1={c['f1']} fpr={c['fpr']}")

    print("\n=== Phantom-drift removal (60m, normal_baseline) ===")
    for g, v in report["phantom_drift_analysis"]["removed_by_gate"].items():
        print(f"  {g}: removed {v['n_removed']}/{v['n_total_phantom_fps']} ({v['pct_removed']}%)")

    print("\n=== False suppressions (disruption TPs suppressed, all horizons) ===")
    for g, v in report["false_suppression_analysis"].items():
        print(f"  {g}: {v['n_false_suppressions_all_horizons']}")


if __name__ == "__main__":
    main()
