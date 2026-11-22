"""Neural networks for the model pipeline, trained one epoch at a time from the outside (research process, deep learning environment only).

Qlib's PyTorch models each have `train_epoch(x, y)` and `test_epoch(x, y)`; their own `fit` is exactly "train an epoch, test it, keep the best,
stop early". TALYX runs that loop itself so that every epoch is reported, every epoch is saved (a stopped or failed window resumes from the saved
epoch), the best epoch's parameters are what the window keeps, and the device can be chosen. Rows are `[N, features × look back]` with the features
outside and time running from old to new, the order Qlib's non-`ts` models read (`reshape(N, d_feat, T)`).

Every network is built on the CPU (`GPU=-1`) and moved afterwards (`place`), since Qlib decides its device in the constructor from `torch.cuda`.
"""
from __future__ import annotations

import contextlib
import copy
import importlib
import math
import os
import time

import numpy as np
import pandas as pd
import torch

from . import i18n

# id -> (module, class, the attribute holding the torch network, how its size controls are named)
NETWORKS = {
    "lstm": ("pytorch_lstm", "LSTM", "lstm_model", "rnn"),
    "gru": ("pytorch_gru", "GRU", "gru_model", "rnn"),
    "alstm": ("pytorch_alstm", "ALSTM", "ALSTM_model", "rnn"),
    "transformer": ("pytorch_transformer", "TransformerModel", "model", "attention"),
    "localformer": ("pytorch_localformer", "LocalformerModel", "model", "attention"),
    "tcn": ("pytorch_tcn", "TCN", "tcn_model", "tcn"),
    "sfm": ("pytorch_sfm", "SFM", "sfm_model", "sfm"),
    "krnn": ("pytorch_krnn", "KRNN", "krnn_model", "krnn"),
    "sandwich": ("pytorch_sandwich", "Sandwich", "sandwich_model", "sandwich"),
    "tabnet": ("pytorch_tabnet", "TabnetModel", "tabnet_model", "tabnet"),
    "gats": ("pytorch_gats", "GATs", "GAT_model", "rnn"),
    "mlp": ("pytorch_nn", "DNNModelPytorch", "dnn_model", "mlp"),
    "own": (None, None, "net", "own"),
}
EPOCH_NETWORKS = tuple(NETWORKS)
SEQUENCE = {"lstm", "gru", "alstm", "transformer", "localformer", "tcn", "sfm", "krnn", "sandwich", "gats"}


class DeepFailure(Exception):
    """A training that cannot go on; `failure` is what the run shows."""

    def __init__(self, failure):
        super().__init__(failure["message"])
        self.failure = failure


def oom_kind(error):
    """What ran out: "gpu-memory" for CUDA's out of memory, "memory" for the machine's, None for any other error."""
    text = str(error).lower()
    if isinstance(error, MemoryError):
        return "memory"
    if isinstance(error, RuntimeError) and "out of memory" in text:
        return "gpu-memory" if "cuda" in text or type(error).__name__ == "OutOfMemoryError" else "memory"
    if isinstance(error, RuntimeError) and "not enough memory" in text:
        return "memory"
    return None


# ---------------------------------------------------------------------------------------------------------------- the look back
def sequences(x, t, i, lookback, process=None, chunk=50_000):
    """Each row's last `lookback` days of inputs as `[N, F × L]`: features outside, time from old to new. `x` is days × names × features; days
    before the first one repeat the first. `process` (the window's fitted processing) is applied to each chunk of gathered values."""
    t, i = np.asarray(t), np.asarray(i)
    features = x.shape[2]
    out = np.empty((len(t), features * lookback), dtype=np.float32)
    steps = np.arange(lookback) - (lookback - 1)
    for start in range(0, len(t), chunk):
        tt, ii = t[start:start + chunk], i[start:start + chunk]
        days = np.clip(tt[:, None] + steps[None, :], 0, None)  # [n, L]
        gathered = x[days, ii[:, None], :]  # [n, L, F]
        flat = gathered.reshape(-1, features)
        if process is not None:
            flat = process(flat)
        block = np.nan_to_num(np.asarray(flat, dtype=np.float32)).reshape(len(tt), lookback, features)
        out[start:start + len(tt)] = block.transpose(0, 2, 1).reshape(len(tt), features * lookback)
    return out


# ---------------------------------------------------------------------------------------------------------------- building
def _qlib(module, name):
    return getattr(importlib.import_module(f"qlib.contrib.model.{module}"), name)


class _Own:
    """A network the user wrote: Adam on the squared error, in the same two calls (`train_epoch`, `test_epoch`) as Qlib's networks."""

    def __init__(self, net, lr, batch):
        self.net, self.batch, self.device = net, int(batch), torch.device("cpu")
        self.train_optimizer = torch.optim.Adam(net.parameters(), lr=lr)

    def _out(self, values):
        return self.net(values).reshape(-1)

    def train_epoch(self, x, y):
        values = torch.from_numpy(np.asarray(x.values, dtype=np.float32)).to(self.device)
        target = torch.from_numpy(np.asarray(y.values, dtype=np.float32).reshape(-1)).to(self.device)
        self.net.train()
        order = torch.from_numpy(np.random.permutation(len(values))).to(self.device)
        for start in range(0, len(values), self.batch):
            pick = order[start:start + self.batch]
            self.train_optimizer.zero_grad()
            loss = torch.mean((self._out(values[pick]) - target[pick]) ** 2)
            loss.backward()
            self.train_optimizer.step()

    def test_epoch(self, x, y):
        values = torch.from_numpy(np.asarray(x.values, dtype=np.float32)).to(self.device)
        target = torch.from_numpy(np.asarray(y.values, dtype=np.float32).reshape(-1)).to(self.device)
        self.net.eval()
        with torch.no_grad():
            loss = float(torch.mean((self._out(values) - target) ** 2))
        return loss, -loss


