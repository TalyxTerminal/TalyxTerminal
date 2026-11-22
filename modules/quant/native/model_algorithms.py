"""The algorithms a model trains with: Qlib's own model classes, fed one window's rows through a minimal dataset.

The table of algorithms is what the research environment can run, in four groups (trees, linear, ensembles, neural networks); each
algorithm declares its own parameters under its own names (XGBoost's "Column sample", CatBoost's "L2 leaf"). Neural networks are
listed for the deep learning environment and are not trainable here. Every fitted model gives its predictions, its importance by
input (gain for trees, |coefficient| × the input's spread for linear models), each prediction's contributions by input, and its
learning curve (training and validation loss by round).

Reproducible training: LightGBM with `deterministic` and column-wise histograms gives the same model bit for bit on any number of
threads; XGBoost's and CatBoost's CPU training is deterministic for a fixed seed.
"""
from __future__ import annotations

import ast
import pickle

import numpy as np
import pandas as pd

from . import i18n

# ---------------------------------------------------------------------------------------------------------------- the table
TREE = "tree"
LINEAR = "linear"
ENSEMBLE = "ensemble"
NEURAL = "neural"
EPOCH_IDS = ("mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats")
# Trained by Qlib's own fit(dataset), one window at a time (no epoch file): see model_deep.fit_qlib.
QLIB_FIT_IDS = ("igmtf", "add", "adarnn", "tra", "tcts", "hist")
NEURAL_IDS = (*EPOCH_IDS, *QLIB_FIT_IDS)

# key, label, kind, default, help; the keys are what the experiment's params show.
_LGB = [("lr", "Learning rate", "float", 0.05, None), ("leaves", "Leaves", "int", 210, None), ("depth", "Max depth", "int", 8, None),
        ("ff", "Feature fraction", "float", 0.8, None), ("bf", "Row fraction", "float", 0.8, None), ("l1", "L1", "float", 0.0, None),
        ("l2", "L2", "float", 0.0, None), ("minleaf", "Min data in leaf", "int", 100, None), ("rounds", "Rounds, up to", "int", 1000, None),
        ("stop", "Early stop", "int", 50, "Stops when the validation loss has not improved for this many rounds; 0 turns it off")]
_XGB = [("lr", "Learning rate", "float", 0.05, None), ("depth", "Max depth", "int", 6, None), ("ff", "Column sample", "float", 0.8, None),
        ("bf", "Row sample", "float", 0.8, None), ("l1", "Alpha", "float", 0.0, "L1 regularization"), ("l2", "Lambda", "float", 1.0, "L2 regularization"),
        ("minleaf", "Min child weight", "float", 20.0, None), ("rounds", "Rounds, up to", "int", 1000, None),
        ("stop", "Early stop", "int", 50, "Stops when the validation loss has not improved for this many rounds; 0 turns it off")]
_CAT = [("lr", "Learning rate", "float", 0.05, None), ("depth", "Depth", "int", 6, None), ("l2", "L2 leaf", "float", 3.0, None),
        ("rounds", "Rounds, up to", "int", 1000, None), ("stop", "Early stop", "int", 50, "Stops when the validation loss has not improved for this many rounds; 0 turns it off")]
_ALPHA = [("alpha", "Alpha", "float", 1.0, "Regularization strength")]
_DENS = [("lr", "Learning rate", "float", 0.05, None), ("leaves", "Leaves", "int", 63, None), ("models", "Sub-models", "int", 6, None),
         ("rounds", "Rounds each", "int", 200, None)]
_STOP_E = "Stops when the validation loss has not improved for this many epochs"
_NEURAL = [("epochs", "Epochs, up to", "int", 100, None), ("stop", "Early stop", "int", 20, _STOP_E), ("batch", "Batch size", "int", 800, None),
           ("lr", "Learning rate", "float", 0.001, None), ("dropout", "Dropout", "float", 0.0, None), ("layers", "Layers", "int", 2, None),
           ("width", "Width", "int", 64, "Hidden units in each layer")]
_GAMMA = ("gamma", "Market-state weight", "float", 0.1, None)
_DOMAINS = ("domains", "Domains", "int", 2, None)
_STATES = ("states", "States", "int", 3, None)
_STEPS = ("steps", "Weight steps", "int", 3, None)
_LOOK = ("lookback", "Look back", "int", 20, "Days of history each sample sees; memory grows with it")
_OPT = ("optimizer", "Optimizer", "choice", "adam", None)
_DEVICE = ("device", "Device", "text", "", None)


def _neural_params(key, shape):
    base = [item for item in _NEURAL if not (key == "mlp" and item[0] == "dropout")]
    if shape == "sequence":
        base.insert(base.index(next(item for item in base if item[0] == "stop")) + 2, _LOOK)
    if key in ("transformer", "localformer"):
        base.append(("heads", "Heads", "int", 2, None))
    if key == "tabnet":
        base.append(("pretrain", "Pretrain epochs", "int", 50, None))
    if key == "gats":
        base.append(("base", "Start from", "text", "", "A finished LSTM or GRU run whose weights GATs starts from"))
    return [*base, _OPT, _DEVICE]


