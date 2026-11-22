"""Qlib on a research run's bars: provider, MLflow experiment store inside the run's output, a ready task and rolling retraining."""
from __future__ import annotations

import copy
import re

import pandas as pd

from .models import checked_task_train
from .provider import write_provider

_state = {"count": 0}
WARMUP = 60  # Alpha158's longest rolling window, in bars: features before it are empty


def frequency_of(timeframe):
    """Qlib's name for a bar period: daily bars are `day`, shorter ones are minutes."""
    step, unit = int(timeframe[:-1]), timeframe[-1]
    if unit == "d":
        if step != 1:
            raise ValueError("Qlib models take 1d or intraday bars.")
        return "day"
    return f"{step * (60 if unit == 'h' else 1)}min"


def init(invocation, source):
    """Write the bars as a Qlib provider and point Qlib's experiments at a store in this run's output."""
    from pathlib import Path

    import qlib
    # Each call gets its own folder: a values-mode worker runs the code once per cutoff in one process.
    output = Path(invocation["output"]) / ("qlib-" + str(_state["count"] + 1))
    output.mkdir()
    frequency = frequency_of(source.timeframe)
    format_ = "%Y-%m-%d" if frequency == "day" else "%Y-%m-%d %H:%M:%S"
    frames, names = {}, {}
    for column, frame in source.bars.groupby("column"):
        index = pd.DatetimeIndex(pd.to_datetime(frame["label"], unit="ms", utc=True)).tz_localize(None)
        table = pd.DataFrame({field: frame[field].to_numpy() for field in ("open", "high", "low", "close", "volume")}, index=index)
        table["factor"] = 1.0
        # Qlib keeps one folder per instrument, so its name cannot hold the characters of BTC/USDT; the table's own names are restored afterwards.
        safe = re.sub(r'[<>:"/\\|?*]', "_", column)
        frames[safe], names[safe] = table, column
    provider = output / "provider"
    write_provider(frames, provider, {"frequency": frequency})
    store = output / "mlflow"
    store.mkdir()
    _state.update(count=_state["count"] + 1, names=names, output=output, provider=provider, source=source, rolling={}, uri="sqlite:///" + (store / "mlflow.db").as_posix(), artifacts=store / "artifacts", frequency=frequency, format=format_, annualization=source.annualization,
                  calendar=pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in frames.values())))), assets=list(frames))
    qlib.init(provider_uri=str(provider), region="us", kernels=1, exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
              "kwargs": {"uri": _state["uri"], "default_exp_name": "main"}})
    return provider


def original_names(table):
    """A table of Qlib instrument names by anything, with the names of the research bars restored."""
    return table.rename(columns=_state["names"])