def build(algorithm, params, d_feat, lookback, seed, *, engine=None):
    """Qlib's model object for the network, on the CPU. `params` holds epochs, batch, lr, stop, dropout, layers, width, heads, optimizer, pretrain."""
    if algorithm == "own":
        return _Own(params["_module"], float(params.get("lr", 0.001)), int(params.get("batch", 800)))
    module, name, _attribute, family = NETWORKS[algorithm]
    cls = _qlib(module, name)
    common = {"n_epochs": int(params.get("epochs", 100)), "lr": float(params.get("lr", 0.001)), "early_stop": int(params.get("stop", 20)), "GPU": -1,
              "seed": int(seed), "loss": "mse", "optimizer": params.get("optimizer") or "adam"}
    batch, dropout, layers, width = int(params.get("batch", 800)), float(params.get("dropout", 0.0)), int(params.get("layers", 2)), int(params.get("width", 64))
    extra = dict(engine or {})
    if family in ("rnn", "attention"):
        kwargs = {**common, "d_feat": d_feat, "batch_size": batch, "dropout": dropout, "num_layers": layers}
        if family == "rnn":
            kwargs["hidden_size"] = width
            if algorithm == "gats":
                kwargs["base_model"] = params.get("base_model") or "GRU"
        else:
            kwargs.update(d_model=width, nhead=int(params.get("heads", 2)), n_jobs=0)
    elif family == "tcn":
        kwargs = {**common, "d_feat": d_feat, "batch_size": batch, "dropout": dropout, "num_layers": layers, "n_chans": width}
    elif family == "sfm":
        kwargs = {**common, "d_feat": d_feat, "batch_size": batch, "hidden_size": width, "dropout_W": dropout, "dropout_U": dropout}
    elif family == "krnn":
        kwargs = {**common, "fea_dim": d_feat, "batch_size": batch, "dropout": dropout, "rnn_layers": layers, "rnn_dim": width, "cnn_dim": width}
    elif family == "sandwich":
        kwargs = {**common, "fea_dim": d_feat, "batch_size": batch, "dropout": dropout, "rnn_layers": layers}
    elif family == "tabnet":
        kwargs = {**common, "d_feat": d_feat * lookback, "batch_size": batch, "n_d": width, "n_a": width, "out_dim": width, "pretrain": bool(params.get("pretrain", 50)),
                  "pretrain_n_epochs": int(params.get("pretrain", 50))}
        kwargs.pop("n_epochs"), kwargs.pop("early_stop")
        kwargs.update(n_epochs=common["n_epochs"], early_stop=common["early_stop"])
    elif family == "mlp":
        kwargs = {"lr": common["lr"], "batch_size": batch, "optimizer": common["optimizer"] if params.get("optimizer") else "gd", "loss": params.get("loss", "mse"),
                  "GPU": -1, "seed": int(seed), "weight_decay": 0.0, "scheduler": None, "max_steps": 1, "early_stop_rounds": int(params.get("stop", 20)), "eval_steps": 1,
                  "pt_model_kwargs": {"input_dim": d_feat * lookback, "layers": tuple([width] * layers)}}
    else:
        raise ValueError(algorithm)
    kwargs.update(extra)
    model = cls(**kwargs)
    return model if family != "mlp" else _MLP(model, kwargs["batch_size"], kwargs["loss"])


class _MLP:
    """Qlib's MLP has no `train_epoch`/`test_epoch` (it trains by steps): one epoch here is `ceil(rows / batch)` steps of its own step."""

    def __init__(self, model, batch, loss):
        self.model, self.batch, self.loss = model, batch, loss
        self.dnn_model, self.train_optimizer, self.device = model.dnn_model, model.train_optimizer, model.device

    def train_epoch(self, x, y):
        values = torch.from_numpy(np.asarray(x.values, dtype=np.float32)).to(self.device)
        target = torch.from_numpy(np.asarray(y.values, dtype=np.float32).reshape(-1)).to(self.device)
        ones = torch.ones(self.batch, device=self.device)
        self.dnn_model.train()
        for _ in range(math.ceil(len(values) / self.batch)):
            self.train_optimizer.zero_grad()
            choice = torch.from_numpy(np.random.choice(len(values), self.batch)).to(self.device)
            loss = self.model.get_loss(self.dnn_model(values[choice]), ones, target[choice], self.loss)
            loss.backward()
            self.train_optimizer.step()

    def test_epoch(self, x, y):
        values = torch.from_numpy(np.asarray(x.values, dtype=np.float32)).to(self.device)
        target = torch.from_numpy(np.asarray(y.values, dtype=np.float32).reshape(-1)).to(self.device)
        self.dnn_model.eval()
        with torch.no_grad():
            loss = float(self.model.get_loss(self.dnn_model(values), torch.ones(len(values), device=self.device), target, self.loss))
        return loss, -loss


def network_of(model, algorithm):
    return getattr(model, NETWORKS[algorithm][2])


def place(model, algorithm, device):
    """Move a built Qlib model to `device` (a torch device name): its network, its device attribute and the optimizer's state."""
    target = torch.device(device)
    model.device = target
    net = network_of(model, algorithm)
    net.to(target)
    for state in model.train_optimizer.state.values():
        for key, value in state.items():
            if torch.is_tensor(value):
                state[key] = value.to(target)
    return model


# ---------------------------------------------------------------------------------------------------------------- the epoch loop
def _frame(x, y=None, t=None, i=None):
    index = pd.MultiIndex.from_arrays([np.asarray(t), np.asarray(i)], names=["datetime", "instrument"]) if t is not None else None
    columns = pd.MultiIndex.from_product([["feature"], [f"f{n}" for n in range(x.shape[1])]])
    features = pd.DataFrame(x, index=index, columns=columns)["feature"]
    return features, None if y is None else pd.DataFrame({"label": np.asarray(y, dtype=np.float32)}, index=index)


def _states():
    return {"np": np.random.get_state(), "torch": torch.get_rng_state()}


