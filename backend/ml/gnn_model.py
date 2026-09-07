"""Hand-rolled inductive GCN (plain PyTorch tensor ops, no torch_geometric).

Strictly feature-based message passing -- no nn.Embedding keyed by node
index or zone identity anywhere, so the model has no way to memorize "node 3
always does X". Two layers:
  layer 1 (lag-aware): aggregates each neighbor's RAW telemetry from
    (t - edge_lag), not tick t -- this is where propagation lag enters.
  layer 2 (structure-aware): a standard degree-normalized adjacency
    aggregation over layer 1's hidden representations (which already carry
    lagged temporal context, so no lag is reapplied here).

Node/edge masking (for subgraph sampling + edge dropout, and the held-out
structural graph) is handled entirely in the input tensors (X0, lag_agg,
A_hat all zeroed/renormalized for dropped nodes/edges before this model ever
sees them) -- the model itself is graph-shape-agnostic, operating on
whatever (batch, N, F) / (batch, N, N) it's given.
"""
import torch
import torch.nn as nn


class GNNForecaster(nn.Module):
    def __init__(self, in_dim, hidden_dim=32):
        super().__init__()
        self.self1 = nn.Linear(in_dim, hidden_dim)
        self.neigh1 = nn.Linear(in_dim, hidden_dim)
        self.self2 = nn.Linear(hidden_dim, hidden_dim)
        self.neigh2 = nn.Linear(hidden_dim, hidden_dim)
        self.out = nn.Linear(hidden_dim, 1)
        self.act = nn.ReLU()

    def forward(self, x0, lag_agg, a_hat):
        # x0, lag_agg: (batch, N, in_dim); a_hat: (batch, N, N) degree-normalized adjacency (no self-loops)
        h1 = self.act(self.self1(x0) + self.neigh1(lag_agg))
        h2 = self.act(self.self2(h1) + self.neigh2(torch.bmm(a_hat, h1)))
        return self.out(h2).squeeze(-1)  # (batch, N) -- predicted next-tick occupancy_pct/100 per node