def _qlib_params(key):
    """The settings of a network Qlib's own fit trains: the epoch-by-epoch ones it does not take (batch for the day-wise ones) are left out."""
    drop = {"igmtf": {"batch"}, "hist": {"batch"}, "tra": {"dropout", "layers"}}.get(key, set())
    base = [item for item in _NEURAL if item[0] not in drop]
    base.insert(base.index(next(item for item in base if item[0] == "stop")) + 2, _LOOK)
    extra = {"add": [_GAMMA, ("metric", "Early stop on", "choice", "ic", None)], "adarnn": [_DOMAINS, ("metric", "Early stop on", "choice", "ic", None)], "tra": [_STATES], "tcts": [_STEPS]}.get(key, [])
    tail = [] if key == "tra" else [_OPT]
    start = [("base", "Start from", "text", "", "A finished LSTM or GRU run whose weights the network starts from")] if key in ("igmtf", "add", "hist") else []
    concepts = [("concepts", "Concepts", "text", "", "The concept-sector table HIST reads")] if key == "hist" else []
    return [*base, *extra, *tail, *start, *concepts, _DEVICE]


ALGORITHMS = [
    {"id": "lightgbm", "label": i18n.mark("LightGBM"), "group": TREE, "params": _LGB, "engine": "lightgbm.train"},
    {"id": "xgboost", "label": i18n.mark("XGBoost"), "group": TREE, "params": _XGB, "engine": "xgboost.train"},
    {"id": "catboost", "label": i18n.mark("CatBoost"), "group": TREE, "params": _CAT, "engine": "catboost.CatBoost"},
    {"id": "linear", "label": i18n.mark("Least squares"), "group": LINEAR, "params": [], "engine": "qlib.contrib.model.linear.LinearModel(ols)"},
    {"id": "ridge", "label": i18n.mark("Ridge"), "group": LINEAR, "params": _ALPHA, "engine": "qlib.contrib.model.linear.LinearModel(ridge)"},
    {"id": "lasso", "label": i18n.mark("Lasso"), "group": LINEAR, "params": [("alpha", "Alpha", "float", 0.001, "Regularization strength")], "engine": "qlib.contrib.model.linear.LinearModel(lasso)"},
    {"id": "nnls", "label": i18n.mark("Non-negative least squares"), "group": LINEAR, "params": [], "engine": "qlib.contrib.model.linear.LinearModel(nnls)"},
    {"id": "double_ensemble", "label": i18n.mark("DoubleEnsemble"), "group": ENSEMBLE, "params": _DENS, "engine": "qlib.contrib.model.double_ensemble.DEnsembleModel"},
    *({"id": key, "label": label, "group": NEURAL, "params": _neural_params(key, shape), "deep": True, "shape": shape, **({"note": note} if note else {}),
                                                                                                                                  **({"needs": ["base_run"]} if key == "gats" else {})}
      for key, label, shape, note in (
          ("mlp", "MLP", "cross", None), ("lstm", "LSTM", "sequence", None), ("gru", "GRU", "sequence", None), ("alstm", "ALSTM", "sequence", None),
          ("transformer", "Transformer", "sequence", None), ("localformer", "Localformer", "sequence", None), ("tcn", "TCN", "sequence", None),
          ("tabnet", "TabNet", "cross", "Pretrains first, then fits"), ("sfm", "SFM", "sequence", None), ("krnn", "KRNN", "sequence", None),
          ("sandwich", "Sandwich", "sequence", None), ("gats", "GATs", "sequence", None))),
    *({"id": key, "label": label, "group": NEURAL, "params": _qlib_params(key), "deep": True, "shape": "sequence", "route": "qlib_fit", **({"needs": needs} if needs else {})}
      for key, label, needs in (("igmtf", "IGMTF", ["base_run"]), ("add", "ADD", ["base_run"]), ("adarnn", "ADARNN", None), ("tra", "TRA", None), ("tcts", "TCTS", ["aux_labels"]),
                                ("hist", "HIST", ["base_run", "concepts"]))),
]
BY_ID = {item["id"]: item for item in ALGORITHMS}
# Which optimizer and early-stop measure each network offers, the first being its default (Qlib 0.9.8's constructors and metric functions, read from the source).
_ADAM = [{"id": "adam", "label": i18n.mark("Adam")}, {"id": "gd", "label": "SGD"}]
_SGD = [{"id": "gd", "label": "SGD"}, {"id": "adam", "label": i18n.mark("Adam")}]
NETWORK_OPTIONS = {
    **{key: {"optimizers": _ADAM, "earlyStopOn": []} for key in EPOCH_IDS if key != "mlp"},
    "mlp": {"optimizers": _SGD, "earlyStopOn": []},
    "igmtf": {"optimizers": _ADAM, "earlyStopOn": []}, "hist": {"optimizers": _ADAM, "earlyStopOn": []},
    "add": {"optimizers": _ADAM, "earlyStopOn": [{"id": "ic", "label": i18n.mark("Validation IC")}, {"id": "loss", "label": i18n.mark("Validation loss")}, {"id": "ric", "label": i18n.mark("Validation rank IC")}]},
    "adarnn": {"optimizers": _ADAM, "earlyStopOn": [{"id": "loss", "label": i18n.mark("Validation loss")}, {"id": "ic", "label": i18n.mark("Validation IC")}]},
    "tra": {"optimizers": [], "earlyStopOn": []},
    "tcts": {"optimizers": _ADAM, "earlyStopOn": []},
}