def fit_network(algorithm, train, valid, params, *, d_feat, lookback, seed=42, reproducible=True, threads=1, device="cpu", checkpoint=None, digest="",
                on_epoch=None, engine=None, stop_after=None):
    """Train one window. `train` and `valid` hold `x` (`[N, F × L]`), `y`, `t`, `i`. Returns (model, curve, best_epoch) where the model carries the
    best epoch's parameters. `checkpoint` is a file the loop writes after each epoch and resumes from when its `digest` is the same;
    `on_epoch(epoch, epochs, train_loss, valid_loss, best_loss, best_epoch)` hears every epoch; `stop_after` stops after that many epochs (tests)."""
    torch.set_num_threads(max(1, int(threads)))
    if reproducible:
        torch.use_deterministic_algorithms(True)
        if str(device).startswith("cuda"):
            os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
            torch.backends.cudnn.benchmark = False
    np.random.seed(int(seed))
    torch.manual_seed(int(seed))
    model = build(algorithm, params, d_feat, lookback, seed, engine=engine)
    holder = model.model if isinstance(model, _MLP) else model
    if algorithm == "gats" and params.get("base_state"):
        start_from_base(holder, params["base_state"])
    if str(device) != "cpu":
        place(holder, algorithm, device)
        if isinstance(model, _MLP):
            model.dnn_model, model.train_optimizer, model.device = holder.dnn_model, holder.train_optimizer, holder.device
    x_train, y_train = _frame(train["x"], train["y"], train["t"], train["i"])
    x_valid, y_valid = _frame(valid["x"], valid["y"], valid["t"], valid["i"])
    if algorithm == "tabnet":
        fresh = checkpoint is None or not os.path.isfile(checkpoint)
        pretrain = int(params.get("pretrain", 50))
        for number in range(pretrain if fresh else 0):
            model.pretrain_epoch(x_train)
            loss = model.pretrain_test_epoch(x_valid)
            if on_epoch is not None:
                on_epoch(0, int(params.get("epochs", 100)), None, float(loss), None, 0, pretrain=(number + 1, pretrain))
        from qlib.contrib.model.pytorch_tabnet import FinetuneModel
        model.tabnet_model = FinetuneModel(model.out_dim, model.final_out_dim, model.tabnet_model).to(model.device)
        model.train_optimizer = type(model.train_optimizer)(model.tabnet_model.parameters(), lr=float(params.get("lr", 0.01)))
    net = network_of(holder, algorithm)
    epochs, stop = int(params.get("epochs", 100)), int(params.get("stop", 20))
    state = {"epoch": 0, "best_score": -np.inf, "best_epoch": 0, "stale": 0, "best": copy.deepcopy(net.state_dict()), "train": [], "valid": []}
    if checkpoint is not None and os.path.isfile(checkpoint):
        saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
        if saved.get("digest") == digest:
            net.load_state_dict(saved["net"])
            holder.train_optimizer.load_state_dict(saved["opt"])
            state.update({key: saved[key] for key in ("epoch", "best_score", "best_epoch", "stale", "best", "train", "valid")})
            np.random.set_state(saved["np"])
            torch.set_rng_state(saved["torch"])
    while state["epoch"] < epochs and (stop <= 0 or state["stale"] < stop):
        try:
            model.train_epoch(x_train, y_train)
            train_loss, _ = model.test_epoch(x_train, y_train)
            valid_loss, score = model.test_epoch(x_valid, y_valid)
        except (MemoryError, RuntimeError) as error:
            kind = oom_kind(error)
            if kind is None:
                raise
            if kind == "gpu-memory":
                with contextlib.suppress(Exception):
                    torch.cuda.empty_cache()
            raise DeepFailure({"stage": "compute", "kind": kind, "title": "ran out of GPU memory" if kind == "gpu-memory" else "ran out of memory", "message": str(error).splitlines()[0],
                               "epoch": state["epoch"] + 1, "saved": state["epoch"], "batch": int(params.get("batch", 800))}) from error
        if not math.isfinite(float(valid_loss)) or not math.isfinite(float(train_loss)):
            raise DeepFailure({"stage": "compute", "title": i18n.t("the validation loss became NaN at epoch {epoch}", epoch=state['epoch'] + 1),
                               "message": i18n.t("the validation loss became NaN at epoch {epoch}; lower the learning rate (now {rate})", epoch=state['epoch'] + 1, rate=params.get('lr', 0.001)),
                               "epoch": state["epoch"] + 1})
        state["epoch"] += 1
        state["train"].append(float(train_loss))
        state["valid"].append(float(valid_loss))
        if score > state["best_score"]:
            state.update(best_score=float(score), best_epoch=state["epoch"], stale=0, best=copy.deepcopy(net.state_dict()))
        else:
            state["stale"] += 1
        if checkpoint is not None:
            tmp = f"{checkpoint}.tmp"
            torch.save({"digest": digest, "net": net.state_dict(), "opt": holder.train_optimizer.state_dict(), **_states(),
                        **{key: state[key] for key in ("epoch", "best_score", "best_epoch", "stale", "best", "train", "valid")}}, tmp)
            os.replace(tmp, checkpoint)
        if on_epoch is not None:
            on_epoch(state["epoch"], epochs, float(train_loss), float(valid_loss), -state["best_score"], state["best_epoch"])
        if stop_after is not None and state["epoch"] >= stop_after:
            break
    net.load_state_dict(state["best"])
    if checkpoint is not None and os.path.isfile(checkpoint) and (state["epoch"] >= epochs or (stop > 0 and state["stale"] >= stop)):
        os.remove(checkpoint)
    return holder if not isinstance(model, _MLP) else model, {"train": state["train"], "valid": state["valid"], "unit": "epoch"}, state["best_epoch"]


# ---------------------------------------------------------------------------------------------------------------- predicting and keeping
def predict(model, algorithm, x, chunk=20_000, days=None):
    """Predictions for `[N, F x L]` rows in a no-grad forward pass, `chunk` rows at a time. GATs attends across the rows of one pass, so its rows go one day at a
    time (as Qlib's own GATs predicts): `days` says each row's day, and a name's prediction never sees another day's rows."""
    if algorithm == "gats":
        if days is None:
            raise ValueError("GATs predicts one day at a time: give the day of each row")
        days = np.asarray(days)
        out = np.zeros(len(x))
        for day in np.unique(days):
            at = np.nonzero(days == day)[0]
            out[at] = predict(model, "gats-day", np.asarray(x)[at], chunk=len(at))
        return out
    holder = model.model if isinstance(model, _MLP) else model
    net = network_of(holder, "gats" if algorithm == "gats-day" else algorithm)
    net.eval()
    device = next(net.parameters()).device
    out = []
    with torch.no_grad():
        for start in range(0, len(x), chunk):
            block = torch.from_numpy(np.asarray(x[start:start + chunk], dtype=np.float32)).to(device)
            result = net(block, torch.ones(len(block), block.shape[1], device=device)) if algorithm == "tabnet" else net(block)
            out.append(result.detach().cpu().numpy().reshape(-1))
    return np.concatenate(out) if out else np.zeros(0)


def start_from_base(holder, path):
    """GATs starts from a finished LSTM or GRU run's window: the base network's recurrent weights go into GATs's own (Qlib's `model_path`); what the base holds that
    GATs does not have, or in another shape, is a mismatch the check names before sending."""
    saved = torch.load(path, map_location="cpu", weights_only=False)
    base = {key: value for key, value in saved["state"].items() if key.startswith("rnn.")}
    own = network_of(holder, "gats").state_dict()
    wrong = [key for key, value in base.items() if key not in own or own[key].shape != value.shape]
    if not base or wrong:
        raise DeepFailure({"stage": "settings", "title": i18n.t("Start from a run with the same inputs and look back"),
                           "message": i18n.t("The run to start from does not have the same width, layers and inputs as GATs here; choose a run with the same settings.")})
    network_of(holder, "gats").load_state_dict({**own, **base})


def state_of(model, algorithm):
    holder = model.model if isinstance(model, _MLP) else model
    return network_of(holder, algorithm).state_dict()


def rebuild(algorithm, params, d_feat, lookback, seed, state, engine=None):
    model = build(algorithm, params, d_feat, lookback, seed, engine=engine)
    holder = model.model if isinstance(model, _MLP) else model
    network_of(holder, algorithm).load_state_dict(state)
    return model


def timer():
    return time.perf_counter()


# ---------------------------------------------------------------------------------------------------------------- what a window keeps
class NeuralFitted:
    """One window's trained network, shaped like `model_algorithms.Fitted`. Rows are read through `source(t, i)`, which gives the look back
    already processed as the window's training fitted it (set by the run; not kept in the window cache)."""

    neural = True

    def __init__(self, algorithm, model, features, curve, best, *, params, d_feat, lookback, seed, engine=None, objective="l2"):
        self.algorithm, self.model, self.features, self.curve, self.best = algorithm, model, features, curve, best
        self.params, self.d_feat, self.lookback, self.seed, self.engine, self.objective, self.k = params, d_feat, lookback, seed, engine, objective, 5
        self.source = None
        self.valid_rows = None

    @property
    def width(self):
        """Columns of the rows an input owns: the look back, or one each when the rows already are Alpha360's 5 fields × 60 days."""
        return self.lookback if self.d_feat == len(self.features) else 1

    def predict_rows(self, t, i):
        return predict(self.model, self.algorithm, self.source(t, i), days=t)

    def predict(self, x, days=None):
        return predict(self.model, self.algorithm, np.nan_to_num(np.asarray(x, dtype=np.float32)), days=days)

    def predict_array(self, x, t, i):
        return predict(self.model, self.algorithm, np.nan_to_num(np.asarray(x, dtype=np.float32)), days=t)

    def importance(self, x=None):
        """Permutation importance (share): the fall in the validation rank IC when an input is shuffled within each day."""
        if self.valid_rows is None:
            return {name: 0.0 for name in self.features}
        return permutation_importance(self)

    def contributions(self, x):
        return None

    def contributions_rows(self, t, i, steps=64):
        """Integrated gradients for each row, by input (a sequence network's look back days added up), the last column the prediction at the baseline."""
        return integrated_gradients(self, self.source(t, i), steps)