def library_lightgbm(invocation, factors, *, rounds, train_bars, valid_bars, step, gap):
    """Use selected immutable factor versions as features; future labels stay outside fitting windows."""
    from pathlib import Path

    if not factors or any(not name for name in factors):
        raise ValueError('Select at least one training factor from the library.')
    if gap < 2:
        raise ValueError('The next-bar return label needs at least two observations between fitting windows.')
    research = invocation['research']
    frozen = research['factors']
    retained = {item['name'] for item in frozen['items']}
    if not set(factors) <= retained:
        raise ValueError('Every training factor must be selected by a declared library parameter.')
    rows = pd.read_parquet(frozen['path'])
    source = _state['source']
    bars = source.bars
    if invocation.get('cutoff') is not None:
        rows = rows[rows['datetime'] <= invocation['cutoff']]
    names = {item['instrumentId']: item['column'] for item in source.instruments}
    rows = rows[rows['name'].isin(factors)].assign(column=lambda t: t['instrument'].map(names))
    from .library_features import at_closes
    features = at_closes(rows, bars, frozen.get('knownBy'))
    safe_names = {original: safe for safe, original in _state['names'].items()}
    dates = pd.to_datetime(features.index.get_level_values(0), unit='ms', utc=True).tz_localize(None)
    features.index = pd.MultiIndex.from_arrays([dates, features.index.get_level_values(1).map(safe_names)], names=['datetime', 'instrument'])
    close = source.close.rename(columns=safe_names)
    close.index = close.index.tz_localize(None)
    target = (close.shift(-2) / close.shift(-1) - 1).stack(future_stack=True).to_frame('LABEL0')
    target.index.names = ['datetime', 'instrument']
    output = Path(_state['output'])
    feature_path, label_path = output / 'library-features.pkl', output / 'library-labels.pkl'
    features.to_pickle(feature_path)
    target.to_pickle(label_path)
    # Reuse the window layout, replacing the complete Alpha158 handler before
    # any task is instantiated. The only features are the frozen library rows.
    calendar = _state['calendar']
    needed = train_bars + valid_bars + 2 * gap + 1
    if len(calendar) < needed:
        raise ValueError(f'This training range has {len(calendar)} bars; at least {needed} are needed.')
    day = lambda at: calendar[at].strftime(_state['format'])
    valid_first, test_first = train_bars + gap, train_bars + valid_bars + 2 * gap
    segments = {'train': [day(0), day(train_bars - 1)], 'valid': [day(valid_first), day(valid_first + valid_bars - 1)],
                'test': [day(test_first), day(len(calendar) - 1)]}
    return {'model': {'class': 'LGBModel', 'module_path': 'qlib.contrib.model.gbdt',
                     'kwargs': {'num_boost_round': rounds, 'num_threads': 1, 'early_stopping_rounds': 50}},
            'dataset': {'class': 'DatasetH', 'module_path': 'qlib.data.dataset', 'kwargs': {'segments': segments,
                'handler': {'class': 'DataHandlerLP', 'module_path': 'qlib.data.dataset.handler', 'kwargs': {
                    'data_loader': {'class': 'StaticDataLoader', 'module_path': 'qlib.data.dataset.loader',
                        'kwargs': {'config': {'feature': str(feature_path), 'label': str(label_path)}}},
                    'learn_processors': [{'class': 'DropnaLabel'}], 'infer_processors': []}}}},
            'record': [{'class': 'SignalRecord', 'module_path': 'qlib.workflow.record_temp'},
                       {'class': 'SigAnaRecord', 'module_path': 'qlib.workflow.record_temp',
                        'kwargs': {'ana_long_short': False, 'ann_scaler': _state['annualization']}}],
            'talyx': {'libraryFeatures': list(features.columns), 'labelLookahead': 2, 'factors': frozen['items']}}


def freeze_model(publisher, name, recorder, task):
    """Archive the actual last fitted window, not the rolling prediction summary."""
    from .model_publish import archive_qlib, qlib_label_lookahead, qlib_training_cutoff
    if recorder.id in _state['rolling']:
        recorder, task = _state['rolling'][recorder.id]
    available = _state['source'].bars.groupby('label')['knownAt'].max().sort_index().tolist()
    lookahead = task['talyx']['labelLookahead'] if 'talyx' in task else qlib_label_lookahead(task)
    cutoff = qlib_training_cutoff(task, _state['calendar'], available, label_lookahead=lookahead)
    return archive_qlib(publisher, name, recorder, task, training_cutoff=cutoff,
                        providers={'default': _state['provider']}, qlib_init={'provider_uri': str(_state['provider']), 'region': 'us'})


def experiment(name):
    """The experiment is made here so its artifacts live in the run's output, not under the working directory."""
    from mlflow.tracking import MlflowClient
    client = MlflowClient(tracking_uri=_state["uri"])
    if client.get_experiment_by_name(name) is None:
        client.create_experiment(name, artifact_location=(_state["artifacts"] / name).as_uri())
    return name


