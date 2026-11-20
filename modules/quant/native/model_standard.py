"""TALYX's standard model pipeline: inputs, processing, the label, walk-forward windows and one algorithm, trained window by window.

Each setting below is declared once; the preparation shows them in these groups, and a model only keeps their values. Each run
keeps and executes its own copy of this file. "Write as code" copies it into the model's project, where it
can be changed freely while the same settings stay in the same places.

`q.model_data` builds the data set from the inputs, the processing (statistics over time are fitted on each window's training
segment only), the label (from the next open) and the windows (`days` bars apart). `q.fit` trains one window with one of the
algorithms the research environment can run; `q.record` keeps the window's model, numbers and predictions.
"""
import talyx_quant as q

__version__ = "1.0"

# Data: the universe and the period are the data settings; these leave names out of a day.
leave_out_halted = q.param("leave_out_halted", True, label="Leave out halted days", group="Data", help="Days without a price or volume")
leave_out_young = q.param("leave_out_young", True, label="Leave out names listed under 1 year", group="Data", help="Names listed under a year")
leave_out_illiquid = q.param("leave_out_illiquid", False, label="Leave out names under the minimum a day", group="Data", help="Names trading under the minimum a day, 20-day average")
min_dollar = q.param("min_dollar", 5000000.0, min=0.0, label="Minimum a day", group="Data", show_if={"leave_out_illiquid": True},
                     help="Dollar volume a day, in the quote currency")

# Inputs: one row each; source is library, run, value, builtin, market or code.
inputs = q.table("inputs", [], columns=["source", "name", "ref"], label="Inputs", group="Inputs",
                 help="Library factors at a version, factor runs not yet published, built-in sets, Market series and features written in code")
keep_columns = q.param("keep_columns", "", label="Built-in columns", group="Inputs",
                       help="Only these columns of the built-in sets, such as KMID, ROC5 (Qlib's FilterCol); empty keeps them all")

# Processing: inputs and the label are set apart.
inputs_missing = q.choice("inputs_missing", "median", [("median", "Day's median"), ("mean", "Day's mean"), ("zero", "Zero"), ("industry", "Industry median"),
                                                       ("drop", "Drop the row"), ("model", "Leave to the model"), ("train_median", "Training median")],
                          label="Missing", group="Processing", help="Training median is fitted on each window's training segment")
inputs_extremes = q.choice("inputs_extremes", "mad3", [("mad3", "Clip at 3 MAD"), ("mad5", "Clip at 5 MAD"), ("none", "None")], label="Extremes", group="Processing")
inputs_scale = q.choice("inputs_scale", "rank", [("rank", "Rank each day"), ("zscore", "Z-score each day"), ("robust", "Robust z-score, training segment"),
                                                 ("zscore_train", "Z-score, training segment"), ("minmax", "Min–max, training segment"), ("tanh", "Tanh"),
                                                 ("none", "None")], label="Scale", group="Processing", help="Fitted on each window's training segment")
take_out_industry = q.param("take_out_industry", False, label="Take out industry", group="Processing")
take_out_size = q.param("take_out_size", False, label="Take out size", group="Processing")
label_missing = q.choice("label_missing", "drop", [("drop", "Drop the row")], label="Label missing", group="Processing")
label_extremes = q.choice("label_extremes", "mad3", [("mad3", "Clip at 3 MAD"), ("none", "None")], label="Label extremes", group="Processing")
label_scale = q.choice("label_scale", "rank", [("rank", "Rank each day"), ("zscore", "Z-score each day"), ("none", "None")], label="Label scale", group="Processing")

# Label: a formula (it may read later bars with Ref); the quick choices in the preparation write it.
label = q.param("label", "CSRank(Ref($open, -6) / Ref($open, -1) - 1)", label="Label", group="Label")
objective = q.choice("objective", "l2", [("l2", "Regression · L2"), ("l1", "Regression · L1"), ("huber", "Regression · Huber"), ("quantile", "Regression · Quantile"),
                                         ("binary", "Classification · Binary"), ("multiclass", "Classification · Groups"), ("lambdarank", "Ranking · LambdaRank"),
                                         ("xendcg", "Ranking · XE-NDCG"), ("pairwise", "Ranking · Pairwise")], label="Objective", group="Label")
objective_param = q.param("objective_param", 1.0, label="", group="Label", show_if={"objective": ["huber", "quantile", "multiclass"]})
weight = q.choice("weight", "equal", [("equal", "Equal"), ("size", "Market value"), ("formula", "Formula")], label="Sample weight", group="Label")
label_aux = q.param("label_aux", "", label="Auxiliary labels", group="Label", show_if={"algorithm": "tcts"}, help="One formula in each line; none means the label's own return one and two bars further out")
weight_formula = q.param("weight_formula", "", label="Weight formula", group="Label", show_if={"weight": "formula"})