def groups_of(features, limit=40):
    """Inputs in groups for shuffling: one each up to `limit`, else by the letters their names start with."""
    if len(features) <= limit:
        return {name: [n] for n, name in enumerate(features)}
    import re
    out = {}
    for n, name in enumerate(features):
        out.setdefault(re.match(r"[A-Za-z$]*", name).group(0) or name, []).append(n)
    while len(out) > limit:
        out = {f"group {k}": value for k, value in enumerate(np.array_split(np.arange(len(features)), limit))} if len(out) > limit else out
        out = {key: list(map(int, value)) for key, value in out.items()}
    return out


def _rank_ic(pred, y, t):
    frame = pd.DataFrame({"p": pred, "y": y, "t": t})
    daily = frame.groupby("t").apply(lambda g: g["p"].rank().corr(g["y"].rank()) if len(g) > 2 else np.nan, include_groups=False)
    return float(np.nanmean(daily.to_numpy())) if len(daily) else 0.0


def permutation_importance(fitted, limit=40_000):
    rows = fitted.valid_rows
    t, i, y = np.asarray(rows["t"]), np.asarray(rows["i"]), np.asarray(rows["y"])
    rng = np.random.default_rng(0)
    if len(t) > limit:
        days = np.unique(t)
        keep_days = rng.choice(days, size=max(1, int(len(days) * limit / len(t))), replace=False)
        keep = np.isin(t, keep_days)
        t, i, y = t[keep], i[keep], y[keep]
    x = fitted.source(t, i)
    base = _rank_ic(fitted.predict_array(x, t, i), y, t)
    width = fitted.width
    drops = {}
    order = np.argsort(t, kind="stable")
    for name, members in groups_of(fitted.features).items():
        shuffled = x.copy()
        columns = [f * width + j for f in members for j in range(width)]
        for day in np.unique(t):
            at = np.nonzero(t == day)[0]
            if len(at) < 2:
                continue
            perm = rng.permutation(at)
            shuffled[np.ix_(at, columns)] = x[np.ix_(perm, columns)]
        drops[name] = max(0.0, base - _rank_ic(fitted.predict_array(shuffled, t, i), y, t))
    del order
    total = sum(drops.values())
    share = {name: (value / total if total > 0 else 0.0) for name, value in drops.items()}
    out = {}
    for name, members in groups_of(fitted.features).items():
        for f in members:
            out[fitted.features[f]] = share[name] / len(members)
    return out


def integrated_gradients(fitted, x, steps=64, chunk=2000):
    """Each input's share of `f(x) - f(baseline)`: the baseline is the day's average name (each column's mean over the rows given, which are one day's),
    the midpoint rule over `steps`, `x` in blocks of `chunk` rows. A row's values add up to the prediction's gap to the baseline."""
    x = np.asarray(x, dtype=np.float64)
    base = np.nanmean(x, axis=0, keepdims=True)
    holder = fitted.model.model if isinstance(fitted.model, _MLP) else fitted.model
    net = network_of(holder, fitted.algorithm)
    net.eval()
    device = next(net.parameters()).device
    width, features = fitted.width, len(fitted.features)
    out = np.zeros((len(x), features + 1))
    with torch.backends.cudnn.flags(enabled=False):
        for start in range(0, len(x), chunk):
            block = torch.from_numpy(x[start:start + chunk]).to(device)
            origin = torch.from_numpy(np.repeat(base, len(block), axis=0)).to(device)
            total = torch.zeros_like(block)
            for step in range(steps):
                point = (origin + (block - origin) * ((step + 0.5) / steps)).float().requires_grad_(True)
                result = net(point, torch.ones(len(point), point.shape[1], device=device)) if fitted.algorithm == "tabnet" else net(point)
                result.reshape(-1).sum().backward()
                total += point.grad.double()
            attribution = ((block - origin) * total / steps).cpu().numpy()
            out[start:start + len(block), :features] = attribution.reshape(len(block), features, width).sum(axis=2)
    out[:, features] = predict(fitted.model, fitted.algorithm, np.repeat(base, len(x), axis=0).astype(np.float32), days=np.zeros(len(x)))  # the rows are one day's
    return out


def integrated_gradients_of(forward, x, width, features, steps=64, chunk=2000):
    """`integrated_gradients` for a network whose forward pass takes the inputs alone (`forward`: tensor [N, F x width] -> tensor [N]): the same baseline, steps and sum."""
    x = np.asarray(x, dtype=np.float64)
    base = np.nanmean(x, axis=0, keepdims=True)
    out = np.zeros((len(x), features + 1))
    with torch.backends.cudnn.flags(enabled=False):
        for start in range(0, len(x), chunk):
            block = torch.from_numpy(x[start:start + chunk])
            origin = torch.from_numpy(np.repeat(base, len(block), axis=0))
            total = torch.zeros_like(block)
            for step in range(steps):
                point = (origin + (block - origin) * ((step + 0.5) / steps)).float().requires_grad_(True)
                forward(point).reshape(-1).sum().backward()
                total += point.grad.double()
            out[start:start + len(block), :features] = ((block - origin) * total / steps).numpy().reshape(len(block), features, width).sum(axis=2)
    with torch.no_grad():
        out[:, features] = forward(torch.from_numpy(np.repeat(base, len(x), axis=0)).float()).reshape(-1).numpy()
    return out


def save(fitted, path):
    if getattr(fitted, "qlib_fit", False):
        return save_qlib(fitted, path)
    torch.save({"algorithm": fitted.algorithm, "state": state_of(fitted.model, fitted.algorithm), "features": fitted.features, "curve": fitted.curve, "best": fitted.best,
                "params": fitted.params, "d_feat": fitted.d_feat, "lookback": fitted.lookback, "seed": fitted.seed, "engine": fitted.engine, "objective": fitted.objective}, path)


def load(path):
    found = torch.load(path, map_location="cpu", weights_only=False)
    if found.get("kind") == "qlib":
        return load_qlib(found)
    model = rebuild(found["algorithm"], found["params"], found["d_feat"], found["lookback"], found["seed"], found["state"], found.get("engine"))
    return NeuralFitted(found["algorithm"], model, found["features"], found["curve"], found["best"], params=found["params"], d_feat=found["d_feat"], lookback=found["lookback"],
                        seed=found["seed"], engine=found.get("engine"), objective=found.get("objective", "l2"))


