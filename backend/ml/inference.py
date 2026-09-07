"""Standalone inference wrapper -- loads trained weights, exposes forecast /
network-pressure functions. NOT wired into the FastAPI app; that's a
separate future integration decision.

Two distinct anomaly/pressure concepts, kept explicitly separate:
  - OFFLINE (train_gnn.py only): predicted vs ACTUAL next-tick occupancy.
    That is training/eval-only and is NOT reproduced here.
  - LIVE (this module): network_pressure() never uses future ground truth.
    It compares the GNN's graph-propagated expectation against the LSTM's
    own independent forecast for the same zone -- both of these are already
    known at inference time, no peeking at what actually happens next.
"""
import os

import numpy as np
import torch

import features as F
from lstm_model import LSTMForecaster
from gnn_model import GNNForecaster

WEIGHTS_DIR = F.WEIGHTS_DIR
HORIZONS = [15, 30, 60]


def load_lstm():
    cfg = F.load_json(os.path.join(WEIGHTS_DIR, "lstm_feature_config.json"))
    model = LSTMForecaster(input_dim=cfg["feature_dim"], hidden_dim=cfg["hidden_dim"], num_layers=cfg["num_layers"])
    model.load_state_dict(torch.load(os.path.join(WEIGHTS_DIR, "lstm.pt"), weights_only=True))
    model.eval()
    scaler_cfg = F.load_json(os.path.join(WEIGHTS_DIR, "lstm_scaler.json"))

    def capacity_scaler(capacity):
        v = np.log1p(capacity)
        lo, hi = scaler_cfg["log1p_min"], scaler_cfg["log1p_max"]
        return float((v - lo) / (hi - lo)) if hi > lo else 0.0

    return model, capacity_scaler


def load_gnn():
    cfg = F.load_json(os.path.join(WEIGHTS_DIR, "gnn_feature_config.json"))
    model = GNNForecaster(in_dim=cfg["in_dim"], hidden_dim=cfg["hidden_dim"])
    model.load_state_dict(torch.load(os.path.join(WEIGHTS_DIR, "gnn.pt"), weights_only=True))
    model.eval()
    return model, cfg


def forecast_zone(lstm_model, capacity_scaler, run_meta, zone_domain, series, t, n_ticks):
    """run_meta: dict with event_type/scenario_type(ignored)/sport/.../demand_intensity,
    same shape as one entry from features.load_runs()'s run dict (scenario_type is
    accepted but never read -- it must never reach the feature vector).
    series: output of features.zone_series() for this zone, must cover ticks
    (t-11..t) at least. Returns occupancy_pct forecasts for +15/+30/+60 ticks."""
    seq = [F.lstm_feature_vector(run_meta, zone_domain, series, tt, n_ticks, capacity_scaler) for tt in range(t - 11, t + 1)]
    x = torch.tensor([seq], dtype=torch.float32)
    with torch.no_grad():
        pred = lstm_model(x).numpy()[0]  # (3,) occupancy_pct/100
    return {f"occupancy_pct_{h}m": float(pred[i] * 100.0) for i, h in enumerate(HORIZONS)}


