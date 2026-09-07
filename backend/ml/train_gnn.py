"""Train the inductive GNN flow-propagation model on the Step 1 dataset.

Run: python backend/ml/train_gnn.py
Output: backend/ml/weights/gnn.pt, gnn_split.json, gnn_feature_config.json,
        gnn_metrics.json

Two distinct evaluations, kept explicitly separate (see report at the end):
  - OFFLINE: predicted next-tick occupancy vs ACTUAL next-tick occupancy
    (this IS the training signal and the reported MAE/RMSE below).
  - LIVE (see inference.py): no actual future occupancy is ever available at
    that point -- this script does not implement that path, only trains the
    model the live path will load.
"""
import os
import random
import time

import numpy as np
import torch
import torch.nn as nn

import features as F
from gnn_model import GNNForecaster

SEED = F.SEED
SEQ_LEN = 12          # anchor tick t >= 12, same lookback convention as the LSTM
STRIDE = 2
EPOCHS = 15
LR = 5e-3
KEEP_PROB = 0.8        # per-node keep probability during TRAIN subgraph sampling
MIN_KEPT_NODES = 5
EDGE_DROPOUT = 0.15     # TRAIN-only, independent per remaining edge
ANOMALY_THRESHOLD_PP = 10.0  # percentage points; used only for the normal_baseline false-positive check
WEIGHTS_DIR = F.WEIGHTS_DIR

N = F.N_ZONES


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def build_node_history(run):
    """(n_ticks, N, FEATURE_DIM_GNN) array for all 9 zones of one run."""
    n_ticks = run["n_ticks"]
    hist = np.zeros((n_ticks, N, F.FEATURE_DIM_GNN), dtype=np.float32)
    for zone_role in F.ZONE_ROLES:
        zone = run["zones"][zone_role]
        series = F.zone_series(zone)
        idx = F.ZONE_INDEX[zone_role]
        for t in range(n_ticks):
            hist[t, idx] = F.gnn_node_feature_vector(zone["domain"], series, t)
    return hist


def sample_kept_nodes(rng):
    while True:
        mask = rng.random(N) < KEEP_PROB
        if mask.sum() >= MIN_KEPT_NODES:
            return mask


def build_example(hist, t, kept_mask, drop_edges, rng=None):
    """kept_mask: (N,) bool. drop_edges: whether to apply EDGE_DROPOUT (train only, needs rng)."""
    self_feats = hist[t].copy()
    self_feats[~kept_mask] = 0.0

    lag_agg = np.zeros((N, F.FEATURE_DIM_GNN), dtype=np.float32)
    degree_lag = np.zeros(N, dtype=np.float32)
    adjacency = np.zeros((N, N), dtype=np.float32)

    for a, b, lag in F.ZONE_EDGES:
        ia, ib = F.ZONE_INDEX[a], F.ZONE_INDEX[b]
        if not (kept_mask[ia] and kept_mask[ib]):
            continue  # induced subgraph only: drop incident edges of a removed node, never reconnect
        if drop_edges and rng.random() < EDGE_DROPOUT:
            continue
        ta = t - lag
        lag_agg[ia] += hist[ta, ib]
        lag_agg[ib] += hist[ta, ia]
        degree_lag[ia] += 1
        degree_lag[ib] += 1
        adjacency[ia, ib] = 1.0
        adjacency[ib, ia] = 1.0

    nz = degree_lag > 0
    lag_agg[nz] /= degree_lag[nz, None]
    row_sum = adjacency.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0] = 1.0
    a_hat = adjacency / row_sum

    target = hist[t + 1, :, 0].copy()  # occ/100 channel
    target_mask = kept_mask.astype(np.float32)
    return self_feats, lag_agg, a_hat, target, target_mask


def build_examples(runs, split):
    examples = {"train": [], "val": [], "test": []}
    rng = np.random.default_rng(SEED)
    for rid, run in runs.items():
        n_ticks = run["n_ticks"]
        split_name = split[rid]
        hist = build_node_history(run)
        for t in range(SEQ_LEN, n_ticks - 1, STRIDE):
            if split_name == "train":
                kept_mask = sample_kept_nodes(rng)
                x0, lag_agg, a_hat, target, target_mask = build_example(hist, t, kept_mask, drop_edges=True, rng=rng)
            else:
                kept_mask = np.ones(N, dtype=bool)
                x0, lag_agg, a_hat, target, target_mask = build_example(hist, t, kept_mask, drop_edges=False)
            examples[split_name].append({
                "x0": x0, "lag_agg": lag_agg, "a_hat": a_hat, "target": target, "target_mask": target_mask,
                "event_type": run["event_type"], "scenario_type": run["scenario_type"], "run_id": rid, "tick": t,
            })
    return examples