def alpha158_lightgbm(source, *, rounds, train_bars, valid_bars, step, label, gap):
    """A plain Qlib task: Alpha158 features, a LightGBM model, the first train and valid windows and a test range to the last bar (rolling cuts it into windows); edit it freely."""
    calendar = _state["calendar"]
    needed = WARMUP + train_bars + valid_bars + step + 2 * gap
    if len(calendar) < needed:
        raise ValueError(f"The data has {len(calendar)} bars; this task needs at least {needed} ({WARMUP} for Alpha158's longest window, train {train_bars}, valid {valid_bars}, test {step}, {gap} bars between windows for the label's look-ahead).")
    day = lambda position: calendar[position].strftime(_state["format"])
    first = WARMUP
    valid_first = first + train_bars + gap
    test_first = valid_first + valid_bars + gap
    segments = {"train": [day(first), day(first + train_bars - 1)], "valid": [day(valid_first), day(valid_first + valid_bars - 1)],
                "test": [day(test_first), day(len(calendar) - 1)]}
    return {"model": {"class": "LGBModel", "module_path": "qlib.contrib.model.gbdt", "kwargs": {"num_boost_round": rounds, "num_threads": 1, "early_stopping_rounds": 50}},
            "dataset": {"class": "DatasetH", "module_path": "qlib.data.dataset", "kwargs": {
                "handler": {"class": "Alpha158", "module_path": "qlib.contrib.data.handler", "kwargs": {
                    "start_time": day(0), "end_time": day(len(calendar) - 1), "fit_start_time": segments["train"][0], "fit_end_time": segments["train"][1],
                    "instruments": _state["assets"], "freq": _state["frequency"], "label": [label]}},
                "segments": segments}},
            "record": [{"class": "SignalRecord", "module_path": "qlib.workflow.record_temp"},
                       {"class": "SigAnaRecord", "module_path": "qlib.workflow.record_temp", "kwargs": {"ana_long_short": False, "ann_scaler": _state["annualization"]}}]}


def rolling_tasks(task, step):
    """One task per window of `step` bars: the training window grows, the validation window moves, the test window is the next `step` bars (the last may be shorter).

    Qlib's RollingGen drops that last window and fails when the data does not divide into whole windows, so the windows are laid out here from the same
    fixed first windows: the same windows come out whatever later bars exist, which is what lets an update reproduce published predictions."""
    calendar, text = _state["calendar"], _state["format"]
    segments = task["dataset"]["kwargs"]["segments"]
    at = {name: [calendar.get_loc(pd.Timestamp(value)) for value in segments[name]] for name in ("train", "valid", "test")}
    last, tasks, shift = len(calendar) - 1, [], 0
    while at["test"][0] + shift <= last:
        window = copy.deepcopy(task)
        first = at["test"][0] + shift
        window["dataset"]["kwargs"]["segments"] = {
            "train": [segments["train"][0], calendar[at["train"][1] + shift].strftime(text)],
            "valid": [calendar[at["valid"][0] + shift].strftime(text), calendar[at["valid"][1] + shift].strftime(text)],
            "test": [calendar[first].strftime(text), calendar[min(first + step - 1, last)].strftime(text)]}
        tasks.append(window)
        shift += step
    return tasks


def train(invocation, task):
    from qlib.model.trainer import TrainerR
    recorder = TrainerR(experiment_name=experiment("main"), train_func=checked_task_train)([task])[0]
    return recorder


def train_rolling(invocation, task, *, step):
    """Windows of `step` bars: each is trained on everything before it (the training window grows) and predicts only itself; the windows' predictions form one summary Recorder."""
    from qlib.model.ens.ensemble import RollingEnsemble
    from qlib.model.trainer import TrainerR
    from qlib.utils import get_cls_kwargs, init_instance_by_config
    from qlib.workflow import R
    from qlib.workflow.record_temp import SignalRecord
    from qlib.workflow.task.collect import RecorderCollector
    windows = "windows"
    tasks = rolling_tasks(task, step)
    recorders = TrainerR(experiment_name=experiment(windows), train_func=checked_task_train)(tasks)
    collected = RecorderCollector(experiment=windows, artifacts_key=["pred", "label"], artifacts_path={"pred": "pred.pkl", "label": "label.pkl"},  # i18n: ignore
                                  process_list=[RollingEnsemble()])()
    with R.start(experiment_name=experiment("main"), recorder_name="summary"):
        R.log_params(window_experiment=windows)
        R.save_objects(**{"pred.pkl": collected["pred"], "label.pkl": collected["label"], "task": task})
        summary = R.get_recorder()
        for record in task.get("record", []):
            if issubclass(get_cls_kwargs(record)[0], SignalRecord):
                continue
            init_instance_by_config(record, recorder=summary, default_module="qlib.workflow.record_temp").generate()
    _state['rolling'][summary.id] = recorders[-1], tasks[-1]
    return summary