def network_pressure(gnn_model, current_node_feats, node_history_for_lag, lstm_forecast_15m_by_zone=None):
    """LIVE signal -- never uses future ground truth.

    current_node_feats: dict zone_role -> [occ/100, entry_pct/100, exit_pct/100, *domain_onehot(3)]
        (features.gnn_node_feature_vector output) for the CURRENT tick.
    node_history_for_lag: dict zone_role -> list of that same feature vector
        for the last (max_lag+1) ticks, most recent last (index -1 = current
        tick), used to pull each neighbor's value from (current - edge_lag).
    lstm_forecast_15m_by_zone: optional dict zone_role -> occupancy_pct (0-100)
        from forecast_zone(). If given, each zone's OWN occupancy channel in
        current_node_feats is replaced with this forecasted value before the
        GNN runs, so network_expected_occupancy_pct answers "if zones evolve
        to their +15m LSTM forecast, what does the network expect next for
        each zone" rather than just "given right now".
        CAVEAT (state explicitly, do not hide): the GNN was trained only on
        real observed telemetry, never on LSTM-forecasted values standing in
        for occupancy -- feeding it a forecast is a distribution shift the
        model was not evaluated against. Treat this as an approximation, not
        a validated prediction.

    Returns: dict zone_role -> {network_expected_occupancy_pct, network_adjusted_pressure_pct}
    (the latter is None if no lstm forecast was supplied for that zone -- there's
    nothing to compare the network's expectation against).
    """
    zones = F.ZONE_ROLES
    x0 = np.zeros((F.N_ZONES, F.FEATURE_DIM_GNN), dtype=np.float32)
    for z in zones:
        vec = list(current_node_feats[z])
        if lstm_forecast_15m_by_zone and z in lstm_forecast_15m_by_zone:
            vec[0] = lstm_forecast_15m_by_zone[z] / 100.0
        x0[F.ZONE_INDEX[z]] = vec

    lag_agg = np.zeros((F.N_ZONES, F.FEATURE_DIM_GNN), dtype=np.float32)
    degree = np.zeros(F.N_ZONES, dtype=np.float32)
    adjacency = np.zeros((F.N_ZONES, F.N_ZONES), dtype=np.float32)
    for a, b, lag in F.ZONE_EDGES:
        ia, ib = F.ZONE_INDEX[a], F.ZONE_INDEX[b]
        hist_a, hist_b = node_history_for_lag[a], node_history_for_lag[b]
        if lag > len(hist_a) or lag > len(hist_b):
            continue  # not enough history buffered for this edge's lag; skip rather than guess
        val_b_lagged = hist_b[-1 - lag]
        val_a_lagged = hist_a[-1 - lag]
        lag_agg[ia] += val_b_lagged
        lag_agg[ib] += val_a_lagged
        degree[ia] += 1
        degree[ib] += 1
        adjacency[ia, ib] = 1.0
        adjacency[ib, ia] = 1.0
    nz = degree > 0
    lag_agg[nz] /= degree[nz, None]
    row_sum = adjacency.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0] = 1.0
    a_hat = adjacency / row_sum

    with torch.no_grad():
        pred = gnn_model(
            torch.tensor(x0[None, ...], dtype=torch.float32),
            torch.tensor(lag_agg[None, ...], dtype=torch.float32),
            torch.tensor(a_hat[None, ...], dtype=torch.float32),
        ).numpy()[0]

    out = {}
    for z in zones:
        expected_pct = float(pred[F.ZONE_INDEX[z]] * 100.0)
        pressure = None
        if lstm_forecast_15m_by_zone and z in lstm_forecast_15m_by_zone:
            pressure = expected_pct - lstm_forecast_15m_by_zone[z]
        out[z] = {"network_expected_occupancy_pct": expected_pct, "network_adjusted_pressure_pct": pressure}
    return out


if __name__ == "__main__":
    # Smoke test only -- proves the saved weights actually load and the
    # composition runs end to end on one real run from the dataset, not a
    # claim about accuracy (that's train_lstm.py's/train_gnn.py's job).
    os.chdir(os.path.dirname(__file__))
    runs = F.load_runs()
    rid = next(iter(runs))
    run = runs[rid]
    t = 40
    print(f"Smoke test on run {rid} ({run['event_type']}/{run['scenario_type']}), tick {t}")

    lstm_model, capacity_scaler = load_lstm()
    gnn_model, gnn_cfg = load_gnn()

    lstm_forecasts, current_feats, history_feats = {}, {}, {}
    for zone_role in F.ZONE_ROLES:
        zone = run["zones"][zone_role]
        series = F.zone_series(zone)
        lstm_forecasts[zone_role] = forecast_zone(lstm_model, capacity_scaler, run, zone["domain"], series, t, run["n_ticks"])
        current_feats[zone_role] = F.gnn_node_feature_vector(zone["domain"], series, t)
        history_feats[zone_role] = [F.gnn_node_feature_vector(zone["domain"], series, tt) for tt in range(max(0, t - F.MAX_LAG), t + 1)]

    print("\nLSTM forecasts:")
    for z, v in lstm_forecasts.items():
        print(f"  {z}: {v}")

    lstm_15m = {z: v["occupancy_pct_15m"] for z, v in lstm_forecasts.items()}
    pressure = network_pressure(gnn_model, current_feats, history_feats, lstm_forecast_15m_by_zone=lstm_15m)
    print("\nNetwork pressure (using LSTM +15m forecast as the propagated state):")
    for z, v in pressure.items():
        print(f"  {z}: {v}")