def build_heldout_examples(runs, split):
    """Deterministic held-out structural graph (2 fixed nodes removed) --
    NEVER used in training/tuning, only this final generalization check, and
    only over test-split runs."""
    kept_mask = np.array([z not in F.HELD_OUT_REMOVED_NODES for z in F.ZONE_ROLES])
    out = []
    for rid, run in runs.items():
        if split[rid] != "test":
            continue
        n_ticks = run["n_ticks"]
        hist = build_node_history(run)
        for t in range(SEQ_LEN, n_ticks - 1, STRIDE):
            x0, lag_agg, a_hat, target, target_mask = build_example(hist, t, kept_mask, drop_edges=False)
            out.append({
                "x0": x0, "lag_agg": lag_agg, "a_hat": a_hat, "target": target, "target_mask": target_mask,
                "event_type": run["event_type"], "scenario_type": run["scenario_type"], "run_id": rid, "tick": t,
            })
    return out


def to_tensors(examples):
    X0 = torch.tensor(np.stack([e["x0"] for e in examples]), dtype=torch.float32)
    LA = torch.tensor(np.stack([e["lag_agg"] for e in examples]), dtype=torch.float32)
    A = torch.tensor(np.stack([e["a_hat"] for e in examples]), dtype=torch.float32)
    Y = torch.tensor(np.stack([e["target"] for e in examples]), dtype=torch.float32)
    M = torch.tensor(np.stack([e["target_mask"] for e in examples]), dtype=torch.float32)
    return X0, LA, A, Y, M


def masked_mse(pred, target, mask):
    sq = (pred - target) ** 2 * mask
    denom = mask.sum().clamp(min=1.0)
    return sq.sum() / denom