def declared():
    """The table as the preparation shows it: each algorithm with its group, its own parameters and why it cannot be chosen."""
    out = []
    for item in ALGORITHMS:
        out.append({"id": item["id"], "label": item["label"], "group": item["group"],
                    **({"shape": item["shape"]} if item.get("shape") else {}), **({"deep": True, "route": item.get("route", "epochs")} if item.get("deep") else {}),
                    **({"needs": item["needs"]} if item.get("needs") else {}),
                    **({"optimizers": NETWORK_OPTIONS[item["id"]]["optimizers"]} if item.get("deep") and NETWORK_OPTIONS[item["id"]]["optimizers"] else {}),
                    **({"earlyStopOn": NETWORK_OPTIONS[item["id"]]["earlyStopOn"]} if item.get("deep") and len(NETWORK_OPTIONS[item["id"]]["earlyStopOn"]) > 1 else {}),
                    **({"unavailable": item["unavailable"]} if item.get("unavailable") else {}), **({"note": item["note"]} if item.get("note") else {}),
                    "objectives": objectives_of(item["id"]),
                    "params": [{"key": key, "label": label, "kind": "number" if kind in ("int", "float") else kind, "type": kind, "default": default, **({"hint": hint} if hint else {}),
                                **({"unit": "rounds"} if key in ("rounds", "stop") and item["group"] != NEURAL else {"unit": "epochs"} if key in ("epochs", "stop") else {})}
                               for key, label, kind, default, hint in item["params"]]})
    return out


def defaults(algorithm):
    return {key: default for key, _label, _kind, default, _hint in BY_ID[algorithm]["params"]}


def checked_params(algorithm, params):
    """The algorithm's parameters with defaults filled in and each value of its declared type; unknown keys are an error."""
    if algorithm not in BY_ID:
        raise ValueError(f"Unknown algorithm {algorithm!r}")
    item = BY_ID[algorithm]
    kinds = {key: kind for key, _label, kind, _default, _hint in item["params"]}
    unknown = sorted(set(params) - set(kinds))
    if unknown:
        raise ValueError(f"{item['label']} has no setting {unknown[0]!r}; its settings are " + ", ".join(kinds))
    out = defaults(algorithm)
    for key, value in params.items():
        if kinds[key] in ("choice", "text"):
            out[key] = str(value)
            continue
        if type(value) is bool or not isinstance(value, (int, float)):
            raise ValueError(f"{item['label']} {key} needs a number")
        out[key] = int(value) if kinds[key] == "int" else float(value)
    return out


# ---------------------------------------------------------------------------------------------------------------- objectives and weights
# id, label, the number the objective takes (name, lowest, highest, default); what each library calls it is in `fit`.
OBJECTIVES = [("l2", "Regression · L2", None), ("l1", "Regression · L1", None), ("huber", "Regression · Huber", ("δ", 0.0, None, 1.0)),
              ("quantile", "Regression · Quantile", ("q", 0.0, 1.0, 0.5)), ("binary", "Classification · Binary", None),
              ("multiclass", "Classification · Groups", ("groups", 3, 10, 5)), ("lambdarank", "Ranking · LambdaRank", None),
              ("xendcg", "Ranking · XE-NDCG", None), ("pairwise", "Ranking · Pairwise", None)]
_FULL = ["l2", "l1", "huber", "quantile", "binary", "multiclass", "lambdarank"]
SUPPORTS = {"lightgbm": [*_FULL, "xendcg"], "xgboost": [*_FULL, "pairwise"], "catboost": [*_FULL, "pairwise"],
            "linear": ["l2"], "ridge": ["l2"], "lasso": ["l2"], "nnls": ["l2"], "double_ensemble": ["l2"],
            # a network trains the label as a number; only the MLP has a loss choice that takes a 0/1 label
            **{key: ["l2"] for key in NEURAL_IDS}, "mlp": ["l2", "binary"]}
RANKING = {"lambdarank", "xendcg", "pairwise"}


def objectives_of(algorithm):
    """The objectives the algorithm's library can train, as the preparation lists them (an objective with a number says which)."""
    found = SUPPORTS.get(algorithm, [])
    out = []
    for key, label, number in OBJECTIVES:
        if key in found:
            out.append({"id": key, "label": label, "weights": takes_weight(algorithm, key),
                        **({"param": {"name": number[0], "min": number[1], "max": number[2], "default": number[3]}} if number else {})})
    return out


def takes_weight(algorithm, objective):
    """Whether the library takes one weight for each row under this objective (measured on each library; see the build draft, D16)."""
    if algorithm in ("double_ensemble", "nnls") or algorithm not in SUPPORTS or algorithm in NEURAL_IDS:
        return False
    if algorithm in ("xgboost", "catboost"):
        return objective not in {"lambdarank", "pairwise"}
    return True


