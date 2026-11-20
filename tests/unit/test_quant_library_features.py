"""Live factors enter training features at availability, never retroactively at their original label."""
import pandas as pd

from modules.quant.native.library_features import at_closes


def test_delayed_factors_are_carried_to_known_closes_without_backdating():
    bars = pd.DataFrame({'column': ['A'] * 4, 'label': [100, 200, 300, 400], 'closeAt': [200, 300, 400, 500], 'knownAt': [200, 300, 400, 500]})
    rows = pd.DataFrame([
        {'column': 'A', 'name': 'F', 'field': 'score', 'datetime': 100, 'availableAt': 200, 'value': 1.0},
        {'column': 'A', 'name': 'F', 'field': 'score', 'datetime': 200, 'availableAt': 450, 'value': 2.0},
        {'column': 'A', 'name': 'G', 'field': 'score', 'datetime': 100, 'availableAt': 400, 'value': 7.0},
    ])
    frame = at_closes(rows, bars)
    assert frame['F:score'].tolist() == [1, 1, 1, 2]
    assert frame['G:score'].iloc[:2].isna().all()
    assert frame['G:score'].iloc[2:].tolist() == [7, 7]


def test_catchup_uses_newest_label_and_keeps_instruments_separate():
    bars = pd.DataFrame({'column': ['A', 'B'], 'label': [400, 400], 'closeAt': [500, 500], 'knownAt': [500, 500]})
    rows = pd.DataFrame([
        {'column': 'A', 'name': 'F', 'field': 'score', 'datetime': 200, 'availableAt': 450, 'value': 2.0},
        {'column': 'A', 'name': 'F', 'field': 'score', 'datetime': 100, 'availableAt': 450, 'value': 1.0},
        {'column': 'B', 'name': 'F', 'field': 'score', 'datetime': 300, 'availableAt': 450, 'value': 3.0},
    ])
    frame = at_closes(rows, bars)
    assert frame.loc[(400, 'A'), 'F:score'] == 2
    assert frame.loc[(400, 'B'), 'F:score'] == 3


def test_source_publish_delay_uses_the_bar_availability_clock():
    bars = pd.DataFrame({"column": ["A", "A"], "label": [100, 200], "closeAt": [200, 300], "knownAt": [215, 315]})
    rows = pd.DataFrame([
        {"column": "A", "name": "F", "field": "score", "datetime": 100, "availableAt": 205, "value": 1.},
        {"column": "A", "name": "F", "field": "score", "datetime": 200, "availableAt": 316, "value": 2.},
    ])
    frame = at_closes(rows, bars)
    assert frame["F:score"].tolist() == [1., 1.]
    assert at_closes(rows, bars, known_by=320)["F:score"].tolist() == [1., 2.]
    assert bars["closeAt"].tolist() == [200, 300]


def test_qlib_freeze_waits_until_the_last_consumed_observation_is_known(tmp_path, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from modules.quant.native import model_publish

    # Loading this clock boundary needs no Qlib training dependency; training is never invoked.
    monkeypatch.setitem(sys.modules, "modules.quant.native.models", SimpleNamespace(checked_task_train=None))
    path = Path(model_publish.__file__).with_name("qlib_helpers.py")
    spec = importlib.util.spec_from_file_location("modules.quant.native._clock_helpers", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper._state.update(rolling={}, source=SimpleNamespace(bars=pd.DataFrame({"label": [0, 0, 1, 2],
        "closeAt": [100, 100, 200, 300], "knownAt": [110, 115, 215, 315]})),
        calendar=pd.date_range("2024-01-02", periods=3), provider=tmp_path)
    monkeypatch.setattr(model_publish, "archive_qlib", lambda *args, **kwargs: kwargs)
    task = {"dataset": {"kwargs": {"segments": {"train": ["2024-01-02", "2024-01-02"], "valid": ["2024-01-03", "2024-01-03"]}, "handler": {}}},
            "talyx": {"labelLookahead": 1}}
    result = helper.freeze_model(None, "model", SimpleNamespace(id="r"), task)
    assert result["training_cutoff"] == 315
