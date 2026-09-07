"""Shared-weight LSTM crowd forecaster. One model across all 9 zones / 3
domains (domain + capacity are input features, not separate per-zone
models) -- direct multi-horizon output, not recursive rollout."""
import torch
import torch.nn as nn


class LSTMForecaster(nn.Module):
    def __init__(self, input_dim, hidden_dim=64, num_layers=1):
        super().__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers=num_layers, batch_first=True)
        self.head_15 = nn.Linear(hidden_dim, 1)
        self.head_30 = nn.Linear(hidden_dim, 1)
        self.head_60 = nn.Linear(hidden_dim, 1)

    def forward(self, x):
        # x: (batch, seq_len=12, input_dim)
        out, _ = self.lstm(x)
        last = out[:, -1, :]  # (batch, hidden_dim)
        preds = torch.cat([self.head_15(last), self.head_30(last), self.head_60(last)], dim=1)
        return preds  # (batch, 3) -- occupancy_pct/100 at +15/+30/+60 ticks; NOT clipped (occupancy can exceed 1.0, e.g. congested gates >100% capacity)
