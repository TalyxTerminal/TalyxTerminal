"""The research process of one model run: the standard pipeline or the model's own code, trained window by window.

The Host writes the job (`invocation.json`): the frozen data, the inputs as the run fixed them, the settings, the windows to train and
the run's folders. The code (TALYX's `model_standard.py`, or the project's entry) runs with `talyx_quant` bound to this run: its
`q.model_data`, `q.fit`, `q.record` and `q.predict` come here. Each window's model, numbers and predictions are kept in the run's
window cache as soon as it is done, so a stopped or failed run continues from the windows it finished; the same cache lets a
publication retrain only its cut-off window, and an update train only the newest window.

Files written in the output folder: `progress.jsonl` (one line a step, read by the Host every second), `work.json` (the key numbers,
baselines, checks and windows), `results.json` (the Results sections), `figures.json` (one set of figures for the whole run),
`pred.npy` (the test predictions, bars × names), and `answer.json` (ok, or the failure).

Modes: train (a run, resumed or not), values (one day's predictions and contributions), cutoff (the future-data check of a
publication), update (the newest values of a publication) and combine (the mean of several runs' predictions).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import pickle
import re
import runpy
import shutil
import sys
import time
import traceback
from pathlib import Path

native_directory = Path(__file__).resolve().parent
sys.path[:] = [path for path in sys.path if Path(path or os.getcwd()).resolve() != native_directory]
sys.path.insert(0, str(native_directory.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from native import i18n
from native import metrics as M  # noqa: E402
from native import model_algorithms as A  # noqa: E402
from native import model_data as D  # noqa: E402
from native.process_memory import available_memory, peak_memory  # noqa: E402,F401

GB = 1024 ** 3
CURRENT = None


class RunFailure(Exception):
    def __init__(self, failure):
        super().__init__(failure["message"])
        self.failure = failure


def progress(output, stage, **values):
    with open(Path(output) / "progress.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": int(time.time() * 1000), "stage": stage, **values}, default=_plain) + "\n")


def _plain(value):
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.bool_):
        return bool(value)
    raise TypeError(type(value).__name__)


def write_json(path, value):
    from native.model_figures import _clean
    Path(path).write_text(json.dumps(_clean(value), allow_nan=False, default=_plain), encoding="utf-8")


def _day(label):
    moment = pd.Timestamp(int(label), unit="ms", tz="UTC")
    return i18n.t("{month} {day}, {year}", month=moment.strftime(i18n.t("%b")), day=moment.day, year=moment.year)


# ---------------------------------------------------------------------------------------------------------------- data
def load_panel(job):
    from native import factor_resident as R
    panel = R.load_panel(job["data"])
    bars = pd.read_parquet(job["data"]["barsPath"], columns=["label", "time"])
    first = bars.groupby("label")["time"].min()
    panel.open_times = first.reindex(panel.labels).to_numpy(dtype="int64", na_value=0)
    held = job.get("heldOutFrom")
    if held is not None and job.get("kind") != "heldout":
        cut = panel.truncate(int(held))
        cut.open_times = panel.open_times[:len(cut.labels)]
        panel = cut
    cutoff = job.get("cutoff")
    if cutoff is not None:
        cut = panel.truncate(int(cutoff) + 1)
        cut.open_times = panel.open_times[:len(cut.labels)]
        panel = cut
    return panel


def inputs_of(job):
    out = []
    for item in job["inputs"]:
        out.append(D.Input(id=item["id"], name=item["name"], source=item["source"], ref=item.get("ref", ""), formula=item.get("formula"), path=item.get("path"), only=D.kept_columns((job.get("params") or {}).get("keep_columns")) if item["source"] == "builtin" else []))
    return out


def dataset_key(job, panel):
    """What decides the data set apart from the bars: the inputs (by content, not by the folder they were copied to), the processing,
    the label and who is left out, and the code."""
    inputs = [{key: value for key, value in item.items() if key != "path"} for item in job["inputs"]]
    spec = {"first": int(panel.labels[0]), "names": list(panel.columns), "inputs": inputs,
            "settings": {key: job["settings"].get(key) for key in ("processing", "label", "leaveOut")}, "code": (job.get("code") or {}).get("digest")}
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:24]


# ---------------------------------------------------------------------------------------------------------------- the q API
class Segment:
    def __init__(self, part, features):
        self.x, self.y, self.t, self.i = part["x"], part["y"], part["t"], part["i"]
        self.features = features

    def frame(self):
        """The rows as a table: one column per feature, indexed by (bar position, name position)."""
        return pd.DataFrame(self.x, index=pd.MultiIndex.from_arrays([self.t, self.i], names=["bar", "name"]), columns=self.features)

    def __len__(self):
        return len(self.t)


class WindowView:
    """One walk-forward window as code sees it: number, spans, and its training, validation and test rows (with the processing fitted on
    its training rows)."""

    def __init__(self, run, window):
        self._run, self._window = run, window
        self.k = window.k
        self._parts = None

    @property
    def parts(self):
        if self._parts is None:
            self._parts = self._run.data.matrices(self._window)
            if not len(self._parts["train"]["t"]):
                raise RunFailure(self._run.empty_window(self._window))
        return self._parts

    train = property(lambda self: Segment(self.parts["train"], self._run.data.features))
    valid = property(lambda self: Segment(self.parts["valid"], self._run.data.features))
    test = property(lambda self: Segment(self.parts["test"], self._run.data.features))

    @property
    def spans(self):
        return self._window.spans(self._run.panel.labels)


class ModelDataView:
    def __init__(self, run):
        self._run = run
        self.features = run.data.features
        self.x, self.y = run.data.x, run.data.y

    def windows(self):
        """The windows this run trains: the ones its window cache does not hold yet (all of them for a fresh run)."""
        return [WindowView(self._run, window) for window in self._run.to_train()]


def values_days(windows, lookback, total):
    """The days of the input tensor a day's values can read: each window's test days and the `lookback` days before them (a sequence network reads them)."""
    keep = np.zeros(total, dtype=bool)
    for window in windows:
        first, last = window.test
        keep[max(int(first) - int(lookback) + 1, 0):int(last) + 1] = True
    return np.nonzero(keep)[0]


def values_inputs_bytes(days, names, features):
    """What the kept inputs take: float32, days x names x features."""
    return int(days) * int(names) * int(features) * 4


class Training:
    """One run's state behind `talyx_quant`'s model calls."""

    def __init__(self, job):
        self.job = job
        self.output = Path(job["output"])
        self.cache = Path(job["cache"])
        self.cache.mkdir(parents=True, exist_ok=True)
        self.panel = load_panel(job)
        self.data = None
        self.recorded = {}
        self.predictions = None
        self.threads = int(job.get("threads") or 1)
        self.started = time.perf_counter()
        self.pred = None
        self.ridge = None

    # data ----------------------------------------------------------------------------------------------------------------
    def model_data(self, inputs=None, *, leave_out=None, processing=None, label=None, split=None):
        if self.data is not None:
            return ModelDataView(self)
        settings = self.job["settings"]
        merged = {"leaveOut": leave_out or settings.get("leaveOut") or D.LEAVE_OUT_DEFAULT, "processing": processing or settings["processing"],
                  "label": label or settings["label"], "split": split or settings["split"]}
        merged["processing"]["inputs"].setdefault("takeOut", [])
        self.job["settings"] = {**settings, **merged}
        code = self._code_input if self.job.get("code") else None
        kept = self.kept_inputs(merged)
        if kept is not None:
            self.data = kept
            progress(self.output, "inputs", state="done", features=len(self.data.features), windows=len(self.data.windows), kept=True)
        else:
            progress(self.output, "inputs", state="running")
            try:
                self.data = D.ModelData(self.panel, inputs_of(self.job), merged, code=code)
            except D.DataError as error:
                raise RunFailure(error.failure) from error
            progress(self.output, "inputs", state="done", features=len(self.data.features), windows=len(self.data.windows))
            self.keep_inputs(merged)
        self.pred = np.full(self.panel.shape, np.nan)
        self.ridge = np.full(self.panel.shape, np.nan)
        self._load_cached()
        return ModelDataView(self)

    # The inputs of a run's days of values: built once for the run (every window of it), kept beside the day files, read back for the next day --------------
    def inputs_file(self, merged):
        """Where a values job keeps the run's built inputs, and the key that says they are this run's (its data, settings, inputs and code); None outside values jobs."""
        if self.job.get("mode") != "values":
            return None, None
        import hashlib
        job = self.job
        key = hashlib.sha256(json.dumps({"data": job["data"].get("key"), "settings": merged, "inputs": job.get("inputs"), "code": (job.get("code") or {}).get("digest"),
                                         "cutoff": job.get("cutoff"), "heldOutFrom": job.get("heldOutFrom"), "run": str(job.get("runOutput"))}, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]
        return Path(job["output"]).parent / "inputs.pkl", key

    def kept_inputs(self, merged):
        path, key = self.inputs_file(merged)
        if path is None or not path.is_file():
            return None
        try:
            with open(path, "rb") as stream:
                found = pickle.load(stream)  # noqa: S301 - this run's own file, written by keep_inputs
            if found.get("key") != key:
                return None
            data = found["data"]
            data.panel = self.panel
            # Only the days Values can ask for were kept: the rest of the tensor is zeros that are never read (and never take memory until written).
            x = np.zeros(found["shape"], dtype=np.float32)
            x[found["days"]] = data.x
            data.x = x
            return data
        except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ImportError, KeyError) as error:   # a kept file this code cannot read is built again
            progress(self.output, "inputs", state="rebuilt", reason=f"{type(error).__name__}: {error}")   # the run's log says why it took longer
            return None

    def keep_inputs(self, merged):
        """Keep what a day's values read of the built inputs: the test days of every window and the look back before them, as float32; not the training rows
        and not the raw copy the report reads. The size is worked out from the shapes first and logged."""
        import copy
        path, key = self.inputs_file(merged)
        if path is None:
            return
        data = self.data
        days = values_days(data.windows, int(self.job.get("params", {}).get("nn_lookback") or 20) + 1 if (self.job.get("params") or {}).get("algorithm") in A.NEURAL_IDS else 1, data.x.shape[0])
        shape = data.x.shape
        size = values_inputs_bytes(len(days), shape[1], shape[2])
        reduced = copy.copy(data)
        reduced.panel = None
        reduced.coverage_raw = None
        reduced.x = np.ascontiguousarray(data.x[days], dtype=np.float32)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            with open(temporary, "wb") as stream:
                pickle.dump({"key": key, "data": reduced, "days": days, "shape": shape}, stream, protocol=5)
            temporary.replace(path)
            progress(self.output, "inputs", state="kept", days=int(len(days)), of=int(shape[0]), bytes=int(size), file=int(path.stat().st_size))
        except OSError as error:   # a full or locked disk: the next day's values build the inputs again, and the run's log says so
            progress(self.output, "inputs", state="not kept", reason=str(error))
            with contextlib.suppress(OSError):
                path.with_suffix(".tmp").unlink()

    def _code_input(self, item):
        """An input written as a function in the project (`file.py:function`), called with the run's data."""
        import importlib.util

        import talyx_quant as q
        file, _, function = item.ref.partition(":")
        path = Path(self.job["code"]["source"]) / file
        spec = importlib.util.spec_from_file_location(f"_talyx_feature_{Path(file).stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found = getattr(module, function)
        q._Run.data = q.ResearchData.from_panel(self.panel)
        table = found(q._Run.data)
        ready = getattr(found, "_talyx_ready", "close")
        item.ready = ready
        if ready == "next_close":
            item.note = "Declared ready at the next close; the label starts at the next open"
        from native.factor_resident import _wide
        return _wide(table, self.panel, item.name)

    def digest(self, window):
        """What a cached window must match to be used again: the data set, the settings (algorithm and parameters included) and the
        window's training and validation segments."""
        spans = window.spans(self.panel.labels)
        spec = {"dataset": dataset_key(self.job, self.panel), "params": self.job.get("params"), "train": spans["train"], "valid": spans["valid"]}
        return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:24]

    def _load_cached(self):
        """Windows the cache holds already. A fresh run's cache is empty; a resumed run, a publication's check and its updates keep the
        windows whose training and validation segments are the same (`keepDone`), even when later bars have arrived since."""
        keep = bool(self.job.get("keepDone"))
        if self.job.get("mode") == "cutoff":
            return  # the check retrains its window on the cut data; nothing cached may stand in for it
        for window in self.data.windows:
            folder = self.cache / str(window.k)
            meta = folder / "window.json"
            if not meta.is_file():
                continue
            found = json.loads(meta.read_text(encoding="utf-8"))
            spans = window.spans(self.panel.labels)
            same = found["train"] == spans["train"] and found["valid"] == spans["valid"] if keep else found.get("digest") == self.digest(window)
            if not same:
                continue
            stored = np.load(folder / "test.npz")
            inside = stored["t"] < len(self.panel.labels)  # a run cut earlier than the cache's keeps the predictions up to its end
            t, i = stored["t"][inside], stored["i"][inside]
            self.pred[t, i] = stored["pred"][inside]
            if "ridge" in stored:
                self.ridge[t, i] = stored["ridge"][inside]
            self.recorded[window.k] = found

    def to_train(self):
        only = self.job.get("windows")
        chosen = [window for window in self.data.windows if window.k not in self.recorded and (only is None or window.k in only)]
        self.total = len(self.data.windows)
        return chosen

    def empty_window(self, window):
        """Why a window has no training rows, naming an input that has no values there."""
        first, last = window.train
        names = []
        for f, name in enumerate(self.data.features):
            if np.isnan(self.data.x[first:last + 1, :, f]).all():
                names.append(name)
        span = i18n.t("{start} to {end}", start=_day(self.panel.labels[first]), end=_day(self.panel.labels[last]))
        if len(names) > 1:
            detail = i18n.t("{name} has no values from {span} (and {others} more inputs), and rows with a missing input are dropped.",
                            name=names[0], span=span, others=len(names) - 1)
        elif names:
            detail = i18n.t("{name} has no values from {span}, and rows with a missing input are dropped.", name=names[0], span=span)
        else:
            detail = i18n.t("No name has a label and every input from {span}.", span=span)
        return {"stage": "compute", "window": window.k, "title": i18n.t("Window {window} of {total} has no training rows", window=window.k, total=len(self.data.windows)), "message": detail, "detail": detail}

    # training ------------------------------------------------------------------------------------------------------------
    def fit(self, algorithm, window, params=None, *, seed=42, reproducible=True):
        if not algorithm:
            raise RunFailure({"stage": "code", "title": i18n.t("no algorithm"), "message": i18n.t("Choose an algorithm in the preparation first.")})
        self.job.setdefault("algorithm", algorithm)
        self.job.setdefault("algorithmParams", params or {})
        self.job.setdefault("seed", seed)
        if algorithm in A.NEURAL_IDS:
            return self.fit_neural(algorithm, window, params or {}, seed=seed, reproducible=reproducible)
        parts = window.parts
        last = {"at": 0.0}
        total_windows = len(self.data.windows)

        def on_round(done, total):
            now = time.time()
            if now - last["at"] >= 1.0:
                last["at"] = now
                progress(self.output, "window", state="running", window=window.k, windows=total_windows, round=done, rounds=total,
                         fraction=round(done / max(total, 1), 3))
        progress(self.output, "window", state="running", window=window.k, windows=total_windows, round=0, fraction=0.0)
        started = time.perf_counter()
        try:
            label = self.job["settings"]["label"]
            objective = label.get("objective", "l2")
            if objective not in A.SUPPORTS.get(algorithm, ["l2"]):
                raise RunFailure({"stage": "settings", "title": i18n.t("objective not available"), "message": i18n.t("{algorithm} cannot train {objective}; choose another objective.", algorithm=i18n.t(A.BY_ID[algorithm]['label']), objective=objective)})
            if label.get("weight", "equal") != "equal" and not A.takes_weight(algorithm, objective):
                parts = {key: {name: value for name, value in part.items() if name != "w"} if isinstance(part, dict) else part for key, part in parts.items()}
            engine = A.engine_params(algorithm, (self.job.get("params") or {}).get("engine_params", ""), declared=set(A.CONTROL_NAMES.get(algorithm, {}).values()))
            fitted = A.fit(algorithm, parts, self.data.features, params or {}, seed=seed, reproducible=reproducible, threads=self.threads, on_round=on_round,
                           objective=objective, objective_param=label.get("objectiveParam"), engine=engine)
        except MemoryError as error:
            raise RunFailure(self.out_of_memory(window)) from error
        except Exception as error:
            if "bad_alloc" in str(error) or "Memory" in str(error) and "alloc" in str(error).lower():
                raise RunFailure(self.out_of_memory(window)) from error
            raise
        fitted.seconds = time.perf_counter() - started
        return fitted

    def neural_source(self, record, lookback):
        """`source(t, i)`: rows as `[N, F × L]`, the look back processed as this window's training fitted it."""
        from native import model_deep as ND
        fitted = record.get("fitted") if isinstance(record, dict) else record
        fitted = None if fitted is None else {key: np.asarray(value) for key, value in fitted.items()}
        settings = self.data.settings["processing"]["inputs"]
        if lookback == 60 and self.alpha360_only():
            # Alpha360 alone already is 5 fields × 60 days, fields outside and time oldest first: Qlib's benchmark reads it as it is
            return lambda t, i: np.nan_to_num(np.asarray(D.apply_fitted(self.data.x[t, i], fitted, settings), dtype=np.float32))
        return lambda t, i: ND.sequences(self.data.x, t, i, lookback, process=lambda flat: D.apply_fitted(flat, fitted, settings))

    def alpha360_only(self):
        # the whole of Alpha360 is six fields over 60 days, which a network reads as a sequence; some of its columns are plain features
        return len(self.data.features) == 300 and all(name.startswith("Alpha360.") for name in self.data.features)

    def fit_neural(self, algorithm, window, params, *, seed, reproducible):
        from native import model_deep as ND
        parts = window.parts
        total_windows = len(self.data.windows)
        shape = A.BY_ID[algorithm].get("shape")
        features = self.data.features
        alpha = self.alpha360_only() and shape == "sequence"
        lookback = 60 if alpha else int(params.get("lookback", 20)) if shape == "sequence" else 1
        source = self.neural_source({"fitted": parts["fitted"]}, lookback)
        own = {entry[0] for entry in A.BY_ID[algorithm]["params"]}
        checked = A.checked_params(algorithm, {key: value for key, value in params.items() if key in own})
        device = params.get("device") or ND_device()
        engine = A.engine_params(algorithm, (self.job.get("params") or {}).get("engine_params", ""))
        label = self.job["settings"]["label"]
        base = self.job.get("baseRun")
        if algorithm == "gats" and base:
            # the base run's same window, when it has one; its algorithm is GATs's recurrent base
            found = Path(base["windows"]) / str(window.k) / "model.pt"
            checked = {**checked, "base_model": base["algorithm"], **({"base_state": str(found)} if found.is_file() else {})}
        folder = self.cache / str(window.k)
        folder.mkdir(parents=True, exist_ok=True)
        digest = self.digest(window._window)
        last = {"at": 0.0}
        started = time.perf_counter()

        def on_epoch(epoch, epochs, train_loss, valid_loss, best_loss, best_epoch, pretrain=None):
            now = time.time()
            if now - last["at"] < 1.0 and epoch < epochs:
                return
            last["at"] = now
            progress(self.output, "window", state="running", window=window.k, windows=total_windows, epoch=epoch, epochs=epochs, trainLoss=train_loss,
                     validLoss=valid_loss, bestLoss=best_loss, bestEpoch=best_epoch, fraction=round(epoch / max(epochs, 1), 3),
                     **({"pretrain": pretrain[0], "pretrains": pretrain[1]} if pretrain else {}))
        progress(self.output, "window", state="running", window=window.k, windows=total_windows, epoch=0, epochs=int(checked["epochs"]), fraction=0.0)
        train = {**parts["train"], "x": source(parts["train"]["t"], parts["train"]["i"])}
        valid = {**parts["valid"], "x": source(parts["valid"]["t"], parts["valid"]["i"])}
        if algorithm == "mlp" and label.get("objective") == "binary":
            checked = {**checked, "loss": "binary"}
            train = {**train, "y": A._targets("binary", 5, train["y"], train["t"])}
            valid = {**valid, "y": A._targets("binary", 5, valid["y"], valid["t"])}
        if algorithm in A.QLIB_FIT_IDS:
            return self.fit_qlib(algorithm, window, checked, train, valid, source, lookback, folder, on_epoch, seed=seed, reproducible=reproducible, engine=engine,
                                 alpha=alpha, started=started, label=label)
        try:
            model, curve, best = ND.fit_network(algorithm, train, valid, checked, d_feat=5 if alpha else len(features), lookback=lookback, seed=seed, reproducible=reproducible,
                                                threads=self.threads, device=device, checkpoint=str(folder / "epoch.pt"), digest=digest, on_epoch=on_epoch, engine=engine)
        except ND.DeepFailure as error:
            raise RunFailure(self.deep_failure(error.failure, window)) from error
        except (MemoryError, RuntimeError) as error:
            if ND.oom_kind(error) is None:
                raise
            raise RunFailure(self.out_of_memory(window, kind=ND.oom_kind(error), batch=int(checked.get("batch", 800)))) from error
        fitted = ND.NeuralFitted(algorithm, model, features, curve, best, params=checked, d_feat=5 if alpha else len(features), lookback=lookback, seed=seed, engine=engine,
                                 objective=label.get("objective", "l2"))
        fitted.source = source
        fitted.valid_rows = {key: parts["valid"][key] for key in ("t", "i", "y")}
        fitted.seconds = time.perf_counter() - started
        return fitted

    def fit_network(self, net_class, net_kwargs, window, params, *, seed, reproducible):
        """`q.fit_network`: the user's own `torch.nn.Module` trained as the other networks are (epoch file, early stop, device)."""
        import torch
        from native import model_deep as ND
        parts = window.parts
        features = self.data.features
        lookback = int(params.get("lookback", 1))
        source = self.neural_source({"fitted": parts["fitted"]}, lookback)
        folder = self.cache / str(window.k)
        folder.mkdir(parents=True, exist_ok=True)
        total_windows = len(self.data.windows)
        checked = {"epochs": int(params.get("epochs", 100)), "stop": int(params.get("stop", 20)), "batch": int(params.get("batch", 800)), "lr": float(params.get("lr", 0.001)),
                   "lookback": lookback}
        last = {"at": 0.0}

        def on_epoch(epoch, epochs, train_loss, valid_loss, best_loss, best_epoch, pretrain=None):
            if time.time() - last["at"] < 1.0 and epoch < epochs:
                return
            last["at"] = time.time()
            progress(self.output, "window", state="running", window=window.k, windows=total_windows, epoch=epoch, epochs=epochs, trainLoss=train_loss, validLoss=valid_loss,
                     bestLoss=best_loss, bestEpoch=best_epoch, fraction=round(epoch / max(epochs, 1), 3))
        torch.manual_seed(int(seed))
        net = net_class(**net_kwargs)
        train = {**parts["train"], "x": source(parts["train"]["t"], parts["train"]["i"])}
        valid = {**parts["valid"], "x": source(parts["valid"]["t"], parts["valid"]["i"])}
        started = time.perf_counter()
        try:
            model, curve, best = ND.fit_network("own", train, valid, {**checked, "_module": net}, d_feat=len(features), lookback=lookback, seed=seed, reproducible=reproducible,
                                                threads=self.threads, device=params.get("device") or ND_device(), checkpoint=str(folder / "epoch.pt"), digest=self.digest(window._window),
                                                on_epoch=on_epoch)
        except ND.DeepFailure as error:
            raise RunFailure({**error.failure, "window": window.k, "title": i18n.t("Window {window}: {title}", window=window.k, title=error.failure['title'])}) from error
        fitted = ND.NeuralFitted("own", model, features, curve, best, params={**checked, "_module": net}, d_feat=len(features), lookback=lookback, seed=seed)
        fitted.source = source
        fitted.valid_rows = {key: parts["valid"][key] for key in ("t", "i", "y")}
        fitted.seconds = time.perf_counter() - started
        return fitted

    def fit_qlib(self, algorithm, window, params, train, valid, source, lookback, folder, on_epoch, *, seed, reproducible, engine, alpha, started, label):
        """A network Qlib's own `fit(dataset)` trains: one window at a time, no epoch file (a stopped run starts the window again)."""
        from native import model_deep as ND
        parts = window.parts
        extras = {}
        base = self.job.get("baseRun")
        if algorithm in ("igmtf", "add", "hist") and base:
            # the base run's same window, when it has one: its network goes in as Qlib's `model_path`
            found = Path(base["windows"]) / str(window.k) / "model.pt"
            if found.is_file():
                import torch
                saved = torch.load(found, map_location="cpu", weights_only=False)
                Path(folder).mkdir(parents=True, exist_ok=True)
                torch.save(saved["state"], Path(folder) / "base.pt")
                params = {**params, "base_model": base["algorithm"], "model_path": str(Path(folder) / "base.pt")}
        if algorithm == "tcts":
            found = self.aux_labels()
            if not found:
                raise RunFailure({"stage": "settings", "title": i18n.t("TCTS needs an auxiliary label"), "message": i18n.t("TCTS needs at least one auxiliary label; the label here is not a plain return over some days.")})
            extras["aux"] = {name: [np.where(np.isfinite(a[part["t"], part["i"]]), a[part["t"], part["i"]], part["y"]) for a in found]
                             for name, part in (("train", train), ("valid", valid))}
        if algorithm == "hist":
            found = self.concepts(window)
            if found is None:
                raise RunFailure({"stage": "settings", "title": i18n.t("HIST needs concept data"), "message": i18n.t("Market gives no concept-sector membership for these names.")})
            extras["concepts"] = {**found, "names": [self.panel.columns[n] for n in range(len(self.panel.columns))]}
        if algorithm == "add":
            raw = np.where(self.data.rows, self.data.label_raw, np.nan)
            days = np.arange(raw.shape[0])
            mean = np.nanmean(np.where(np.isfinite(raw), raw, np.nan), axis=1) if raw.size else np.zeros(0)
            extras["market"] = pd.Series(mean, index=ND._stamps(days))
        try:
            model, curve, best = ND.fit_qlib(algorithm, train, valid, params, d_feat=5 if alpha else len(self.data.features), lookback=lookback, seed=seed, threads=self.threads,
                                             device=params.get("device") or ND_device(), workdir=str(folder), on_epoch=on_epoch, names=list(self.panel.columns),
                                             horizon=int(self.data.reach["ahead"]), engine=engine, **extras)
        except ND.DeepFailure as error:
            raise RunFailure(self.deep_failure(error.failure, window)) from error
        except (MemoryError, RuntimeError) as error:
            if ND.oom_kind(error) is None:
                raise
            raise RunFailure(self.out_of_memory(window, kind=ND.oom_kind(error), batch=int(params.get("batch", 800)))) from error
        fitted = ND.QlibFitted(algorithm, model, self.data.features, curve, best, params=params, d_feat=5 if alpha else len(self.data.features), lookback=lookback, seed=seed,
                               engine=engine, objective=label.get("objective", "l2"), names=list(self.panel.columns), horizon=int(self.data.reach["ahead"]))
        fitted.source = source
        fitted.valid_rows = {key: parts["valid"][key] for key in ("t", "i", "y")}
        fitted.context = (train, valid)
        fitted.seconds = time.perf_counter() - started
        return fitted

    def aux_labels(self):
        """TCTS's auxiliary labels: the main label's own return one and two bars further out, processed as the main label is; None when the main label is not
        a plain return over some days (then the Label settings have to give them)."""
        if getattr(self, "_aux", None) is not None:
            return self._aux
        formulas = self.data.aux
        if not formulas:
            return None
        out = []
        for item in formulas:
            core, form = D.split_form(item)
            raw = D.label_of(self.panel, core)
            out.append(D.process_label(np.where(self.data.rows, raw, np.nan), self.data.settings["processing"]["label"], form))
        self._aux = out
        return out

    def concepts(self, window):
        """`concepts(names, day)`: the concept-sector membership of the names (names by concepts, 1 for a member): the table chosen in the Concepts row, as
        Market would give it (`classification.concept`; Market declares none now); None when none is chosen."""
        found = self.job.get("concepts")
        if not found or not found.get("path"):
            return None
        table = pd.read_parquet(found["path"])
        matrix = table.reindex(list(self.panel.columns)).fillna(0.0).to_numpy(dtype=np.float32)
        return {"matrix": matrix, "concepts": [str(name) for name in table.columns], "asOf": None}

    def deep_failure(self, failure, window):
        """A network's failure as the run shows it: an out of memory names the window and epoch; anything else is that window's reason."""
        if failure.get("kind") in ("memory", "gpu-memory"):
            return self.out_of_memory(window, kind=failure["kind"], epoch=failure.get("epoch"), saved=failure.get("saved"), batch=failure.get("batch"))
        return {**failure, "window": window.k, "title": i18n.t("Window {window}: {title}", window=window.k, title=failure['title'])}

    def out_of_memory(self, window, *, kind="memory", epoch=None, saved=None, batch=None):
        """Ran out of memory (or a card's): which window and epoch, from which epoch it can go on, the batch size it could try instead (half)."""
        gpu = kind == "gpu-memory"
        need = float(self.job.get("memoryEstimate") or 0) / GB
        free = available_memory() / GB
        detail = (i18n.t("Window {window} needed about {need} GB; {free} GB was free.", window=window.k, need=f"{need:.1f}", free=f"{free:.1f}") if need else i18n.t("{free} GB was free when window {window} ran out.", window=window.k, free=f"{free:.1f}")) if not gpu else \
            i18n.t("The card's memory was full.")
        if saved is not None:
            detail += i18n.t(" Window {window} saved epoch {saved}.", window=window.k, saved=saved) if saved else i18n.t(" Window {window} had not saved an epoch.", window=window.k)
        where = i18n.t("window {window}", window=window.k) if gpu else i18n.t("window {window} of {total}", window=window.k, total=len(self.data.windows))
        if epoch:
            where = i18n.t("{window}, epoch {epoch}", window=where, epoch=epoch)
        return {"stage": "compute", "window": window.k, "kind": kind, "title": (i18n.t("Ran out of GPU memory in {where}", where=where) if gpu else i18n.t("Ran out of memory in {where}", where=where)), "message": detail,
                "detail": detail, "need": round(need, 1), "free": round(free, 1), **({"epoch": epoch} if epoch else {}), **({"saved": saved} if saved is not None else {}),
                **({"batch": batch, "tryBatch": max(1, int(batch) // 2)} if batch else {})}

    def record(self, window, model, *, predictions=None):
        """Keep one window: its model (in the window cache), its numbers, its importance and learning curve, and its test predictions."""
        parts = window.parts
        test, train, valid = parts["test"], parts["train"], parts["valid"]
        if predictions is None:
            predict = model.predict if hasattr(model, "predict") else None
            if predict is None:
                raise RunFailure({"stage": "code", "title": i18n.t("cannot predict"), "message": i18n.t("q.record needs a model with predict(x), or predictions=.")})
            if getattr(model, "neural", False):
                def predict_part(part):
                    return model.predict_rows(part["t"], part["i"])
            else:
                def predict_part(part):
                    return predict(part["x"])
            values = np.asarray(predict_part(test), dtype=float).reshape(-1)
            in_train = np.asarray(predict_part(train), dtype=float).reshape(-1)
            in_valid = None if getattr(model, "algorithm", None) in A.TRAINS_ON_VALID else np.asarray(predict_part(valid), dtype=float).reshape(-1)
        else:
            values = np.asarray(predictions, dtype=float).reshape(-1)
            in_train = in_valid = None
        label = self.data.label_raw

        def segment_ic(t, i, value, span):
            if value is None or not len(t):
                return None
            first, last = span
            grid = np.full((last - first + 1, label.shape[1]), np.nan)
            grid[t - first, i] = value
            return M.mean(M.rank_correlation(grid, label[first:last + 1], 3))
        self.pred[test["t"], test["i"]] = values
        from sklearn.linear_model import Ridge
        ridge = Ridge(alpha=1.0).fit(np.nan_to_num(train["x"]), np.nan_to_num(train["y"])).predict(np.nan_to_num(test["x"])) if len(train["t"]) else np.zeros(len(test["t"]))
        self.ridge[test["t"], test["i"]] = ridge
        fitted = model if isinstance(model, A.Fitted) or getattr(model, "neural", False) else None
        importance = fitted.importance(train["x"]) if fitted is not None else _own_importance(model, self.data.features)
        spans = window._window.spans(self.panel.labels)
        trained_on_valid = fitted is not None and fitted.algorithm in A.TRAINS_ON_VALID
        record = {"k": window.k, "digest": self.digest(window._window), "train": spans["train"], "valid": spans["valid"], "test": spans["test"],
                  "trainSpan": list(window._window.train), "validSpan": list(window._window.valid), "testSpan": list(window._window.test),
                  "trainIc": segment_ic(train["t"], train["i"], in_train, window._window.train),
                  "validIc": segment_ic(valid["t"], valid["i"], in_valid, window._window.valid),
                  "testIc": segment_ic(test["t"], test["i"], values, window._window.test),
                  "best": fitted.best if fitted is not None else None, "rows": int(len(train["t"]) + (len(valid["t"]) if trained_on_valid else 0)),
                  "validRows": 0 if trained_on_valid else int(len(valid["t"])),
                  "secs": round(getattr(model, "seconds", time.perf_counter() - self.started), 2), "algorithm": fitted.algorithm if fitted is not None else "own",
                  "importance": importance, "curve": fitted.curve if fitted is not None else None,
                  "fitted": None if parts["fitted"] is None else {key: value.tolist() for key, value in parts["fitted"].items()}}
        folder = self.cache / str(window.k)
        folder.mkdir(parents=True, exist_ok=True)
        np.savez(folder / "test.npz", t=test["t"], i=test["i"], pred=values, ridge=ridge)
        if fitted is not None:
            A.save(fitted, folder / "model.pkl")
            (folder / "epoch.pt").unlink(missing_ok=True)
        else:
            try:
                with open(folder / "model.pkl", "wb") as stream:
                    pickle.dump(model, stream, protocol=5)
            except (pickle.PicklingError, TypeError, AttributeError) as error:   # an own model that cannot be pickled is retrained on resume, as the log says
                (folder / "model.pkl").unlink(missing_ok=True)
                progress(self.output, "window", state="not kept", window=window.k, reason=f"{type(error).__name__}: {error}")
        write_json(folder / "window.json", record)
        self.recorded[window.k] = record
        progress(self.output, "window", state="done", window=window.k, windows=len(self.data.windows), testIc=record["testIc"], trainIc=record["trainIc"],
                 validIc=record["validIc"], best=record["best"], rows=record["rows"], secs=record["secs"])
        window._parts = None

    def predict(self, scores):
        """Whole-period predictions from code that trains its own way (no windows): a table of dates by names, or a Series by (date, name)."""
        from native.factor_resident import _wide
        if isinstance(scores, pd.Series):
            scores = scores.unstack()
        self.predictions = _wide(scores, self.panel, "The predictions")


def _own_importance(model, features):
    for attribute in ("feature_importances_", "coef_"):
        values = getattr(model, attribute, None)
        if values is not None:
            values = np.abs(np.asarray(values, dtype=float)).reshape(-1)
            if len(values) == len(features) and values.sum() > 0:
                return {name: float(value / values.sum()) for name, value in zip(features, values, strict=True)}
    return {}


def current():
    if CURRENT is None:
        raise RuntimeError("q.model_data, q.fit and q.record work inside a model run.")
    return CURRENT


# ---------------------------------------------------------------------------------------------------------------- running the code
def run_code(training):
    import talyx_quant as q
    from native.object_worker import failure
    job = training.job
    code = job.get("code")
    if code is not None and code.get("own"):
        source, entry = Path(code["source"]).resolve(), code["entry"]
        roots = {"source": source}
        if code.get("common"):
            roots["library"] = Path(code["common"]).resolve()
    else:
        pipeline = Path(job["pipelinePath"])
        source, entry = pipeline.parent, pipeline.name
        roots = {}
    for root in roots.values():
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    q._bind_parameters(job.get("params") or {})
    # q.qlib and q.report work in the run's own folder, as in a notebook
    from native.publish import Publisher
    own = {"runId": job.get("runId") or "model", "mode": "model", "output": str(Path(job["output"]) / "code"),
           "research": {"info": training.panel.info, "barsPath": job["data"]["barsPath"]}}
    q._bind_research(own, Publisher(own))
    previous = os.getcwd()
    os.chdir(source)
    sys.argv = [entry]
    try:
        runpy.run_path(str(source / entry), run_name="__main__")
    except RunFailure:
        raise
    except Exception as error:  # the code's own error: where it broke is the result
        if not roots:
            if isinstance(error, (ValueError, TypeError)) and "Setting" in str(error) or "Parameter" in str(error):
                raise RunFailure({"stage": "settings", "title": i18n.t("a setting is not valid"), "message": str(error)}) from error
            raise
        found = failure(error, roots)
        inner = next((item for item in reversed(found["frames"]) if item["root"] in {"source", "library"}), None)
        # Windows train in order, so the one it broke in is the first not recorded yet (none before the data are made).
        window = next((item.k for item in training.data.windows if item.k not in training.recorded), None) if training.data is not None else None
        raise RunFailure({"stage": "code", "title": f"stopped at line {inner['line']}" if inner else "stopped in the code", "message": f"{found['type']}: {found['message']}",
                          "file": inner["file"] if inner else None, "line": inner["line"] if inner else None, "traceback": found["traceback"], "window": window}) from error
    finally:
        os.chdir(previous)


def pipeline_of():
    """The standard pipeline a run used: its version and a digest of its text (it is TALYX's own file; the run keeps a copy of it)."""
    text = (Path(__file__).parent / "model_standard.py").read_text(encoding="utf-8")
    version = next((line.split("=", 1)[1].strip().strip('"') for line in text.splitlines() if line.startswith("__version__")), None)
    return {"version": version, "digest": hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]}


def scored_span(labels, predictions):
    """Only days with a held-out prediction count toward the measured result."""
    dates = np.asarray(labels)[np.isfinite(predictions).any(axis=1)]
    if not len(dates):
        return None
    return {"start": pd.Timestamp(int(dates[0]), unit="ms", tz="UTC").date().isoformat(),
            "end": pd.Timestamp(int(dates[-1]), unit="ms", tz="UTC").date().isoformat(), "bars": len(dates)}


def summarize(training, *, future=None):
    """The run's numbers, checks and Results from its predictions; written to the output folder."""
    from native.model_report import Report
    data = training.data
    windows = [training.recorded[k] for k in sorted(training.recorded)]
    per_window = bool(windows) and training.predictions is None
    pred = training.pred if per_window else training.predictions
    held = training.job.get("heldOutFrom") if training.job.get("kind") == "heldout" else None
    if held is not None and pred is not None:
        # A held-out validation is scored once, on the held-out period only: its windows are those that test there.
        start = int(np.searchsorted(training.panel.labels, int(held)))
        pred = np.array(pred, dtype=float)
        pred[:start] = np.nan
        windows = [window for window in windows if window["testSpan"][0] >= start]
    if pred is None or not np.isfinite(pred).any():
        raise RunFailure({"stage": "code", "title": i18n.t("trained nothing"), "message": i18n.t("The code finished without a prediction: call q.record(window, model) for each window, or q.predict(scores).")})
    report = Report(data, pred, windows if per_window else [], run_settings={**(training.job.get("run") or {}), "number": training.job.get("number")}, per_window=per_window,
                    ridge=training.ridge if per_window else None, importance=[w["importance"] for w in windows if w.get("importance")] if per_window else [],
                    curves=[w.get("curve") for w in windows] if per_window else [])
    numbers = report.numbers()
    checks = report.checks(numbers, future)
    results, figures = report.results(numbers)
    out = training.output
    np.save(out / "pred.npy", report.pred)
    np.save(out / "label.npy", data.label_raw)  # the label as it was realised, for scoring a published model against it
    window_rows = [{key: w.get(key) for key in ("k", "train", "valid", "test", "trainIc", "validIc", "testIc", "best", "rows", "secs")} for w in windows] if per_window else []
    work = {"numbers": numbers, "checks": checks, "baselines": report.base, "windows": window_rows, "perWindow": per_window,
            "features": data.features, "inputs": data.describe()["inputs"], "dataFrom": int(training.panel.labels[0]), "dataTo": int(training.panel.labels[-1]),
            "tested": scored_span(training.panel.labels, pred), "notes": training.panel.notes, "peakMemory": peak_memory(), "seconds": round(time.perf_counter() - training.started, 2), "figureCount": len(figures),
            "horizon": data.horizon, "reach": data.reach, "label": data.label_formula, "annualization": float(training.panel.annualization),
            "settings": {"objective": training.job["settings"]["label"].get("objective", "l2"), "weight": training.job["settings"]["label"].get("weight", "equal")},
            "pipeline": None if (training.job.get("code") or {}).get("own") else pipeline_of(),
            "ic": {"x": [stamp.strftime("%Y-%m-%d") for stamp in training.panel.dates], "y": [M.finite(v) for v in report.ic]}}
    write_json(out / "work.json", work)
    write_json(out / "results.json", results)
    write_json(out / "figures.json", figures)
    meta = {"labels": training.panel.labels.tolist(), "columns": training.panel.columns, "features": data.features}
    write_json(out / "pred.json", meta)
    return work


# ---------------------------------------------------------------------------------------------------------------- modes
def train(job):
    global CURRENT
    training = Training(job)
    CURRENT = training
    output = training.output
    progress(output, "compute", state="running")
    run_code(training)
    if training.data is None:
        raise RunFailure({"stage": "code", "title": i18n.t("no data"), "message": i18n.t("The code never asked for its data: call q.model_data().")})
    progress(output, "numbers", state="running")
    work = summarize(training)
    progress(output, "numbers", state="done")
    return {"ok": True, "seconds": work["seconds"], "windows": len(training.recorded), "peakMemory": work["peakMemory"]}


def cutoff(job):
    """A publication's future-data check: the window covering the cut-off retrained on the data cut there; its predictions up to the
    cut-off must equal the run's own."""
    global CURRENT
    training = Training(job)
    CURRENT = training
    run_code(training)
    window = training.recorded.get(job["windows"][0]) if job.get("windows") else None
    if window is None:
        raise RunFailure({"stage": "compute", "title": i18n.t("no cut-off window"), "message": i18n.t("The cut-off window was not trained.")})
    original = np.load(Path(job["sourceCache"]) / str(window["k"]) / "test.npz")
    mine = np.load(training.cache / str(window["k"]) / "test.npz")
    theirs = {(int(t), int(i)): float(value) for t, i, value in zip(original["t"], original["i"], original["pred"], strict=True)}
    cut_labels = training.panel.labels
    worst = None
    for t, i, value in zip(mine["t"], mine["i"], mine["pred"], strict=True):
        before = theirs.get((int(t), int(i)))
        if before is None or abs(before - float(value)) > 1e-9 * max(1.0, abs(before)):
            worst = (int(t), int(i), before, float(value))
            break
    if worst is None:
        return {"ok": True, "passed": True, "window": window["k"], "compared": int(len(mine["t"])),
                "title": i18n.t("Window {window} retrained on the data cut at {cutoff}: its {count} predictions are the same", window=window['k'], cutoff=_day(cut_labels[-1]), count=len(mine['t']))}
    t, i, before, after = worst
    return {"ok": True, "passed": False, "window": window["k"], "label": int(cut_labels[t]), "name": training.panel.columns[i],
            "title": i18n.t("{symbol} on {date} is {before} with all the data but {after} when the data is cut at {cutoff}: the model reads data from after the time it predicts", symbol=training.panel.columns[i], date=_day(cut_labels[t]), before=f'{before:.6g}', after=f'{after:.6g}', cutoff=_day(cut_labels[-1]))}


def update(job):
    """A publication's newest values; a combined run's (`sources`) are each source's own, ranked within each day and averaged (as `combine` does)."""
    sources = job.get("sources")
    if not sources:
        return _update_one(job)
    parts, answers = [], []
    for index, source in enumerate(sources):
        output = Path(job["output"]) / f"source-{index}"
        output.mkdir(parents=True, exist_ok=True)
        answer = _update_one({**job, **{key: value for key, value in source.items() if key != "output"}, "sources": None, "output": str(output)})
        answers.append(answer)
        frame = pd.read_parquet(output / "values.parquet")
        frame["rank"] = frame.groupby("label")["value"].rank(pct=True)
        parts.append(frame[["label", "column", "rank"]])
    stacked = pd.concat(parts).groupby(["label", "column"], as_index=False)["rank"].mean().rename(columns={"rank": "value"})
    stacked.to_parquet(Path(job["output"]) / "values.parquet", index=False)
    if (Path(job["output"]) / "source-0" / "labels.parquet").is_file():
        shutil.copy(Path(job["output"]) / "source-0" / "labels.parquet", Path(job["output"]) / "labels.parquet")
    return {"ok": True, "rows": len(stacked), "trainedWindows": sorted({k for a in answers for k in a["trainedWindows"]}), "newestWindow": max(a["newestWindow"] for a in answers),
            "dataTo": answers[0]["dataTo"]}


def _update_one(job):
    """One model's newest values: windows already trained come from its cache, a window that starts on a retraining day is trained,
    and every day after the cursor is predicted by the window whose test period holds it (the newest one up to the last bar)."""
    global CURRENT
    training = Training(job)
    CURRENT = training
    run_code(training)
    newest = max(training.recorded) if training.recorded else None
    if newest is None:
        raise RunFailure({"stage": "compute", "title": i18n.t("no window"), "message": i18n.t("No window of this publication could be trained.")})
    # Each window predicts its own test period (the newest one up to the last bar), so a publication that resumes after months, or whose
    # held-out period lies between its data and today, has a value for every day after its cursor, not only for the newest window's days.
    after = int(job.get("after") or 0)
    labels = training.panel.labels
    rows = []
    for window in training.data.windows:
        if window.k not in training.recorded:
            continue
        last = len(labels) - 1 if window.k == newest else min(window.test[1], len(labels) - 1)
        if last < window.test[0] or int(labels[last]) <= after:
            continue
        fitted = A.load(training.cache / str(window.k) / "model.pkl")
        if getattr(fitted, "neural", False):
            fitted.source = training.neural_source(training.recorded[window.k], fitted.lookback)
        t, i = training.data.segment((window.test[0], last), labelled=False)
        if len(t) == 0:
            continue
        fitted_processing = training.recorded[window.k].get("fitted")
        x = D.apply_fitted(training.data.x[t, i], None if fitted_processing is None else {key: np.asarray(value) for key, value in fitted_processing.items()},
                           training.data.settings["processing"]["inputs"])
        values = fitted.predict_rows(t, i) if getattr(fitted, "neural", False) else fitted.predict(x)
        rows += [{"label": int(labels[a]), "column": training.panel.columns[b], "value": float(v)} for a, b, v in zip(t, i, values, strict=True) if int(labels[a]) > after]
    pd.DataFrame(rows, columns=["label", "column", "value"]).to_parquet(training.output / "values.parquet", index=False)
    if job.get("labelFrom") is not None:
        # The label as it was trained on (its horizon and price field), measured on the bars this update has: a period whose horizon has not passed has none yet.
        realised = np.asarray(training.data.label_raw, dtype=float)
        first = int(np.searchsorted(labels, int(job["labelFrom"])))
        t, i = np.nonzero(np.isfinite(realised[first:]))
        t = t + first
        pd.DataFrame({"label": np.asarray(labels)[t].astype("int64"), "column": np.asarray(training.panel.columns, dtype=object)[i], "value": realised[t, i]}).to_parquet(
            training.output / "labels.parquet", index=False)
    trained = sorted(set(training.recorded) - set(job.get("cachedWindows") or []))
    return {"ok": True, "rows": len(rows), "trainedWindows": trained, "newestWindow": newest, "dataTo": int(training.panel.labels[-1])}


def default_day(training, job):
    """The day Values opens on: the last day of the last test period whose label every name has realised, outside the held-out period."""
    labels = training.panel.labels
    held = job.get("heldOutFrom")
    stop = len(labels) if held is None else int(np.searchsorted(labels, int(held)))
    for window in reversed(training.data.windows):
        for row in range(min(window.test[1], stop - 1), window.test[0] - 1, -1):
            live = training.data.rows[row]
            if live.any() and np.isfinite(training.data.label_raw[row][live]).all():
                return int(labels[row])
    return int(labels[max(training.data.windows[-1].test[0], 0)]) if training.data.windows else int(labels[-1])


def values_figure(rows):
    """Prediction rank against the realised label's rank for one day, a point a name."""
    from native import model_figures as F
    have = [row for row in rows if row["prediction"] is not None and row["later"] is not None]
    if len(have) < 3:
        return None
    pred = np.argsort(np.argsort([row["prediction"] for row in have])) / (len(have) - 1)
    later = np.argsort(np.argsort([row["later"] for row in have])) / (len(have) - 1)
    return F.figure("model.values.scatter", [{"type": "scatter", "mode": "markers", "x": pred.tolist(), "y": later.tolist(), "name": "Names",
                                              "text": [row["ticker"] for row in have], "customdata": [[row["prediction"], row["later"]] for row in have],
                                              "hovertemplate": "%{text}<br>Prediction %{customdata[0]:+.2f}<br>Realised %{customdata[1]:+.1%}<extra></extra>",
                                              "meta": {"role": "main", "key": "values"}}],
                    xaxis={"title": i18n.t("Prediction rank")}, yaxis={"title": i18n.t("Realised rank")})


_WARM = {}  # the one run whose values this process keeps ready: {"key", "training", "models"}


def warm_key(job):
    """What says the inputs and models kept here are this run's: its data, settings, inputs and code, and its predictions' file as it is now."""
    import hashlib
    predictions = Path(job["runOutput"]) / "pred.npy"
    return hashlib.sha256(json.dumps({"data": job["data"].get("key"), "settings": job.get("settings"), "inputs": job.get("inputs"), "params": job.get("params"),
                                      "code": (job.get("code") or {}).get("digest"), "cutoff": job.get("cutoff"), "heldOutFrom": job.get("heldOutFrom"), "run": str(job["runOutput"]),
                                      "cache": str(job.get("cache")), "predicted": predictions.stat().st_mtime_ns if predictions.is_file() else None},
                                     sort_keys=True, default=str).encode("utf-8")).hexdigest()


def values_training(job):
    """The run's data as a day's values reads it. A warm process (`job["warm"]`) builds it once and keeps it, with the models loaded, for the next day; else each
    look builds it (or reads it back from the run's kept inputs file)."""
    global CURRENT
    key = warm_key(job) if job.get("warm") else None
    if key is not None and _WARM.get("key") == key:
        CURRENT = _WARM["training"]
        return _WARM["training"], _WARM["models"]
    _WARM.clear()  # one run at a time: the memory of the one before goes first
    training = Training(job)
    CURRENT = training
    training.job["windows"] = []
    run_code(training)
    models = {}
    if key is not None:
        _WARM.update(key=key, training=training, models=models)
    return training, models


def _label(value):
    """An industry cell as text, or None where the name had none that day."""
    return None if value is None or (isinstance(value, float) and value != value) else str(value)


def values(job):
    """One day's predictions with each input's contribution (the window whose test period holds the day), and the later return."""
    marks = {"start": time.perf_counter()}
    training, models = values_training(job)
    marks["panel"] = marks["inputs"] = time.perf_counter()
    labels = training.panel.labels
    if job.get("date") is None:
        label = default_day(training, job)
    else:
        label = int(job["date"])
    row = int(np.clip(np.searchsorted(labels, label, side="right") - 1, 0, len(labels) - 1))
    window = next((w for w in training.data.windows if w.test[0] <= row <= w.test[1]), None)
    pred = np.load(Path(job["runOutput"]) / "pred.npy", mmap_mode="r")
    if window is None or not np.isfinite(pred[row]).any():
        tested = [w for w in training.data.windows]
        return {"ok": True, "date": int(labels[row]), "rows": [], "reason": "No window predicts this day" + (
            f"; the test periods run {_day(labels[tested[0].test[0]])} to {_day(labels[tested[-1].test[1]])}" if tested else "")}
    t, i = training.data.segment((row, row), labelled=False)
    cached = training.recorded.get(window.k) or {}
    fitted_processing = cached.get("fitted")
    x = D.apply_fitted(training.data.x[t, i], None if fitted_processing is None else {key: np.asarray(value) for key, value in fitted_processing.items()},
                       training.data.settings["processing"]["inputs"])
    marks["rows"] = time.perf_counter()
    path = A.model_file(training.cache / str(window.k))
    contributions = None
    note = None
    if path.is_file():
        try:
            if window.k not in models:
                models[window.k] = A.load(path.with_name("model.pkl"))
            fitted = models[window.k]
            marks["model"] = time.perf_counter()
            if getattr(fitted, "neural", False):
                fitted.source = training.neural_source(cached, fitted.lookback)
                contributions = fitted.contributions_rows(t, i)
            else:
                contributions = fitted.contributions(x)
        except Exception as error:  # noqa: BLE001
            note = f"Contributions are not available: {type(error).__name__}"
    else:
        note = "This window's model was not kept, so contributions are not available"
    marks["contributions"] = time.perf_counter()
    progress(training.output, "values", seconds={key: round(marks[key] - marks[before], 2) for before, key in zip(list(marks)[:-1], list(marks)[1:])})
    features = training.data.features
    later = training.data.label_raw[row]
    ranks = M.rank_rows(np.asarray(pred[row:row + 1], dtype=float))[0]
    out = []
    for position, (a, b) in enumerate(zip(t, i, strict=True)):
        entry = {"ticker": training.panel.columns[b], "prediction": M.finite(pred[a, b]), "rank": M.finite(ranks[b]),
                 "later": M.finite(later[b]), "sector": _label(training.panel.industry.iat[row, b]) if training.panel.industry is not None else None}
        if contributions is not None:
            parts = contributions[position]
            order = np.argsort(-np.abs(parts[:-1]))[:8]
            entry["contributions"] = [{"input": features[k], "value": float(parts[k])} for k in order]
            entry["base"] = float(parts[-1])
        out.append(entry)
    for entry in out:
        entry["realised"] = entry["later"]
    spearman = M.finite(M.rank_correlation(pred[row:row + 1], later[None, :])[0])
    return {"ok": True, "date": int(labels[row]), "day": int(labels[row]), "window": window.k, "rows": out, "features": len(features), "note": note,
            "figure": values_figure(out), "stats": {"rankIc": spearman},
            "previous": int(labels[row - 1]) if row > 0 else None, "next": int(labels[row + 1]) if row + 1 < len(labels) else None,
            "basis": {"lightgbm": "LightGBM pred_contrib", **{key: "Integrated gradients" for key in A.NEURAL_IDS}, "xgboost": "XGBoost contributions", "catboost": "CatBoost SHAP values"}.get(cached.get("algorithm"), "coefficient × value")}


def mean_rank_scores(ranks):
    """Average the available percentile ranks per name, then apply the CSRankNorm scale once.
    Twice the midrank is integral. Sum those integers on a common denominator so equal means stay tied."""
    present = np.isfinite(ranks)
    counts = present.sum(axis=2)
    result = np.full(ranks.shape[1:], np.nan)
    for day in np.flatnonzero(counts.any(axis=0)):
        sizes = counts[:, day]
        denominator = math.lcm(*(int(n) for n in sizes if n))
        # Python integers keep the common denominator exact even for sources with different coverage.
        weights = np.array([denominator // int(n) if n else 0 for n in sizes], dtype=object)
        doubled = np.nan_to_num(2 * ranks[:, day]).astype(np.int64)
        sources = present[:, day].sum(axis=0)
        valid = sources > 0
        result[day, valid] = (weights @ doubled[:, valid]) / (sources[valid].astype(object) * (2 * denominator))
    return (result - 0.5) * 3.46


def combine(job):
    """The mean of several runs' predictions (each ranked within its day first): a run of its own, scored like any other."""
    global CURRENT
    training = Training(job)
    CURRENT = training
    training.job["windows"] = []
    run_code(training)
    ranks = []
    for path in map(Path, job["sources"]):
        axes = json.loads((path / "pred.json").read_text(encoding="utf-8"))
        scores = pd.DataFrame(np.load(path / "pred.npy"), index=axes["labels"], columns=axes["columns"])
        scores = scores.loc[training.panel.labels, training.panel.columns]
        ranks.append(M.rank_rows(scores.to_numpy()))
    mean = mean_rank_scores(np.stack(ranks))
    mean[~training.data.rows] = np.nan
    training.recorded = {}
    for window in job["sourceWindows"]:
        first, last = window["testSpan"]
        test_ic = M.mean(M.rank_correlation(mean[first:last + 1], training.data.label_raw[first:last + 1]))
        training.recorded[window["k"]] = {**window, "testIc": test_ic, "importance": {}, "curve": None, "best": None}
    training.pred = mean
    training.predictions = None if training.recorded else mean
    training.ridge = None
    work = summarize(training)
    return {"ok": True, "seconds": work["seconds"]}


def universe_benchmark(closes):
    """The benchmark of the portfolio backtest: every name's close-to-close return averaged each day, marked at the close as Qlib marks the portfolio."""
    return (closes / closes.shift(1) - 1).mean(axis=1).iloc[1:]


def portfolio_numbers(frame, annual):
    """Qlib's report as Qlib's own PortAnaRecord reads it: the excess without cost is return less benchmark, with cost it is less the cost too; annualized by the
    data's own observations a year (Qlib's default is the 238 of the A-share calendar). Returns (risk, lines): the risk tables and the lines of the figure."""
    from qlib.contrib.evaluate import risk_analysis
    without = frame["return"] - frame["bench"]
    with_cost = without - frame["cost"]
    risk = {name: {key: M.finite(found.loc[key].iloc[0]) for key in found.index}
            for name, found in (("Excess return, without cost", risk_analysis(without, N=annual)), ("Excess return, with cost", risk_analysis(with_cost, N=annual)))}
    lines = {"excess": (1 + with_cost).cumprod() - 1, "portfolio": (1 + frame["return"] - frame["cost"]).cumprod() - 1, "bench": (1 + frame["bench"]).cumprod() - 1}
    return risk, lines


def portfolio(job):
    """Qlib's own portfolio backtest of a finished run's predictions: top N by the prediction, N dropped a day, after cost, at the next open. Qlib's exchange reads
    prices only from a Qlib data directory, so the run's bars are written as one here, and the American-market defaults (A-share lots, price limits, minimum fees,
    the CSI 300 benchmark) are replaced by what these data are: no lot, no limit, no minimum, the names' own equal-weight return as the benchmark."""
    import qlib
    from native import model_figures as F
    from native.provider import write_provider
    from qlib.contrib.evaluate import backtest_daily
    from qlib.contrib.strategy import TopkDropoutStrategy
    output = Path(job["output"])
    panel = load_panel(job)
    pred = np.load(Path(job["runOutput"]) / "pred.npy")
    top = int(job.get("run", {}).get("topN", 50))
    cost = float(job.get("run", {}).get("cost", 5)) / 1e4
    index = pd.DatetimeIndex(panel.dates).tz_localize(None)
    safe = {name: re.sub(r'[<>:"/\\|?*]', "_", name) for name in panel.columns}
    frames = {}
    for position, name in enumerate(panel.columns):
        table = pd.DataFrame({field: panel.fields[field][:, position] for field in ("open", "high", "low", "close", "volume")}, index=index)
        frames[safe[name]] = table.dropna(subset=["close"]).assign(factor=1.0)
    provider = output / "provider"
    if provider.exists():
        shutil.rmtree(provider)
    write_provider(frames, provider, {"frequency": "day"})
    qlib.init(provider_uri=str(provider), region="us", kernels=1)
    t, i = np.nonzero(np.isfinite(pred))
    names = np.asarray([safe[name] for name in panel.columns], dtype=object)
    score = pd.Series(pred[t, i], index=pd.MultiIndex.from_arrays([index[t], names[i]], names=["datetime", "instrument"])).sort_index()
    benchmark = universe_benchmark(pd.DataFrame(panel.fields["close"], index=index, columns=panel.columns))
    first, last = score.index.get_level_values(0).min(), score.index.get_level_values(0).max()
    last = min(last, index[-2])  # Qlib's trade step reads the next calendar day for its end: the last day cannot be a step
    report, _positions = backtest_daily(start_time=first, end_time=last, strategy=TopkDropoutStrategy(signal=score, topk=top, n_drop=min(5, top)), account=1e8, benchmark=benchmark,
                                         exchange_kwargs={"freq": "day", "deal_price": "open", "limit_threshold": None, "trade_unit": None, "open_cost": cost, "close_cost": cost, "min_cost": 0})
    frame = next(iter(report.values()))[0] if isinstance(report, dict) else report
    risk, lines = portfolio_numbers(frame, int(panel.annualization))
    figure = F.figure("model.returns.portfolio", [F.line(list(frame.index.strftime("%Y-%m-%d")), lines["excess"], "Excess, after cost", "main", "excess"),
                                                   F.line(list(frame.index.strftime("%Y-%m-%d")), lines["portfolio"], "Portfolio, after cost", "group", "portfolio"),
                                                   F.line(list(frame.index.strftime("%Y-%m-%d")), lines["bench"], "Benchmark", "benchmark", "bench"),
                                                   F.line(list(frame.index.strftime("%Y-%m-%d")), frame["turnover"], "Turnover", "detail", "turnover", yaxis="y2")],
                       yaxis={"tickformat": ".0%", "side": "right"}, xaxis={"type": "date"}, yaxis2={"overlaying": "y", "side": "left", "tickformat": ".0%"})
    from native.model_report import _hover
    _hover({"model.returns.portfolio": figure})
    write_json(output / "portfolio.json", {"risk": risk, "days": int(len(frame)), "top": top, "drop": min(5, top), "figure": figure})
    return {"ok": True, "risk": risk}


MODES = {"train": train, "cutoff": cutoff, "update": update, "values": values, "combine": combine, "portfolio": portfolio}


def main():
    invocation = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if invocation.get("support"):
        sys.path.insert(0, invocation["support"])
    os.environ.setdefault("MPLBACKEND", "Agg")
    job = invocation["job"]
    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    try:
        answer = MODES[job["mode"]](job)
    except RunFailure as error:
        progress(output, "failed", failure=error.failure)
        answer = {"ok": False, "failure": error.failure}
    except MemoryError:
        failure = {"stage": "compute", "title": i18n.t("Ran out of memory"), "message": i18n.t("The run ran out of memory; {free} GB was free.", free=f'{available_memory() / GB:.1f}'),
                   "free": round(available_memory() / GB, 1), "need": round(float(job.get("memoryEstimate") or 0) / GB, 1)}
        progress(output, "failed", failure=failure)
        answer = {"ok": False, "failure": failure}
    except Exception as error:  # noqa: BLE001 - the run ends and says what broke; the traceback goes to the log
        traceback.print_exc()
        failure = {"stage": "compute", "title": i18n.t("The model engine failed"),
                   "message": i18n.t("The model engine hit an internal error ({error_type}: {reason}) and stopped; the details are in Quant's log.", error_type=type(error).__name__, reason=str(error)[:200])}
        progress(output, "failed", failure=failure)
        answer = {"ok": False, "failure": failure}
    write_json(output / "answer.json", answer)
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    # The run's state must live in the module talyx_quant imports (native.model_run), not in this script's copy.
    from native import model_run
    raise SystemExit(model_run.main())


def ND_device():
    """The device a network trains on when none is chosen: the first NVIDIA card the installed PyTorch can use, else the CPU."""
    import torch
    return "cuda:0" if torch.cuda.is_available() else "cpu"