# ---------------------------------------------------------------------------------------------------------------- the library's own parameters
# What each control above is called in each library: a key written in Engine parameters that one of these names is refused ("set above").
CONTROL_NAMES = {
    "lightgbm": {"learning_rate": "lr", "eta": "lr", "num_leaves": "leaves", "max_depth": "depth", "feature_fraction": "ff", "colsample_bytree": "ff", "bagging_fraction": "bf",
                 "subsample": "bf", "lambda_l1": "l1", "reg_alpha": "l1", "lambda_l2": "l2", "reg_lambda": "l2", "min_data_in_leaf": "minleaf", "min_child_samples": "minleaf"},
    "xgboost": {"eta": "lr", "learning_rate": "lr", "max_depth": "depth", "colsample_bytree": "ff", "subsample": "bf", "alpha": "l1", "reg_alpha": "l1", "lambda": "l2",
                "reg_lambda": "l2", "min_child_weight": "minleaf"},
    "catboost": {"learning_rate": "lr", "eta": "lr", "depth": "depth", "max_depth": "depth", "l2_leaf_reg": "l2", "reg_lambda": "l2"},
}
CONTROL_NAMES["double_ensemble"] = {"learning_rate": "lr", "num_leaves": "leaves"}
# What TALYX sets itself, with the control that sets it.
TALYX_SETS = {"num_threads": "Threads", "nthread": "Threads", "thread_count": "Threads", "n_jobs": "Threads", "num_thread": "Threads", "threads": "Threads",
              "seed": "Seed", "random_seed": "Seed", "random_state": "Seed", "bagging_seed": "Seed", "feature_fraction_seed": "Seed", "data_random_seed": "Seed",
              "deterministic": "Reproducible", "force_col_wise": "Reproducible", "force_row_wise": "Reproducible",
              "objective": "Objective", "loss": "Objective", "loss_function": "Objective", "num_class": "Objective", "application": "Objective", "alpha_quantile": "Objective",
              "qid": "Objective", "group_id": "Objective", "label_gain": "Objective", "eval_metric": "Objective", "metric": "Objective",
              "early_stopping_round": "Early stop", "early_stopping_rounds": "Early stop", "early_stopping": "Early stop",
              "num_boost_round": "Rounds", "num_iterations": "Rounds", "n_estimators": "Rounds", "iterations": "Rounds", "num_round": "Rounds",
              "verbosity": "Run settings", "verbose": "Run settings", "silent": "Run settings", "allow_writing_files": "Run settings"}
INTEGER_KEYS = {"max_bin", "num_leaves", "max_depth", "min_data_in_leaf", "min_child_samples", "bagging_freq", "min_data_in_bin", "max_cat_threshold", "depth", "border_count",
                "n_estimators", "min_data_per_group", "cat_smooth_count", "max_leaves", "grow_policy_count"}


def _split(text):
    parts, depth, quote, current = [], 0, None, ""
    for char in text:
        if quote:
            current += char
            quote = None if char == quote else quote
        elif char in "\"'":
            quote = char
            current += char
        elif char in "[(":
            depth += 1
            current += char
        elif char in "])":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    return parts


# What the controls and TALYX itself set on a neural network, by the Qlib constructor's own names.
NETWORK_TALYX_KEYS = {"n_epochs", "batch_size", "lr", "early_stop", "dropout", "num_layers", "hidden_size", "d_model", "nhead", "optimizer", "fore_optimizer", "metric", "loss",
                      "d_feat", "fea_dim", "input_dim", "GPU", "seed", "n_jobs", "model_path", "base_model", "stock2concept", "stock_index", "save_path", "max_steps",
                      "early_stop_rounds", "eval_steps", "pt_model_kwargs", "scheduler", "n_chans", "rnn_layers", "rnn_dim", "cnn_dim", "dropout_W", "dropout_U",
                      "pretrain", "pretrain_n_epochs", "n_d", "n_a", "out_dim", "kwargs"}
_NETWORK_CLASS = {"mlp": ("pytorch_nn", "DNNModelPytorch"), "lstm": ("pytorch_lstm", "LSTM"), "gru": ("pytorch_gru", "GRU"), "alstm": ("pytorch_alstm", "ALSTM"),
                  "transformer": ("pytorch_transformer", "TransformerModel"), "localformer": ("pytorch_localformer", "LocalformerModel"), "tcn": ("pytorch_tcn", "TCN"),
                  "tabnet": ("pytorch_tabnet", "TabnetModel"), "sfm": ("pytorch_sfm", "SFM"), "krnn": ("pytorch_krnn", "KRNN"), "sandwich": ("pytorch_sandwich", "Sandwich"),
                  "gats": ("pytorch_gats", "GATs"), "igmtf": ("pytorch_igmtf", "IGMTF"), "add": ("pytorch_add", "ADD"), "adarnn": ("pytorch_adarnn", "ADARNN"),
                  "tra": ("pytorch_tra", "TRAModel"), "tcts": ("pytorch_tcts", "TCTS"), "hist": ("pytorch_hist", "HIST")}


