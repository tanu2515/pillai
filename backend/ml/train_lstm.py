"""Train the shared-weight LSTM crowd forecaster on the Step 1 dataset.

Run: python backend/ml/train_lstm.py
Output: backend/ml/weights/lstm.pt, lstm_scaler.json, lstm_split.json,
        lstm_feature_config.json, lstm_metrics.json
"""
import json
import os
import random
import time

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

import features as F
from lstm_model import LSTMForecaster

SEED = F.SEED
SEQ_LEN = 12
HORIZONS = [15, 30, 60]
STRIDE = 2
EPOCHS = 10
BATCH_SIZE = 256
LR = 1e-3
WEIGHTS_DIR = F.WEIGHTS_DIR


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def build_examples(runs, split, capacity_scaler):
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
                    "seq": seq, "target": target, "mask": mask,
                    "event_type": run["event_type"], "scenario_type": run["scenario_type"],
                    "domain": domain, "zone_role": zone_role, "run_id": rid,
                })
    return examples


def to_tensors(examples):
    X = torch.tensor([e["seq"] for e in examples], dtype=torch.float32)
    Y = torch.tensor([e["target"] for e in examples], dtype=torch.float32)
    M = torch.tensor([e["mask"] for e in examples], dtype=torch.float32)
    return X, Y, M


def masked_mse(pred, target, mask):
    sq = (pred - target) ** 2 * mask
    denom = mask.sum().clamp(min=1.0)
    return sq.sum() / denom