# Split: walk-forward windows; the gap between segments is what the label reads ahead.
train_months = q.choice("train_months", 36, [(12, "1 year"), (24, "2 years"), (36, "3 years"), (60, "5 years")], label="Training", group="Split")
window_mode = q.choice("window_mode", "rolling", [("rolling", "Rolling"), ("expanding", "Expanding")], label="Window", group="Split")
valid_months = q.choice("valid_months", 6, [(3, "3 months"), (6, "6 months"), (12, "12 months")], label="Validation", group="Split")
every_months = q.choice("every_months", 3, [(1, "1 month"), (3, "3 months"), (6, "6 months")], label="Retrain every", group="Split",
                        help="Also the length of each test period")
first_test = q.param("first_test", "", label="First test", group="Split", help="Empty: the first test starts after the first training and validation")

# Algorithm and its own settings.
algorithm = q.choice("algorithm", "", [("", "Choose"), ("lightgbm", "LightGBM"), ("xgboost", "XGBoost"), ("catboost", "CatBoost"), ("linear", "Least squares"),
                                       ("ridge", "Ridge"), ("lasso", "Lasso"), ("nnls", "Non-negative least squares"), ("double_ensemble", "DoubleEnsemble"),
                                       ("mlp", "MLP"), ("lstm", "LSTM"), ("gru", "GRU"), ("alstm", "ALSTM"),
                                       ("transformer", "Transformer"), ("localformer", "Localformer"), ("tcn", "TCN"), ("tabnet", "TabNet"), ("sfm", "SFM"),
                                       ("krnn", "KRNN"), ("sandwich", "Sandwich"), ("gats", "GATs"), ("igmtf", "IGMTF"), ("add", "ADD"),
                                       ("adarnn", "ADARNN"), ("tra", "TRA"), ("tcts", "TCTS"), ("hist", "HIST")],
                     label="Algorithm", group="Algorithm")