def network_constructor_names(algorithm):
    """The names the Qlib network's constructor takes, read from its source without importing it (the research environment has no PyTorch)."""
    import importlib.util
    module, name = _NETWORK_CLASS[algorithm]
    spec = importlib.util.find_spec(f"qlib.contrib.model.{module}")
    tree = ast.parse(open(spec.origin, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    return {arg.arg for arg in item.args.args if arg.arg != "self"}
    return set()


def _library_has(algorithm, key):
    """Whether the algorithm's library takes this parameter name, by what the library itself says."""
    if algorithm in ("lightgbm", "double_ensemble"):
        from lightgbm.basic import _ConfigAliases
        table = _ConfigAliases._get_all_param_aliases()
        return key in table or any(key in names for names in table.values())
    if algorithm in ("linear", "ridge", "lasso", "nnls"):
        import inspect

        from qlib.contrib.model.linear import LinearModel
        return key in inspect.signature(LinearModel.__init__).parameters
    probe = np.random.default_rng(0).standard_normal((20, 3))
    if algorithm == "xgboost":
        import re
        import warnings

        import xgboost as xgb
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            xgb.train({"verbosity": 1, key: 1}, xgb.DMatrix(probe, label=probe[:, 0]), 1)
        unused = {name for item in caught for name in re.findall(r'"([^"]+)"', str(item.message)) if "not used" in str(item.message)}
        return key not in unused
    if algorithm == "catboost":
        import catboost as cb
        try:
            cb.CatBoost({"iterations": 1, "verbose": False, key: 1, "allow_writing_files": False}).fit(probe, probe[:, 0])
        except Exception as error:  # noqa: BLE001 - the library says whether it knows the option
            return "Unknown option" not in str(error) or f"{{{key}}}" not in str(error)
        return True
    return True


def _network_engine_params(algorithm, text, label):
    names = network_constructor_names(algorithm)
    out = {}
    for part in _split(text or ""):
        if "=" not in part:
            raise ValueError("Write key=value, separated by commas")
        key, raw = (piece.strip() for piece in part.split("=", 1))
        if not key.isidentifier():
            raise ValueError("Write key=value, separated by commas")
        if key in NETWORK_TALYX_KEYS:
            raise ValueError(f"{key} is set by TALYX.")
        if key not in names:
            raise ValueError(f"{key}: {label} has no such parameter")
        try:
            value = ast.literal_eval(raw) if raw not in ("true", "false") else raw == "true"
        except (ValueError, SyntaxError):
            raise ValueError(f"{key}: a number, true or false, quoted text or a list in brackets") from None
        out[key] = list(value) if isinstance(value, tuple) else value
    return out


def engine_params(algorithm, text, declared=()):
    """`key=value, key=value` as the library's own parameters: values are literals only (never run as code); a key the library does not have, one
    a control above sets (`declared` holds the controls' short names), and one TALYX sets itself are refused with the reason."""
    out = {}
    label = BY_ID[algorithm]["label"] if algorithm in BY_ID else algorithm
    if algorithm in NEURAL_IDS:
        return _network_engine_params(algorithm, text, label)
    for part in _split(text or ""):
        if "=" not in part:
            raise ValueError("Write key=value, separated by commas")
        key, raw = (piece.strip() for piece in part.split("=", 1))
        if not key.isidentifier():
            raise ValueError("Write key=value, separated by commas")
        if key in TALYX_SETS:
            raise ValueError(f"{key} is set by TALYX ({TALYX_SETS[key]}).")
        control = CONTROL_NAMES.get(algorithm, {}).get(key)
        if control and control in set(declared):
            raise ValueError(f"{key} is set above; change it there.")
        if not _library_has(algorithm, key):
            raise ValueError(f"{key}: {label} has no such parameter")
        try:
            value = ast.literal_eval(raw) if raw not in ("true", "false") else raw == "true"
        except (ValueError, SyntaxError):
            value = None
            ok = False
        else:
            ok = isinstance(value, (bool, int, float, str, list, tuple)) and (not isinstance(value, (list, tuple)) or all(isinstance(item, (int, float, str)) for item in value))
        if not ok or (key in INTEGER_KEYS and (type(value) is not int)):
            raise ValueError(f"{key}: a whole number" if key in INTEGER_KEYS else f"{key}: a number, true or false, quoted text or a list in brackets")
        out[key] = list(value) if isinstance(value, tuple) else value
    return out

# ---------------------------------------------------------------------------------------------------------------- Qlib's dataset, one window
class WindowDataset:
    """What Qlib's models read: `prepare(segment, col_set, data_key)` gives the window's rows as feature and label columns."""

    def __init__(self, parts, features):
        from qlib.data.dataset import DatasetH  # noqa: F401 - Qlib must be importable
        self.parts, self.features = parts, features
        self.segments = {name: (0, 0) for name in parts}

    def _frame(self, name, col_set):
        part = self.parts[name]
        index = pd.MultiIndex.from_arrays([part["t"], part["i"]], names=["datetime", "instrument"])
        sets = [col_set] if isinstance(col_set, str) else list(col_set)
        frames = []
        if "feature" in sets:
            frames.append(pd.DataFrame(part["x"], index=index, columns=pd.MultiIndex.from_product([["feature"], self.features])))
        if "label" in sets:
            frames.append(pd.DataFrame(np.asarray(part["y"], dtype=np.float32).reshape(-1, 1), index=index, columns=pd.MultiIndex.from_tuples([("label", "LABEL0")])))
        frame = pd.concat(frames, axis=1) if len(frames) > 1 else frames[0]
        if col_set == "feature":
            return frame["feature"]
        return frame

    def prepare(self, segments, col_set="__all", data_key=None, **_):
        if isinstance(segments, str):
            return self._frame(segments, col_set)
        return [self._frame(name, col_set) for name in segments]


# ---------------------------------------------------------------------------------------------------------------- fitting
class Fitted:
    """One window's trained model and what it reports."""

    def __init__(self, algorithm, model, features, curve, best, objective="l2", k=5):
        self.algorithm, self.model, self.features, self.curve, self.best = algorithm, model, features, curve, best
        self.objective, self.k = objective, k

    def predict(self, x):
        x = np.asarray(x, dtype=np.float32)
        if not len(x):
            return np.zeros(0)
        native = self.model
        if self.algorithm == "lightgbm":
            return _scores(native.model.predict(x), self.objective, self.k)
        if self.algorithm == "xgboost":
            import xgboost as xgb
            return _scores(native.model.predict(xgb.DMatrix(x)), self.objective, self.k)
        if self.algorithm == "catboost":
            return _scores(native.model.predict(x), self.objective, self.k)
        if self.algorithm in ("linear", "ridge", "lasso", "nnls"):
            return np.nan_to_num(x) @ native.coef_ + native.intercept_
        if self.algorithm == "double_ensemble":
            frame = pd.DataFrame(x, columns=self.features)
            return np.asarray(_ensemble_predict(native, frame))
        raise ValueError(self.algorithm)

    def importance(self, x=None):
        """{feature: importance} by gain (trees), or |coefficient| × the feature's spread on `x` (linear)."""
        native = self.model
        if self.algorithm == "lightgbm":
            values = native.model.feature_importance(importance_type="gain")
        elif self.algorithm == "xgboost":
            score = native.model.get_score(importance_type="total_gain")
            values = [score.get(f"f{index}", 0.0) for index in range(len(self.features))]
        elif self.algorithm == "catboost":
            values = native.model.get_feature_importance()
        elif self.algorithm in ("linear", "ridge", "lasso", "nnls"):
            spread = np.nanstd(np.asarray(x, dtype=float), axis=0) if x is not None and len(x) else np.ones(len(self.features))
            values = np.abs(native.coef_) * np.nan_to_num(spread, nan=0.0)
        elif self.algorithm == "double_ensemble":
            values = np.zeros(len(self.features))
            for booster, chosen in zip(native.ensemble, native.sub_features, strict=True):
                gains = booster.feature_importance(importance_type="gain")
                for name, gain in zip(chosen, gains, strict=True):
                    values[self.features.index(name)] += gain
        else:
            values = np.zeros(len(self.features))
        values = np.asarray(values, dtype=float)
        total = values.sum()
        share = values / total if total > 0 else values
        return {name: float(value) for name, value in zip(self.features, share, strict=True)}

    def contributions(self, x):
        """Each row's prediction split by feature (the last column is the base value): LightGBM's pred_contrib, XGBoost's contributions,
        CatBoost's SHAP values, or coefficient × value for linear models. None where the algorithm has none."""
        x = np.asarray(x, dtype=np.float32)
        native = self.model
        if self.algorithm == "lightgbm" and self.objective != "multiclass":
            return native.model.predict(x, pred_contrib=True)
        if self.algorithm == "xgboost":
            import xgboost as xgb
            return native.model.predict(xgb.DMatrix(x), pred_contribs=True)
        if self.algorithm == "catboost":
            from catboost import Pool
            return native.model.get_feature_importance(Pool(x), type="ShapValues")
        if self.algorithm in ("linear", "ridge", "lasso", "nnls"):
            parts = np.nan_to_num(x) * native.coef_
            return np.hstack([parts, np.full((len(x), 1), native.intercept_)])
        return None


def _ensemble_predict(model, frame):
    """DoubleEnsemble's own prediction (it reads a frame by feature names)."""
    out = pd.Series(np.zeros(len(frame)), index=frame.index)
    for booster, chosen, weight in zip(model.ensemble, model.sub_features, model.sub_weights, strict=True):
        out += booster.predict(frame.loc[:, chosen].values) * weight
    return (out / float(np.sum(model.sub_weights))).to_numpy()


class _Native:
    """What a fitted booster is kept in: the library's own model under `.model`, as the Qlib classes held it."""

    def __init__(self, model):
        self.model = model


class _Weights:
    """Qlib's reweighter for a linear model: the weights of the rows it is given, in order."""

    def __init__(self, values):
        self.values = values

    def reweight(self, data):
        import pandas as pd
        return pd.Series(self.values, index=data.index)


def _targets(objective, k, y, t):
    """The label each objective trains on: the label itself for a regression; for the others the day's own order of it (see the build draft, B11)."""
    y = np.asarray(y, dtype=float)
    if objective in ("l2", "l1", "huber", "quantile"):
        return y
    day = pd.Series(y).groupby(np.asarray(t))
    if objective == "binary":
        values = set(np.unique(y[np.isfinite(y)]).tolist())
        if values <= {0.0, 1.0}:
            return y  # a label that is already 0 and 1 is used as it is
        return (y > day.transform("median").to_numpy()).astype(float)
    share = day.rank(pct=True, method="first").to_numpy()
    classes = int(k) if objective == "multiclass" else 5
    return np.minimum(np.floor(share * classes), classes - 1)


def _groups(t):
    """Rows of each day, in the order they come (rows are sorted by day): what the ranking objectives group by."""
    return np.unique(np.asarray(t), return_counts=True)[1]


def _weight_of(part):
    weight = part.get("w")
    return None if weight is None else np.asarray(weight, dtype=float)


def _scores(raw, objective, k):
    raw = np.asarray(raw)
    if objective == "multiclass" and raw.ndim == 2:
        return raw @ np.arange(raw.shape[1])  # the expected group number
    return raw


# Nothing of these stops on the validation rows, so they train on them too (Qlib's include_valid): their validation IC is in-sample and not kept.
TRAINS_ON_VALID = ("linear", "ridge", "lasso", "nnls", "double_ensemble")


def _joined(first, second):
    """Two parts' rows as one (train then validation), the weights too when both have them."""
    out = {key: np.concatenate([np.asarray(first[key]), np.asarray(second[key])]) for key in ("x", "y", "t", "i") if key in first and key in second}
    if first.get("w") is not None and second.get("w") is not None:
        out["w"] = np.concatenate([np.asarray(first["w"]), np.asarray(second["w"])])
    return out


def fit(algorithm, parts, features, params, *, seed=42, reproducible=True, threads=1, on_round=None, objective="l2", objective_param=None, engine=None):
    """Train one window: `parts` holds train, valid (and test) rows, each with the weight of its rows when there is one; returns a Fitted model.
    `on_round(round, total)` hears progress. `objective` is one of OBJECTIVES (the library's own name for it is below), `objective_param` its number
    (δ, q or the number of groups), `engine` the library's own parameters from Engine parameters (never what a control above sets)."""
    params = checked_params(algorithm, params)
    if objective not in SUPPORTS.get(algorithm, ["l2"]):
        raise ValueError(f"{BY_ID[algorithm]['label']} cannot train {objective}")
    engine = dict(engine or {})
    k = int(objective_param or 5)
    dataset = WindowDataset({key: parts[key] for key in ("train", "valid")}, features)
    evals = {}
    stop = int(params.get("stop", 0)) or None
    train, valid = parts["train"], parts["valid"]
    ranking = objective in RANKING
    if algorithm == "lightgbm":
        import lightgbm as lgb
        kwargs = {"learning_rate": params["lr"], "num_leaves": params["leaves"], "max_depth": params["depth"], "feature_fraction": params["ff"],
                  "bagging_fraction": params["bf"], "bagging_freq": 1 if params["bf"] < 1 else 0, "lambda_l1": params["l1"], "lambda_l2": params["l2"],
                  "min_data_in_leaf": params["minleaf"], "num_threads": int(threads), "seed": int(seed), "verbosity": -1}
        if reproducible:
            kwargs.update(deterministic=True, force_col_wise=True)
        named = {"l2": {"objective": "mse"}, "l1": {"objective": "regression_l1"}, "huber": {"objective": "huber", "alpha": float(objective_param or 1.0)},
                 "quantile": {"objective": "quantile", "alpha": float(objective_param or 0.5)}, "binary": {"objective": "binary"},
                 "multiclass": {"objective": "multiclass", "num_class": k}, "lambdarank": {"objective": "lambdarank", "label_gain": [0, 1, 2, 3, 4]},
                 "xendcg": {"objective": "rank_xendcg"}}[objective]
        lgb_params = {**kwargs, **named, **engine}
        rounds = params["rounds"]

        def made(part):
            y = _targets(objective, k, part["y"], part["t"])
            data = lgb.Dataset(np.asarray(part["x"], dtype=np.float32), label=y, weight=_weight_of(part))
            if ranking:
                data.set_group(_groups(part["t"]))
            return data
        sets = [made(train), made(valid)]
        callbacks = [lgb.record_evaluation(evals)]
        if stop:
            callbacks.append(lgb.early_stopping(stop, verbose=False))
        if on_round is not None:
            callbacks.append(lambda env: on_round(env.iteration + 1, env.end_iteration))
        booster = lgb.train(lgb_params, sets[0], num_boost_round=rounds, valid_sets=list(sets), valid_names=["train", "valid"], callbacks=callbacks)
        metric = next(iter(evals["train"]))
        curve = {"train": [float(v) for v in evals["train"][metric]], "valid": [float(v) for v in evals["valid"][metric]]}
        best = int(booster.best_iteration or len(curve["valid"]))
        model = _Native(booster)
    elif algorithm == "xgboost":
        import xgboost as xgb
        named = {"l2": {"objective": "reg:squarederror"}, "l1": {"objective": "reg:absoluteerror"}, "huber": {"objective": "reg:pseudohubererror", "huber_slope": float(objective_param or 1.0)},
                 "quantile": {"objective": "reg:quantileerror", "quantile_alpha": float(objective_param or 0.5)}, "binary": {"objective": "binary:logistic"},
                 "multiclass": {"objective": "multi:softprob", "num_class": k}, "lambdarank": {"objective": "rank:ndcg"}, "pairwise": {"objective": "rank:pairwise"}}[objective]
        xgb_params = {"eta": params["lr"], "max_depth": params["depth"], "colsample_bytree": params["ff"], "subsample": params["bf"], "alpha": params["l1"],
                      "lambda": params["l2"], "min_child_weight": params["minleaf"], "nthread": int(threads), "seed": int(seed), "tree_method": "hist", "verbosity": 0,
                      **named, **engine}

        def made(part):
            data = xgb.DMatrix(np.asarray(part["x"], dtype=np.float32), label=_targets(objective, k, part["y"], part["t"]), weight=_weight_of(part) if not ranking else None)
            if ranking:
                data.set_info(qid=np.asarray(part["t"]))
            return data
        dtrain, dvalid = made(train), made(valid)
        callbacks = []
        if on_round is not None:
            class _Progress(xgb.callback.TrainingCallback):
                def after_iteration(self, model, epoch, evals_log):
                    on_round(epoch + 1, params["rounds"])
                    return False
            callbacks.append(_Progress())
        booster = xgb.train(xgb_params, dtrain, num_boost_round=params["rounds"], evals=[(dtrain, "train"), (dvalid, "valid")], early_stopping_rounds=stop,
                            evals_result=evals, verbose_eval=False, callbacks=callbacks)
        metric = next(iter(evals["train"]))
        curve = {"train": [float(v) for v in evals["train"][metric]], "valid": [float(v) for v in evals["valid"][metric]]}
        best = int(getattr(booster, "best_iteration", len(curve["valid"]) - 1)) + 1
        booster = booster[:best]   # xgb.train returns the last round's model: the prediction, contributions and importance are the best round's
        model = _Native(booster)
    elif algorithm == "catboost":
        import catboost as cb
        named = {"l2": {"loss_function": "RMSE"}, "l1": {"loss_function": "MAE"}, "huber": {"loss_function": f"Huber:delta={float(objective_param or 1.0)}"},
                 "quantile": {"loss_function": f"Quantile:alpha={float(objective_param or 0.5)}"}, "binary": {"loss_function": "Logloss"},
                 "multiclass": {"loss_function": "MultiClass", "classes_count": k}, "lambdarank": {"loss_function": "YetiRank"}, "pairwise": {"loss_function": "PairLogit"}}[objective]

        def made(part):
            return cb.Pool(np.asarray(part["x"], dtype=np.float32), label=_targets(objective, k, part["y"], part["t"]), weight=_weight_of(part) if not ranking else None,
                           group_id=np.asarray(part["t"]) if ranking else None)
        cat_params = {"learning_rate": params["lr"], "depth": params["depth"], "l2_leaf_reg": params["l2"], "thread_count": int(threads), "random_seed": int(seed),
                      "allow_writing_files": False, "iterations": params["rounds"], "verbose": False, **named, **engine}
        booster = cb.CatBoost(cat_params)
        booster.fit(made(train), eval_set=made(valid), early_stopping_rounds=stop or params["rounds"] + 1, use_best_model=True, verbose=False)
        found = booster.get_evals_result()
        first = list(found["learn"].values())[0]
        second = list(found["validation"].values())[0]
        square = objective == "l2"  # RMSE is shown as the squared error, as before
        curve = {"train": [float(v) ** 2 if square else float(v) for v in first], "valid": [float(v) ** 2 if square else float(v) for v in second]}
        best = int(booster.get_best_iteration() or 0) + 1
        model = _Native(booster)
    elif algorithm in ("linear", "ridge", "lasso", "nnls"):
        from qlib.contrib.model.linear import LinearModel
        estimator = {"linear": "ols"}.get(algorithm, algorithm)
        model = LinearModel(estimator=estimator, alpha=float(params.get("alpha", 0.0)), fit_intercept=True)
        part = _joined(train, valid)   # nothing stops on the validation rows, so they are trained on (Qlib's include_valid)
        dataset.parts["train"] = {**part, "x": np.nan_to_num(part["x"])}
        weight = _weight_of(part)
        model.fit(dataset, reweighter=_Weights(weight) if weight is not None and algorithm != "nnls" else None)
        return Fitted(algorithm, model, features, None, None, objective, k)
    elif algorithm == "double_ensemble":
        from qlib.contrib.model.double_ensemble import DEnsembleModel
        # Qlib's benchmark settings (workflow_config_doubleensemble_Alpha158) for what the preparation does not show.
        model = DEnsembleModel(base_model="gbm", loss="mse", num_models=params["models"], epochs=params["rounds"], learning_rate=params["lr"],
                               num_leaves=params["leaves"], num_threads=int(threads), seed=int(seed), verbosity=-1, enable_sr=True, enable_fs=True,
                               alpha1=1, alpha2=1, bins_sr=10, bins_fs=5, decay=0.5, sample_ratios=[0.8, 0.7, 0.6, 0.5, 0.4],
                               sub_weights=[1] * int(params["models"]), early_stopping_rounds=None,
                               **({"deterministic": True, "force_col_wise": True} if reproducible else {}), **engine)
        dataset.parts["train"] = _joined(train, valid)   # early stopping is off: the validation rows are trained on
        model.fit(dataset)
        curve, best = None, None
    else:
        raise ValueError(f"{BY_ID.get(algorithm, {}).get('label', algorithm)} cannot be trained here")
    return Fitted(algorithm, model, features, curve, best, objective, k)


def model_file(folder, *, must_exist=True):
    """The file a window's model is in: model.pt for a neural network, model.pkl otherwise."""
    from pathlib import Path
    folder = Path(folder)
    return folder / "model.pt" if (folder / "model.pt").is_file() else folder / "model.pkl"


def save(fitted, path):
    if getattr(fitted, "neural", False):
        from native import model_deep
        model_deep.save(fitted, str(path).replace("model.pkl", "model.pt"))
        return
    with open(path, "wb") as stream:
        pickle.dump({"algorithm": fitted.algorithm, "model": fitted.model, "features": fitted.features, "curve": fitted.curve, "best": fitted.best,
                     "objective": fitted.objective, "k": fitted.k}, stream, protocol=5)


def load(path):
    path = str(path)
    if path.endswith("model.pkl") and __import__("os").path.isfile(path[:-4] + ".pt"):
        from native import model_deep
        return model_deep.load(path[:-4] + ".pt")
    with open(path, "rb") as stream:
        found = pickle.load(stream)  # noqa: S301 - the run's own window cache, written above
    return Fitted(found["algorithm"], found["model"], found["features"], found["curve"], found["best"], found.get("objective", "l2"), found.get("k", 5))