# ---------------------------------------------------------------------------------------------------------------- the networks Qlib's own fit trains
# id -> (module, class, whether a higher validation score is better by default)
QLIB_NETWORKS = {"igmtf": ("pytorch_igmtf", "IGMTF"), "add": ("pytorch_add", "ADD"), "adarnn": ("pytorch_adarnn", "ADARNN"), "tra": ("pytorch_tra", "TRAModel"),
                 "tcts": ("pytorch_tcts", "TCTS"), "hist": ("pytorch_hist", "HIST")}
_DAY0 = pd.Timestamp("2000-01-03")
_initialised = {}


def _stamps(t):
    return _DAY0 + pd.to_timedelta(np.asarray(t, dtype=np.int64), unit="D")


def _qlib_init(workdir):
    """Qlib's own settings need initialising once, with no data directory: the data come in memory."""
    if not _initialised:
        import qlib
        os.makedirs(workdir, exist_ok=True)
        qlib.init(provider_uri=workdir, kernels=1, logging_level="ERROR")
        _initialised["ok"] = True


def _patch_base(module_name, **sizes):
    """Qlib builds the base LSTM/GRU of IGMTF and HIST with its default sizes (6 inputs, 64 units, 2 layers): build it with this run's."""
    import functools
    module = importlib.import_module(f"qlib.contrib.model.{module_name}")
    for name in ("LSTMModel", "GRUModel"):
        original = getattr(module, f"_talyx_{name}", None) or getattr(module, name, None)
        if original is None:
            continue
        setattr(module, f"_talyx_{name}", original)
        setattr(module, name, functools.partial(original, **sizes))


def _patch_adarnn_shape(d_feat, len_seq):
    """Qlib's ADARNN reads every row as 6 fields × 60 days (`reshape(-1, 6, 60)`); read it as this run's."""
    module = importlib.import_module("qlib.contrib.model.pytorch_adarnn")

    def init(self, df):
        self.df_feature = torch.tensor(df["feature"].values.reshape(-1, d_feat, len_seq).transpose(0, 2, 1), dtype=torch.float32)
        self.df_label_reg = torch.tensor(df["label"].values.reshape(-1), dtype=torch.float32)
        self.df_index = df.index
    module.data_loader.__init__ = init


def _patch_igmtf_hidden():
    """With the same number of names every day, Qlib's `np.asarray(list of tensors, dtype=object)` becomes a 3-D array of numbers; make it one-dimensional."""
    from qlib.contrib.model.pytorch_igmtf import IGMTF
    if getattr(IGMTF, "_talyx_patched", False):
        return
    original = IGMTF.get_train_hidden

    def get_train_hidden(self, x_train):
        hidden, day = original(self, x_train)
        if getattr(hidden, "ndim", 1) > 1:
            fixed = np.empty(hidden.shape[0], dtype=object)
            fixed[:] = [torch.tensor(np.asarray(item, dtype=np.float32)) for item in hidden]
            hidden = fixed
        return hidden, day
    IGMTF.get_train_hidden = get_train_hidden
    IGMTF._talyx_patched = True


class _Hook(list):
    def __init__(self, values, heard):
        super().__init__(values)
        self.heard = heard

    def append(self, value):
        super().append(value)
        self.heard(len(self), value)


class _Evals(dict):
    """The `evals_result` Qlib's fit fills: it starts each list afresh, so the list that tells TALYX of every epoch is put in as the list is set."""

    def __init__(self, heard):
        super().__init__()
        self.heard = heard

    def __setitem__(self, key, value):
        super().__setitem__(key, _Hook(value, self.heard) if key == "valid" else value)


def _qlib_frame(parts, names, aux=None, daily=None):
    """One frame of rows for Qlib's dataset: (datetime, instrument) index, `feature` columns F0…, `label` columns LABEL0… (the label, then the auxiliary ones)."""
    xs, ts, iis, ys = [], [], [], []
    for number, part in enumerate(parts):
        xs.append(part["x"] if daily is None else part["x"][:, daily])
        ts.append(np.asarray(part["t"]))
        iis.append(np.asarray(part["i"]))
        ys.append([np.asarray(part["y"], dtype=np.float32)] + [np.asarray(a, dtype=np.float32) for a in (aux[number] if aux else [])])
    x, t, i = np.concatenate(xs), np.concatenate(ts), np.concatenate(iis)
    index = pd.MultiIndex.from_arrays([_stamps(t), np.asarray(names, dtype=object)[i]], names=["datetime", "instrument"])
    columns = pd.MultiIndex.from_tuples([("feature", f"F{n}") for n in range(x.shape[1])])
    frame = pd.DataFrame(np.asarray(x, dtype=np.float32), index=index, columns=columns)
    for label in range(len(ys[0])):
        frame[("label", f"LABEL{label}")] = np.concatenate([row[label] for row in ys])
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame.sort_index()


def _dataset(algorithm, frame, segments, *, lookback=20, horizon=2, states=3, batch=256, d_feat=6):
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.data.dataset.loader import StaticDataLoader
    handler = DataHandlerLP(data_loader=StaticDataLoader(config=frame))
    if algorithm == "tra":
        from qlib.contrib.data.dataset import MTSDatasetH
        return MTSDatasetH(handler, segments, seq_len=int(lookback), horizon=int(horizon), num_states=int(states), batch_size=int(batch), memory_mode="sample", drop_last=False)
    return DatasetH(handler, segments)


def _score_direction(algorithm, params):
    """Whether a higher validation score is better for what the network's Qlib fit tracks."""
    if algorithm in ("igmtf", "hist", "tra", "add"):
        return True
    return params.get("metric", "ic" if algorithm == "add" else "loss") in ("ic", "ric")