def evaluate(pred, target, mask, meta, group_keys=()):
    """pred/target/mask: (N,3) numpy arrays in 0-1 occupancy scale.
    Returns overall + grouped (by group_keys, e.g. 'event_type') MAE/RMSE
    tables, in occupancy-PCT POINTS (i.e. *100), plus sample counts."""
    result = {"overall": {}, "by_horizon": {}}
    for hi, h in enumerate(HORIZONS):
        m = mask[:, hi].astype(bool)
        n = int(m.sum())
        if n == 0:
            result["by_horizon"][f"+{h}m"] = {"n": 0, "mae": None, "rmse": None}
            continue
        err = (pred[m, hi] - target[m, hi]) * 100.0
        result["by_horizon"][f"+{h}m"] = {"n": n, "mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2)))}
    all_m = mask.astype(bool)
    n_all = int(all_m.sum())
    err_all = (pred[all_m] - target[all_m]) * 100.0
    result["overall"] = {"n": n_all, "mae": float(np.mean(np.abs(err_all))), "rmse": float(np.sqrt(np.mean(err_all ** 2)))}

    for key in group_keys:
        groups = {}
        values = sorted(set(meta[key]))
        for v in values:
            sel = np.array([mv == v for mv in meta[key]])
            per_h = {}
            for hi, h in enumerate(HORIZONS):
                m = sel & mask[:, hi].astype(bool)
                n = int(m.sum())
                if n == 0:
                    per_h[f"+{h}m"] = {"n": 0, "mae": None, "rmse": None}
                    continue
                err = (pred[m, hi] - target[m, hi]) * 100.0
                per_h[f"+{h}m"] = {"n": n, "mae": float(np.mean(np.abs(err))), "rmse": float(np.sqrt(np.mean(err ** 2)))}
            groups[v] = per_h
        result[f"by_{key}"] = groups
    return result


def main():
    set_seed(SEED)
    t0 = time.time()
    print("Loading runs...")
    runs = F.load_runs()
    split = F.stratified_run_split(runs, seed=SEED)
    split_counts = {"train": 0, "val": 0, "test": 0}
    for v in split.values():
        split_counts[v] += 1
    print("Run split:", split_counts)

    capacity_scaler, cap_cfg = F.fit_capacity_scaler(runs, split, "train")

    print("Building examples...")
    examples = build_examples(runs, split, capacity_scaler)
    for k, v in examples.items():
        print(f"  {k}: {len(v)} examples")

    X_train, Y_train, M_train = to_tensors(examples["train"])
    X_val, Y_val, M_val = to_tensors(examples["val"])
    X_test, Y_test, M_test = to_tensors(examples["test"])

    model = LSTMForecaster(input_dim=F.FEATURE_DIM_LSTM, hidden_dim=64, num_layers=1)
    opt = torch.optim.Adam(model.parameters(), lr=LR)

    train_loader = DataLoader(TensorDataset(X_train, Y_train, M_train), batch_size=BATCH_SIZE, shuffle=True, generator=torch.Generator().manual_seed(SEED))

    print("Training...")
    for epoch in range(EPOCHS):
        model.train()
        total_loss, n_batches = 0.0, 0
        for xb, yb, mb in train_loader:
            opt.zero_grad()
            pred = model(xb)
            loss = masked_mse(pred, yb, mb)
            loss.backward()
            opt.step()
            total_loss += loss.item()
            n_batches += 1
        model.eval()
        with torch.no_grad():
            val_pred = model(X_val)
            val_loss = masked_mse(val_pred, Y_val, M_val).item()
        print(f"epoch {epoch+1}/{EPOCHS}  train_loss={total_loss/n_batches:.5f}  val_loss={val_loss:.5f}")

    model.eval()
    with torch.no_grad():
        test_pred = model(X_test).numpy()
    test_target = Y_test.numpy()
    test_mask = M_test.numpy()
    test_meta = {
        "event_type": [e["event_type"] for e in examples["test"]],
        "scenario_type": [e["scenario_type"] for e in examples["test"]],
        "domain": [e["domain"] for e in examples["test"]],
    }

    lstm_metrics = evaluate(test_pred, test_target, test_mask, test_meta, group_keys=["event_type", "scenario_type", "domain"])

    # persistence baseline: predict(t+h) = occupancy at the last input tick (feature index 0 of the last timestep)
    baseline_pred = X_test[:, -1, 0].numpy()[:, None].repeat(len(HORIZONS), axis=1)
    baseline_metrics = evaluate(baseline_pred, test_target, test_mask, test_meta, group_keys=["event_type", "scenario_type", "domain"])

    os.makedirs(WEIGHTS_DIR, exist_ok=True)
    torch.save(model.state_dict(), os.path.join(WEIGHTS_DIR, "lstm.pt"))
    F.save_json(cap_cfg, os.path.join(WEIGHTS_DIR, "lstm_scaler.json"))
    F.save_json(split, os.path.join(WEIGHTS_DIR, "lstm_split.json"))
    F.save_json({
        "seq_len": SEQ_LEN, "horizons": HORIZONS, "stride": STRIDE, "feature_dim": F.FEATURE_DIM_LSTM,
        "hidden_dim": 64, "num_layers": 1, "seed": SEED, "epochs": EPOCHS, "batch_size": BATCH_SIZE, "lr": LR,
        "feature_order": [
            "occ/100", "entry_pct/100", "exit_pct/100", "phase_onehot(3)", "event_type_onehot(5)",
            "domain_onehot(3)", "sport_onehot(4)", "competition_onehot(9)", "gender_onehot(2)", "stage_onehot(4)",
            "has_ticket_data", "sell_through/100", "booking_velocity/100", "demand_intensity/100",
            "capacity_scaled", "phase_age_norm",
        ],
    }, os.path.join(WEIGHTS_DIR, "lstm_feature_config.json"))
    F.save_json({"lstm": lstm_metrics, "persistence_baseline": baseline_metrics}, os.path.join(WEIGHTS_DIR, "lstm_metrics.json"))

    print(f"\nDone in {time.time()-t0:.1f}s")
    print("\n=== LSTM overall (test) ===", lstm_metrics["overall"])
    print("=== Persistence baseline overall (test) ===", baseline_metrics["overall"])
    print("\n=== LSTM by horizon ===")
    for h, v in lstm_metrics["by_horizon"].items():
        print(f"  {h}: n={v['n']} mae={v['mae']} rmse={v['rmse']}")
    print("=== Persistence by horizon ===")
    for h, v in baseline_metrics["by_horizon"].items():
        print(f"  {h}: n={v['n']} mae={v['mae']} rmse={v['rmse']}")


if __name__ == "__main__":
    os.chdir(os.path.dirname(__file__))
    main()