lgb_lr = q.param("lgb_lr", 0.05, min=0.0001, max=1.0, label="Learning rate", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_leaves = q.param("lgb_leaves", 210, min=2, max=4096, label="Leaves", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_depth = q.param("lgb_depth", 8, min=-1, max=64, label="Max depth", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_ff = q.param("lgb_ff", 0.8, min=0.05, max=1.0, label="Feature fraction", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_bf = q.param("lgb_bf", 0.8, min=0.05, max=1.0, label="Row fraction", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_l1 = q.param("lgb_l1", 0.0, min=0.0, label="L1", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_l2 = q.param("lgb_l2", 0.0, min=0.0, label="L2", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_minleaf = q.param("lgb_minleaf", 100, min=1, label="Min data in leaf", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_rounds = q.param("lgb_rounds", 1000, min=1, max=20000, label="Rounds, up to", group="Algorithm", show_if={"algorithm": "lightgbm"})
lgb_stop = q.param("lgb_stop", 50, min=0, label="Early stop", group="Algorithm", show_if={"algorithm": "lightgbm"},
                   help="Stops when the validation loss has not improved for this many rounds; 0 turns it off")
xgb_lr = q.param("xgb_lr", 0.05, min=0.0001, max=1.0, label="Learning rate", group="Algorithm", show_if={"algorithm": "xgboost"})
xgb_depth = q.param("xgb_depth", 6, min=1, max=32, label="Max depth", group="Algorithm", show_if={"algorithm": "xgboost"})
xgb_ff = q.param("xgb_ff", 0.8, min=0.05, max=1.0, label="Column sample", group="Algorithm", show_if={"algorithm": "xgboost"})
xgb_bf = q.param("xgb_bf", 0.8, min=0.05, max=1.0, label="Row sample", group="Algorithm", show_if={"algorithm": "xgboost"})
xgb_l1 = q.param("xgb_l1", 0.0, min=0.0, label="Alpha", group="Algorithm", show_if={"algorithm": "xgboost"}, help="L1 regularization")
xgb_l2 = q.param("xgb_l2", 1.0, min=0.0, label="Lambda", group="Algorithm", show_if={"algorithm": "xgboost"}, help="L2 regularization")
xgb_minleaf = q.param("xgb_minleaf", 20.0, min=0.0, label="Min child weight", group="Algorithm", show_if={"algorithm": "xgboost"})
xgb_rounds = q.param("xgb_rounds", 1000, min=1, max=20000, label="Rounds, up to", group="Algorithm", show_if={"algorithm": "xgboost"})
xgb_stop = q.param("xgb_stop", 50, min=0, label="Early stop", group="Algorithm", show_if={"algorithm": "xgboost"},
                   help="Stops when the validation loss has not improved for this many rounds; 0 turns it off")
cat_lr = q.param("cat_lr", 0.05, min=0.0001, max=1.0, label="Learning rate", group="Algorithm", show_if={"algorithm": "catboost"})
cat_depth = q.param("cat_depth", 6, min=1, max=16, label="Depth", group="Algorithm", show_if={"algorithm": "catboost"})
cat_l2 = q.param("cat_l2", 3.0, min=0.0, label="L2 leaf", group="Algorithm", show_if={"algorithm": "catboost"})
cat_rounds = q.param("cat_rounds", 1000, min=1, max=20000, label="Rounds, up to", group="Algorithm", show_if={"algorithm": "catboost"})
cat_stop = q.param("cat_stop", 50, min=0, label="Early stop", group="Algorithm", show_if={"algorithm": "catboost"},
                   help="Stops when the validation loss has not improved for this many rounds; 0 turns it off")
ridge_alpha = q.param("ridge_alpha", 1.0, min=0.0, label="Alpha", group="Algorithm", show_if={"algorithm": "ridge"}, help="Regularization strength")
lasso_alpha = q.param("lasso_alpha", 0.001, min=0.0, label="Alpha", group="Algorithm", show_if={"algorithm": "lasso"}, help="Regularization strength")
dens_lr = q.param("dens_lr", 0.05, min=0.0001, max=1.0, label="Learning rate", group="Algorithm", show_if={"algorithm": "double_ensemble"})
dens_leaves = q.param("dens_leaves", 63, min=2, max=4096, label="Leaves", group="Algorithm", show_if={"algorithm": "double_ensemble"})
dens_models = q.param("dens_models", 6, min=1, max=20, label="Sub-models", group="Algorithm", show_if={"algorithm": "double_ensemble"})
dens_rounds = q.param("dens_rounds", 200, min=1, max=5000, label="Rounds each", group="Algorithm", show_if={"algorithm": "double_ensemble"})
nn_epochs = q.param("nn_epochs", 100, min=1, max=2000, label="Epochs, up to", group="Algorithm", show_if={"algorithm": ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tra", "tcts", "hist"]})
nn_stop = q.param("nn_stop", 20, min=0, label="Early stop", group="Algorithm", show_if={"algorithm": ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tra", "tcts", "hist"]}, help="Stops when the validation loss has not improved for this many epochs")
nn_optimizer = q.choice("nn_optimizer", "adam", [("adam", "Adam"), ("gd", "SGD")], label="Optimizer", group="Algorithm", show_if={"algorithm": ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tcts", "hist"]})
nn_batch = q.param("nn_batch", 800, min=1, label="Batch size", group="Algorithm", show_if={"algorithm": ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "add", "adarnn", "tra", "tcts"]})
nn_lr = q.param("nn_lr", 0.001, min=0.000001, max=1.0, label="Learning rate", group="Algorithm", show_if={"algorithm": ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tra", "tcts", "hist"]})
nn_dropout = q.param("nn_dropout", 0.0, min=0.0, max=0.9, label="Dropout", group="Algorithm", show_if={"algorithm": ["lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tcts", "hist"]})
nn_layers = q.param("nn_layers", 2, min=1, max=16, label="Layers", group="Algorithm", show_if={"algorithm": ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tcts", "hist"]})
nn_width = q.param("nn_width", 64, min=1, max=4096, label="Width", group="Algorithm", show_if={"algorithm": ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tra", "tcts", "hist"]}, help="Hidden units in each layer")
nn_lookback = q.param("nn_lookback", 20, min=1, max=250, label="Look back", group="Algorithm", show_if={"algorithm": ["lstm", "gru", "alstm", "transformer", "localformer", "tcn", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tra", "tcts", "hist"]},
                      help="Days of history each sample sees; memory grows with it")
nn_heads = q.param("nn_heads", 2, min=1, max=16, label="Heads", group="Algorithm", show_if={"algorithm": ["transformer", "localformer"]})
nn_pretrain = q.param("nn_pretrain", 50, min=0, label="Pretrain epochs", group="Algorithm", show_if={"algorithm": "tabnet"})
nn_device = q.param("nn_device", "", label="Device", group="Algorithm", show_if={"algorithm": ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tra", "tcts", "hist"]})
nn_metric = q.choice("nn_metric", "ic", [("ic", "Validation IC"), ("loss", "Validation loss"), ("ric", "Validation rank IC")], label="Early stop on", group="Algorithm", show_if={"algorithm": ["add", "adarnn"]})
nn_base = q.param("nn_base", "", label="Start from", group="Algorithm", show_if={"algorithm": ["gats", "igmtf", "add", "hist"]}, help="A finished LSTM or GRU run of this model whose weights the network starts from")
nn_concepts = q.param("nn_concepts", "", label="Concepts", group="Algorithm", show_if={"algorithm": "hist"}, help="The concept-sector table HIST reads: which names belong to which concepts")
nn_gamma = q.param("nn_gamma", 0.1, min=0.0, max=1.0, label="Market-state weight", group="Algorithm", show_if={"algorithm": "add"})
nn_domains = q.param("nn_domains", 2, min=2, max=10, label="Domains", group="Algorithm", show_if={"algorithm": "adarnn"})
nn_states = q.param("nn_states", 3, min=1, max=20, label="States", group="Algorithm", show_if={"algorithm": "tra"})
nn_steps = q.param("nn_steps", 3, min=1, max=20, label="Weight steps", group="Algorithm", show_if={"algorithm": "tcts"})
engine_params = q.param("engine_params", "", label="Engine parameters", group="Algorithm")
seed = q.param("seed", 42, min=0, label="Seed", group="Algorithm")
reproducible = q.param("reproducible", True, label="Reproducible", group="Algorithm", help="Same inputs give the same model on any number of threads; a little slower")

NN_COMMON = {"epochs": nn_epochs, "stop": nn_stop, "optimizer": nn_optimizer, "batch": nn_batch, "lr": nn_lr, "dropout": nn_dropout, "layers": nn_layers,
             "width": nn_width, "lookback": nn_lookback, "device": nn_device}
PARAMS = {
    "lightgbm": {"lr": lgb_lr, "leaves": lgb_leaves, "depth": lgb_depth, "ff": lgb_ff, "bf": lgb_bf, "l1": lgb_l1, "l2": lgb_l2, "minleaf": lgb_minleaf,
                 "rounds": lgb_rounds, "stop": lgb_stop},
    "xgboost": {"lr": xgb_lr, "depth": xgb_depth, "ff": xgb_ff, "bf": xgb_bf, "l1": xgb_l1, "l2": xgb_l2, "minleaf": xgb_minleaf, "rounds": xgb_rounds, "stop": xgb_stop},
    "catboost": {"lr": cat_lr, "depth": cat_depth, "l2": cat_l2, "rounds": cat_rounds, "stop": cat_stop},
    "linear": {}, "ridge": {"alpha": ridge_alpha}, "lasso": {"alpha": lasso_alpha}, "nnls": {},
    **{name: {"epochs": nn_epochs, "stop": nn_stop, "optimizer": nn_optimizer, "batch": nn_batch, "lr": nn_lr, "dropout": nn_dropout, "layers": nn_layers,
             "width": nn_width, "lookback": nn_lookback, "heads": nn_heads, "pretrain": nn_pretrain, "device": nn_device} for name in ["mlp", "lstm", "gru", "alstm", "transformer", "localformer", "tcn", "tabnet", "sfm", "krnn", "sandwich", "gats", "igmtf", "add", "adarnn", "tra", "tcts", "hist"]},
    "add": {**NN_COMMON, "metric": nn_metric, "gamma": nn_gamma}, "adarnn": {**NN_COMMON, "metric": nn_metric, "domains": nn_domains},
    "tra": {k: v for k, v in NN_COMMON.items() if k not in ("optimizer", "dropout", "layers")} | {"states": nn_states},
    "tcts": {**NN_COMMON, "steps": nn_steps},
    "double_ensemble": {"lr": dens_lr, "leaves": dens_leaves, "models": dens_models, "rounds": dens_rounds},
}
for _name in ("gats", "igmtf", "add", "hist"):
    PARAMS[_name] = {**PARAMS[_name], "base": nn_base}
PARAMS["hist"] = {**PARAMS["hist"], "concepts": nn_concepts}

data = q.model_data(
    inputs,
    leave_out={"halted": leave_out_halted, "young": leave_out_young, "illiquid": leave_out_illiquid, "minDollar": min_dollar},
    processing={"inputs": {"missing": inputs_missing, "extremes": inputs_extremes, "scale": inputs_scale,
                           "takeOut": [name for name, on in (("industry", take_out_industry), ("size", take_out_size)) if on]},
                "label": {"missing": label_missing, "extremes": label_extremes, "scale": label_scale}},
    label={"formula": label, "objective": objective, "objectiveParam": objective_param, "weight": weight, "weightFormula": weight_formula,
           "aux": label_aux if algorithm == "tcts" else None},
    split={"trainMonths": train_months, "mode": window_mode, "validMonths": valid_months, "everyMonths": every_months, "firstTest": first_test},
)
for window in data.windows():
    model = q.fit(algorithm, window, PARAMS.get(algorithm, {}), seed=seed, reproducible=reproducible)
    q.record(window, model)
