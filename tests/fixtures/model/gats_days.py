"""Runs in the deep environment: python gats_days.py; prints one JSON line for tests/native/test_model_gats_days.py. The expected predictions are the network run
on one day's rows alone, straight through torch."""
import json

import numpy as np
import torch
from native import model_deep as Dp
from qlib.contrib.model.pytorch_gats import GATs

torch.manual_seed(1)
holder = GATs(d_feat=6, hidden_size=8, num_layers=1, GPU=-1)
net = Dp.network_of(holder, "gats")
net.eval()
rng = np.random.default_rng(2)
x = rng.normal(0, 1, (60, 6 * 20)).astype(np.float32)
days = np.repeat([0, 1, 2], 20)
mine = Dp.predict(holder, "gats", x, days=days)
with torch.no_grad():
    alone = np.concatenate([net(torch.from_numpy(x[days == day])).numpy().reshape(-1) for day in (0, 1, 2)])
    mixed = net(torch.from_numpy(x)).numpy().reshape(-1)   # every day in one pass: what a name would see of the other days
later = x.copy()
later[days == 2] += 1.0
moved = Dp.predict(holder, "gats", later, days=days)
try:
    Dp.predict(holder, "gats", x)
    refused = None
except ValueError as error:
    refused = str(error)
print(json.dumps({"diff": float(np.abs(mine - alone).max()), "mixed_diff": float(np.abs(mixed - alone).max()),
                  "first_days_unmoved": float(np.abs(moved[days < 2] - mine[days < 2]).max()), "refused": refused}))