def build_qlib(algorithm, params, d_feat, lookback, seed, *, workdir, device="cpu", engine=None, concepts=None, n_aux=0, folder=None):
    """Qlib's model object for a network its own fit trains, on the CPU unless a CUDA card is named."""
    module, name = QLIB_NETWORKS[algorithm]
    cls = _qlib(module, name)
    gpu = int(str(device).split(":")[1]) if str(device).startswith("cuda:") else -1
    epochs, stop, lr = int(params.get("epochs", 100)), int(params.get("stop", 20)), float(params.get("lr", 0.001))
    width, layers, dropout = int(params.get("width", 64)), int(params.get("layers", 2)), float(params.get("dropout", 0.0))
    optimizer = params.get("optimizer") or "adam"
    common = {"d_feat": d_feat, "hidden_size": width, "num_layers": layers, "dropout": dropout, "n_epochs": epochs, "early_stop": stop, "GPU": gpu, "seed": int(seed)}
    extra = dict(engine or {})
    start = {"base_model": params.get("base_model") or "GRU", **({"model_path": params["model_path"]} if params.get("model_path") else {})}
    if algorithm in ("igmtf", "hist"):
        _patch_base(module, d_feat=d_feat, hidden_size=width, num_layers=layers)
        kwargs = {**common, "lr": lr, "metric": "ic", "optimizer": optimizer, **start}
        if algorithm == "igmtf":
            _patch_igmtf_hidden()
        else:
            kwargs.update(stock2concept=concepts["matrix_file"], stock_index=concepts["index_file"])
    elif algorithm == "add":
        if params.get("model_path"):
            _patch_base(module, d_feat=d_feat, hidden_size=width, num_layers=layers)
        kwargs = {**start, **common, "lr": lr, "metric": params.get("metric") or "ic", "batch_size": int(params.get("batch", 800)), "gamma": float(params.get("gamma", 0.1)),
                  "optimizer": optimizer}
    elif algorithm == "adarnn":
        _patch_adarnn_shape(d_feat, int(lookback))
        kwargs = {**common, "lr": lr, "metric": params.get("metric") or "loss", "batch_size": int(params.get("batch", 800)), "n_splits": int(params.get("domains", 2)),
                  "len_seq": int(lookback), "optimizer": optimizer, "loss": "mse"}
    elif algorithm == "tcts":
        kwargs = {**{k: v for k, v in common.items() if k != "GPU"}, "GPU": gpu, "output_dim": 1 + int(n_aux), "target_label": 0, "steps": int(params.get("steps", 3)),
                  "batch_size": int(params.get("batch", 800)), "fore_lr": lr, "weight_lr": lr, "input_dim": d_feat * int(lookback), "fore_optimizer": optimizer,
                  "lowest_valid_performance": 1e9}
    elif algorithm == "tra":
        states = int(params.get("states", 3))
        kwargs = {"model_config": dict(input_size=d_feat, hidden_size=width, num_layers=layers if "layers" in params else 2, rnn_arch="LSTM", use_attn=True, dropout=dropout),
                  "tra_config": dict(num_states=states, rnn_arch="LSTM", hidden_size=max(8, width // 2), num_layers=1, dropout=dropout, tau=1.0, src_info="LR_TPE"),
                  "model_type": "RNN", "lr": lr, "n_epochs": epochs, "early_stop": stop, "logdir": os.path.join(workdir, "tra"), "seed": int(seed), "memory_mode": "sample",
                  "eval_train": False, "eval_test": False, "pretrain": True}
    else:
        raise ValueError(algorithm)
    kwargs.update(extra)
    if algorithm == "adarnn" and gpu < 0:
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    model = cls(**kwargs)
    if algorithm == "tcts" and gpu < 0:
        model.device = torch.device("cpu")
        model.use_gpu = False
    return model


def _market_state(model, market):
    """ADD's market state is the day's average label cut in thirds. With a ranked or demeaned label that average is the same every day, so the
    thirds are cut from `market` (a day-indexed Series of the day's average raw return) instead."""
    import types

    def fit_thresh(self, train_label):
        days = train_label.index.get_level_values("datetime").unique()
        self.lo, self.hi = market.reindex(days).quantile([1 / 3, 2 / 3])

    def gen_market_label(self, df, raw_label):
        value = market.reindex(df.index.get_level_values("datetime")).to_numpy()
        df = df.copy()
        df[("market_return", "market_return")] = pd.cut(value, [-np.inf, self.lo, self.hi, np.inf], labels=False)
        return df
    model.fit_thresh = types.MethodType(fit_thresh, model)
    model.gen_market_label = types.MethodType(gen_market_label, model)


def fit_qlib(algorithm, train, valid, params, *, d_feat, lookback, seed=42, reproducible=True, threads=1, device="cpu", workdir, on_epoch=None, names, horizon=2,
             engine=None, aux=None, concepts=None, market=None):
    """Train one window with Qlib's own `fit(dataset)`. `train` and `valid` hold `x` (`[N, F × L]`), `y`, `t`, `i`; `aux` the auxiliary labels (TCTS),
    `concepts` {matrix, concepts} (HIST). Returns (model, curve, best epoch). `on_epoch` hears every epoch the network reports."""
    if algorithm == "tra" and int(lookback) <= int(horizon):
        raise DeepFailure({"stage": "settings", "title": i18n.t("the look back is shorter than the label's reach"),
                           "message": i18n.t("TRA needs a look back longer than the {horizon} days its label reads ahead; now {lookback}.", horizon=int(horizon), lookback=int(lookback))})
    torch.set_num_threads(max(1, int(threads)))
    if reproducible:
        torch.use_deterministic_algorithms(True)
    np.random.seed(int(seed))
    torch.manual_seed(int(seed))
    os.makedirs(workdir, exist_ok=True)
    _qlib_init(os.path.join(workdir, "qlib"))
    held = None
    if algorithm == "hist":
        found = dict(concepts)
        unknown = 733  # Qlib's own index for a name it does not know: its row is all zeros
        matrix = np.zeros((max(len(names), unknown) + 1, np.asarray(found["matrix"]).shape[1]), dtype=np.float32)
        matrix[:len(found["matrix"])] = found["matrix"]
        np.save(os.path.join(workdir, "stock2concept.npy"), matrix, allow_pickle=False)
        with open(os.path.join(workdir, "stock_index.json"), "w", encoding="utf-8") as stream:
            import json
            json.dump({name: n for n, name in enumerate(names)}, stream)
        held = {"matrix_file": os.path.join(workdir, "stock2concept.npy"), "index_file": os.path.join(workdir, "stock_index.json")}
    n_aux = len(aux["train"]) if aux else 0
    model = build_qlib(algorithm, params, d_feat, lookback, seed, workdir=workdir, device=device, engine=engine, concepts=held, n_aux=n_aux)
    daily = [f * lookback + lookback - 1 for f in range(d_feat)] if algorithm == "tra" else None
    frame = _qlib_frame([train, valid], names, aux=[aux["train"], aux["valid"]] if aux else None, daily=daily)
    first, last = int(min(train["t"])), int(max(train["t"]))
    v_first, v_last = int(min(valid["t"])), int(max(valid["t"]))
    segments = {"train": (_stamps([first])[0], _stamps([last])[0]), "valid": (_stamps([v_first])[0], _stamps([v_last])[0]), "test": (_stamps([v_first])[0], _stamps([v_last])[0])}
    dataset = _dataset(algorithm, frame, segments, lookback=lookback, horizon=horizon, states=params.get("states", 3), batch=params.get("batch", 800), d_feat=d_feat)
    epochs = int(params.get("epochs", 100)) * (2 if algorithm == "tra" else 1)  # TRA pretrains for as many epochs first
    higher = _score_direction(algorithm, params)
    seen = {"train": [], "valid": [], "best": None, "best_epoch": 0}

    def heard(count, score):
        if isinstance(score, dict):  # TRA reports a dict of measures; it stops on the IC
            score = score["IC"]
        seen["valid"].append(float(score))
        mine = float(score)
        if not math.isfinite(mine):
            raise DeepFailure({"stage": "compute", "title": i18n.t("the validation score became NaN at epoch {epoch}", epoch=count), "message": i18n.t("the validation score became NaN at epoch {epoch}; lower the learning rate", epoch=count), "epoch": count})
        if seen["best"] is None or (mine > seen["best"] if higher else mine < seen["best"]):
            seen.update(best=mine, best_epoch=count)
        if on_epoch is not None:
            train_loss = seen["train"][count - 1] if len(seen["train"]) >= count else None
            on_epoch(count, epochs, train_loss, mine, seen["best"], seen["best_epoch"])
    evals = _Evals(heard)
    if algorithm == "add" and market is not None:
        _market_state(model, market)
    if algorithm in ("tcts", "add"):
        # these two do not fill `evals_result`: hear their `test_epoch`, called on the training rows and then the validation rows each epoch
        original = model.test_epoch
        calls = {"n": 0}

        def test_epoch(*args, **kwargs):
            value = original(*args, **kwargs)
            calls["n"] += 1
            score = value[model.metric] if isinstance(value, dict) else value
            if calls["n"] % 2 == 1:
                seen["train"].append(float(score))
            else:
                heard(calls["n"] // 2, score)
            return value
        model.test_epoch = lambda *a, **k: test_epoch(*a, **k)
        try:
            model.fit(dataset, save_path=os.path.join(workdir, f"{algorithm}.bin"))
        finally:
            del model.test_epoch  # back to the class's own method (an instance attribute cannot be kept)
            for patched in ("fit_thresh", "gen_market_label"):
                model.__dict__.pop(patched, None)
    else:
        model.fit(dataset, evals_result=evals)
        trained = evals.get("train", [])
        seen["train"] = [float(v) for v in trained]
    curve = {"train": seen["train"], "valid": seen["valid"], "unit": "epoch"}
    return model, curve, int(seen["best_epoch"] or len(seen["valid"]))


class QlibFitted:
    """One window's network trained by Qlib's own fit, shaped like `NeuralFitted`. Rows are predicted through a dataset built from them."""

    neural = True
    qlib_fit = True

    def __init__(self, algorithm, model, features, curve, best, *, params, d_feat, lookback, seed, engine=None, objective="l2", names=None, horizon=2, tail=None):
        self.algorithm, self.model, self.features, self.curve, self.best = algorithm, model, features, curve, best
        self.params, self.d_feat, self.lookback, self.seed, self.engine, self.objective, self.k = params, d_feat, lookback, seed, engine, objective, 5
        self.names, self.horizon, self.tail = names or [], horizon, tail
        self.source = None
        self.valid_rows = None
        self.context = None

    @property
    def width(self):
        return self.lookback if self.d_feat == len(self.features) else 1

    def _context(self, keep_days=None):
        """Earlier rows a network reads when it scores later ones: TRA's memory (the last days of training and validation) and IGMTF's training rows
        (its predict recomputes their hidden states). A saved window keeps the last `keep_days` days only. IGMTF then picks its nearest days from those instead of
        from every training day: measured on a window trained on 394 days, the day-by-day rank correlation of the two sets of predictions is above 0.9999 (largest
        difference 0.8% of the prediction range), and `test_igmtf_keeping_120_days_predicts_as_keeping_all` holds it at 0.99."""
        if self.tail is not None:
            return self.tail
        if self.context is None or self.algorithm not in ("tra", "igmtf"):
            return None
        rows = [self.context[0], self.context[1]] if self.algorithm == "tra" else [self.context[0]]
        last = max(int(max(r["t"])) for r in rows)
        days = keep_days or (self.lookback + self.horizon + 5 if self.algorithm == "tra" else None)
        keep = [np.asarray(r["t"]) > last - days if days else np.ones(len(r["t"]), dtype=bool) for r in rows]
        return {key: np.concatenate([np.asarray(r[key])[k] for r, k in zip(rows, keep)]) for key in ("x", "t", "i", "y")}

    def predict_array(self, x, t, i):
        t, i = np.asarray(t), np.asarray(i)
        part = {"x": np.asarray(x, dtype=np.float32), "t": t, "i": i, "y": np.zeros(len(t), dtype=np.float32)}
        tail = self._context()
        parts = [part] if tail is None else [tail, part]
        daily = [f * self.lookback + self.lookback - 1 for f in range(self.d_feat)] if self.algorithm == "tra" else None
        frame = _qlib_frame(parts, self.names, daily=daily)
        first, last = int(min(t)), int(max(t))
        segments = {"test": (_stamps([first])[0], _stamps([last])[0])}
        if tail is not None:
            segments["train"] = (_stamps([int(min(tail["t"]))])[0], _stamps([int(max(tail["t"]))])[0])
            segments["valid"] = segments["train"]
        dataset = _dataset(self.algorithm, frame, segments, lookback=self.lookback, horizon=self.horizon, states=self.params.get("states", 3), batch=self.params.get("batch", 800))
        with torch.no_grad():
            pred = self.model.predict(dataset) if self.algorithm == "tcts" else self.model.predict(dataset, "test")
        pred = pred.iloc[:, 0] if isinstance(pred, pd.DataFrame) else pred
        index = pd.MultiIndex.from_arrays([_stamps(t), np.asarray(self.names, dtype=object)[i]])
        pred = pred[~pred.index.duplicated(keep="last")]
        return np.nan_to_num(pred.reindex(index).to_numpy(dtype=float))

    def predict_rows(self, t, i):
        return self.predict_array(self.source(t, i), t, i)

    def predict(self, x):
        raise ValueError(f"{self.algorithm} reads rows by day and name; use predict_rows")

    def importance(self, x=None):
        if self.valid_rows is None:
            return {name: 0.0 for name in self.features}
        return permutation_importance(self)

    def contributions(self, x):
        return None

    def contributions_rows(self, t, i, steps=64):
        """Integrated gradients for one day's rows, as every network has them: the network's prediction as a function of the inputs alone, what it reads besides
        the inputs (concept membership, training memory) held as the day's prediction read it. Every row's values add up to its prediction less the baseline's."""
        x = self.source(t, i)
        forward = qlib_forward(self, x, t, i)
        return integrated_gradients_of(forward, x, self.width, len(self.features), steps, chunk=max(len(x), 1))


def _hist_forward(net, x, concept, fixed=None):
    """Qlib's `HISTModel.forward`, split so the day's concept vectors (what the whole day's stocks pool into each concept) can be given instead of recomputed:
    with them held, a row's prediction depends on its own inputs alone. Returns the prediction and the vectors it used."""
    h = net.rnn(x.reshape(len(x), net.d_feat, -1).permute(0, 2, 1))[0][:, -1, :]
    if fixed is None:
        total = torch.sum(concept, 0).reshape(1, -1).repeat(concept.shape[0], 1).mul(concept) + torch.ones(concept.shape[0], concept.shape[1])
        hidden = torch.t(concept / total).mm(h)
        hidden_e = hidden[hidden.sum(1) != 0]
    else:
        hidden_e = fixed["e"]
    e_info = net.fc_es(net.softmax_t2s(net.cal_cos_similarity(h, hidden_e)).mm(hidden_e))
    e_back = net.fc_es_back(e_info)
    out_es = net.leaky_relu(net.fc_es_fore(e_info))
    i_info = h - e_back
    if fixed is None:
        sim = net.cal_cos_similarity(i_info, i_info)
        dim = sim.shape[0]
        diag = sim.diagonal(0)
        sim = sim * (torch.ones(dim, dim) - torch.eye(dim))
        row = torch.linspace(0, dim - 1, dim).long()
        column = sim.max(1)[1].long()
        value = sim.max(1)[0]
        sim[row, column] = 10
        sim[sim != 10] = 0
        sim[row, column] = value
        sim = sim + torch.diag_embed((sim.sum(0) != 0).float() * diag)
        hidden = torch.t(i_info).mm(sim).t()
        hidden_i = hidden[hidden.sum(1) != 0]
    else:
        hidden_i = fixed["i"]
    i_info = net.fc_is(net.softmax_t2s(net.cal_cos_similarity(i_info, hidden_i)).mm(hidden_i))
    i_back = net.fc_is_back(i_info)
    out_is = net.leaky_relu(net.fc_is_fore(i_info))
    out_indi = net.leaky_relu(net.fc_indi(h - e_back - i_back))
    return net.fc_out(out_es + out_is + out_indi).reshape(-1), {"e": hidden_e.detach(), "i": hidden_i.detach()}


def _igmtf_forward(net, x, memory, fixed=None, k_day=10, n_neighbor=10):
    """Qlib's `IGMTFModel.forward` with the day's neighbours held: which training days and which of their rows each row looks at are chosen from the day's
    actual inputs (`fixed`, as the prediction chose them); a row's prediction then depends on its own inputs alone."""
    out = net.rnn(x.reshape(len(x), net.d_feat, -1).permute(0, 2, 1))[0][:, -1, :]
    mini = net.lins(out)
    if fixed is None:
        train_hidden, train_hidden_day = memory
        day = net.cal_cos_similarity(torch.mean(mini, dim=0).unsqueeze(0), train_hidden_day)
        day_index = torch.topk(day, k_day, dim=1)[1]
        sample = net.lins(torch.cat(list(train_hidden[day_index.long().cpu()].squeeze())))
        columns = torch.topk(net.cal_cos_similarity(net.project1(mini), net.project2(sample)), n_neighbor, dim=1)[1]
        fixed = {"memory": sample.detach(), "columns": columns}
    keys = net.project2(fixed["memory"])
    similarity = net.cal_cos_similarity(net.project1(mini), keys).gather(1, fixed["columns"])
    aggregated = ((similarity / fixed["columns"].shape[1]).unsqueeze(-1) * keys[fixed["columns"]]).sum(1)
    return net.fc_out_pred(torch.cat([mini, aggregated], dim=1)).reshape(-1), fixed


def qlib_forward(fitted, x, t, i):
    """The prediction of a network Qlib's own fit trains, as a function `tensor [N, F x width] -> tensor [N]` of one day's inputs, with what the network reads
    besides them held at the day's actual values."""
    algorithm, model = fitted.algorithm, fitted.model
    flat = lambda point: point.reshape(len(point), fitted.d_feat, -1).permute(0, 2, 1)  # noqa: E731
    if algorithm == "tcts":
        net = model.fore_model
        net.eval()
        net.to("cpu")

        def forward(point):
            result = net(point)
            return result[:, 0] if result.dim() > 1 else result
        return forward
    if algorithm == "add":
        net = model.ADD_model
        net.eval()
        net.to("cpu")
        return lambda point: net(point)["excess"].reshape(-1)
    if algorithm == "adarnn":
        net = model.model
        net.eval()
        net.to("cpu")
        return lambda point: net.predict(flat(point)).reshape(-1)
    if algorithm == "tra":
        backbone, tra = model.model, model.tra
        backbone.eval()
        tra.eval()
        backbone.to("cpu")
        tra.to("cpu")
        # predictions are the mean of the predictors (the router only picks a transport during training; `transport_method` is none)
        return lambda point: tra.predictors(backbone(flat(point))).mean(dim=1)
    actual = torch.from_numpy(np.asarray(x, dtype=np.float32))
    if algorithm == "hist":
        import json
        net = model.HIST_model
        net.eval()
        net.to("cpu")
        if not (os.path.isfile(model.stock2concept) and os.path.isfile(model.stock_index)):
            raise ValueError("This window's concept table was not kept")
        matrix = np.load(model.stock2concept, allow_pickle=False)
        with open(model.stock_index, encoding="utf-8") as stream:
            index = json.load(stream)
        rows = [index.get(name, 733) for name in np.asarray(fitted.names, dtype=object)[np.asarray(i)]]
        concept = torch.from_numpy(matrix[rows]).float()
        with torch.no_grad():
            fixed = _hist_forward(net, actual, concept)[1]
        return lambda point: _hist_forward(net, point, concept, fixed)[0]
    if algorithm == "igmtf":
        net = model.igmtf_model
        net.eval()
        net.to("cpu")
        tail = fitted._context()
        if tail is None:
            raise ValueError("This window's training rows were not kept")
        part = {"x": np.asarray(x, dtype=np.float32), "t": np.asarray(t), "i": np.asarray(i), "y": np.zeros(len(t), dtype=np.float32)}
        frame = _qlib_frame([tail, part], fitted.names)
        span = (_stamps([int(min(tail["t"]))])[0], _stamps([int(max(tail["t"]))])[0])
        dataset = _dataset("igmtf", frame, {"train": span, "valid": span, "test": (_stamps([int(min(t))])[0], _stamps([int(max(t))])[0])}, lookback=fitted.lookback,
                           horizon=fitted.horizon, states=3, batch=fitted.params.get("batch", 800))
        from qlib.data.dataset.handler import DataHandlerLP
        model.device = torch.device("cpu")
        memory = model.get_train_hidden(dataset.prepare("train", col_set="feature", data_key=DataHandlerLP.DK_L))
        with torch.no_grad():
            fixed = _igmtf_forward(net, actual, memory)[1]
        return lambda point: _igmtf_forward(net, point, None, fixed)[0]
    raise ValueError(f"Contributions by input are not defined for {algorithm}")


def save_qlib(fitted, path):
    import pickle
    tail = fitted._context(keep_days=120)
    torch.save({"kind": "qlib", "algorithm": fitted.algorithm, "model": pickle.dumps(fitted.model, protocol=5), "features": fitted.features, "curve": fitted.curve,
                "best": fitted.best, "params": fitted.params, "d_feat": fitted.d_feat, "lookback": fitted.lookback, "seed": fitted.seed, "engine": fitted.engine,
                "objective": fitted.objective, "names": fitted.names, "horizon": fitted.horizon, "tail": tail}, path)


def load_qlib(found):
    import pickle
    model = pickle.loads(found["model"])  # noqa: S301
    if found["algorithm"] == "tra" and not hasattr(model, "_writer"):
        model._writer = None
    return QlibFitted(found["algorithm"], model, found["features"], found["curve"], found["best"], params=found["params"], d_feat=found["d_feat"],  # noqa: S301 - the run's own window cache
                      lookback=found["lookback"], seed=found["seed"], engine=found.get("engine"), objective=found.get("objective", "l2"), names=found.get("names"),
                      horizon=found.get("horizon", 2), tail=found.get("tail"))