def masked_mae_rmse(pred, target, mask):
    m = mask.astype(bool)
    n = int(m.sum())
    if n == 0:
        return {"n": 0, "mae": None, "rmse": None}
    err = (pred[m] - target[m]) * 100.0
    return {"n": n, "mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2)))}


def eval_by_group(pred, target, mask, meta, key):
    out = {}
    for v in sorted(set(meta[key])):
        sel = np.array([mv == v for mv in meta[key]])
        sel_mask = mask * sel[:, None]
        out[v] = masked_mae_rmse(pred, target, sel_mask)
    return out


def main():
    set_seed(SEED)
    t0 = time.time()
    print("Loading runs...")
    runs = F.load_runs()
    split = F.stratified_run_split(runs, seed=SEED)

    print("Building examples (this builds a full node-history array per run, takes a bit)...")
    examples = build_examples(runs, split)
    for k, v in examples.items():
        print(f"  {k}: {len(v)} examples")

    X0_tr, LA_tr, A_tr, Y_tr, M_tr = to_tensors(examples["train"])
    X0_va, LA_va, A_va, Y_va, M_va = to_tensors(examples["val"])
    X0_te, LA_te, A_te, Y_te, M_te = to_tensors(examples["test"])

    model = GNNForecaster(in_dim=F.FEATURE_DIM_GNN, hidden_dim=32)
    opt = torch.optim.Adam(model.parameters(), lr=LR)

    n_train = X0_tr.shape[0]
    batch_size = 128
    gen = torch.Generator().manual_seed(SEED)

    print("Training...")
    for epoch in range(EPOCHS):
        model.train()
        perm = torch.randperm(n_train, generator=gen)
        total_loss, n_batches = 0.0, 0
        for i in range(0, n_train, batch_size):
            idx = perm[i:i + batch_size]
            opt.zero_grad()
            pred = model(X0_tr[idx], LA_tr[idx], A_tr[idx])
            loss = masked_mse(pred, Y_tr[idx], M_tr[idx])
            loss.backward()
            opt.step()
            total_loss += loss.item()
            n_batches += 1
        model.eval()
        with torch.no_grad():
            val_pred = model(X0_va, LA_va, A_va)
            val_loss = masked_mse(val_pred, Y_va, M_va).item()
        print(f"epoch {epoch+1}/{EPOCHS}  train_loss={total_loss/n_batches:.5f}  val_loss={val_loss:.5f}")

    model.eval()
    with torch.no_grad():
        test_pred = model(X0_te, LA_te, A_te).numpy()
    test_target = Y_te.numpy()
    test_mask = M_te.numpy()
    test_meta = {
        "event_type": [e["event_type"] for e in examples["test"]],
        "scenario_type": [e["scenario_type"] for e in examples["test"]],
    }

    overall = masked_mae_rmse(test_pred, test_target, test_mask)
    by_event_type = eval_by_group(test_pred, test_target, test_mask, test_meta, "event_type")
    by_scenario = eval_by_group(test_pred, test_target, test_mask, test_meta, "scenario_type")

    # false-positive check: on normal_baseline test examples, anomaly = |actual-predicted| > threshold
    nb_sel = np.array([s == "normal_baseline" for s in test_meta["scenario_type"]])
    nb_mask = test_mask * nb_sel[:, None]
    nb_bool = nb_mask.astype(bool)
    residual_pp = np.abs(test_pred - test_target) * 100.0
    n_nb = int(nb_bool.sum())
    n_flagged = int((residual_pp[nb_bool] > ANOMALY_THRESHOLD_PP).sum()) if n_nb else 0
    false_positive_rate = n_flagged / n_nb if n_nb else None

    print("\nEvaluating on the deterministic held-out structural graph (test-split runs only)...")
    heldout_examples = build_heldout_examples(runs, split)
    X0_h, LA_h, A_h, Y_h, M_h = to_tensors(heldout_examples)
    with torch.no_grad():
        heldout_pred = model(X0_h, LA_h, A_h).numpy()
    heldout_overall = masked_mae_rmse(heldout_pred, Y_h.numpy(), M_h.numpy())

    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    torch.save(model.state_dict(), os.path.join(WEIGHTS_DIR, "gnn.pt"))
    F.save_json(split, os.path.join(WEIGHTS_DIR, "gnn_split.json"))
    F.save_json({
        "seq_len": SEQ_LEN, "stride": STRIDE, "in_dim": F.FEATURE_DIM_GNN, "hidden_dim": 32, "seed": SEED,
        "epochs": EPOCHS, "batch_size": batch_size, "lr": LR,
        "keep_prob": KEEP_PROB, "min_kept_nodes": MIN_KEPT_NODES, "edge_dropout": EDGE_DROPOUT,
        "held_out_removed_nodes": sorted(F.HELD_OUT_REMOVED_NODES),
        "anomaly_threshold_pp": ANOMALY_THRESHOLD_PP,
        "edges_with_lag": [[a, b, lag] for a, b, lag in F.ZONE_EDGES],
        "node_feature_order": ["occ/100", "entry_pct/100", "exit_pct/100", "domain_onehot(3)"],
        "generalization_claim": "Inductive feature-based message passing (no node-identity embeddings), trained/evaluated with structural augmentation (subgraph sampling + edge dropout) to reduce dependence on one fixed graph topology. This does NOT prove generalization to arbitrary real-world event graphs.",
    }, os.path.join(WEIGHTS_DIR, "gnn_feature_config.json"))
    F.save_json({
        "overall_test": overall,
        "by_event_type_test": by_event_type,
        "by_scenario_type_test": by_scenario,
        "normal_baseline_false_positive_rate": {"n_normal_baseline_examples": n_nb, "n_flagged": n_flagged, "rate": false_positive_rate, "threshold_pp": ANOMALY_THRESHOLD_PP},
        "held_out_structural_graph": {"removed_nodes": sorted(F.HELD_OUT_REMOVED_NODES), **heldout_overall},
    }, os.path.join(WEIGHTS_DIR, "gnn_metrics.json"))

    print(f"\nDone in {time.time()-t0:.1f}s")
    print("=== GNN overall (test, full graph) ===", overall)
    print("=== By event_type ===", by_event_type)
    print("=== By scenario_type ===", by_scenario)
    print(f"=== normal_baseline false-positive rate: {n_flagged}/{n_nb} = {false_positive_rate} (threshold {ANOMALY_THRESHOLD_PP}pp) ===")
    print("=== Held-out structural graph (nodes removed:", sorted(F.HELD_OUT_REMOVED_NODES), ") ===", heldout_overall)


if __name__ == "__main__":
    os.chdir(os.path.dirname(__file__))
    main()
