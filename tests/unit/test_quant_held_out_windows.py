"""The held-out confirm and the run count the same windows: the walk-forward layout of the data beyond the windows the source run already has."""
from types import SimpleNamespace

import pandas as pd

from modules.quant.backend.model_runs import ModelRuns


def test_held_out_windows_are_the_layout_beyond_the_source_windows(tmp_path):
    days = pd.bdate_range("2023-01-02", "2026-11-03", tz="UTC")
    labels = [int(day.value // 1_000_000) for day in days]
    folder = tmp_path / "key"
    folder.mkdir()
    pd.DataFrame({"label": labels * 2}).to_parquet(folder / "bars.parquet")
    runs = object.__new__(ModelRuns)
    runs.data = SimpleNamespace(directory=lambda key: tmp_path / key)
    first_test = int(pd.Timestamp("2023-07-03", tz="UTC").value // 1_000_000)
    source = {"data": {"key": "key"}, "settings": {"params": {"every_months": 6, "train_months": 3, "valid_months": 1}}, "reach": {"ahead": 6},
              "windows": [{"k": 1, "test": {"from": first_test, "to": first_test}}, {"k": 2, "test": {"from": first_test, "to": first_test}}]}
    total = 7  # 6-month windows from Jul 2023 to Nov 2026
    assert runs.held_out_windows(source) == total - 2
    assert runs.held_out_windows({**source, "windows": source["windows"][:1]}) == total - 1


def test_a_held_out_run_counts_only_the_windows_it_retrains():
    runs = object.__new__(ModelRuns)
    saved = {}
    records = {"held": {"id": "held", "runKind": "heldout", "of": "src", "windows": [], "progress": {}, "windowsTotal": None, "number": 2},
               "src": {"id": "src", "windows": [{"k": 1}, {"k": 2}, {"k": 3}]}}
    runs.read = lambda identity: records[identity]
    runs.save = lambda record, **changes: saved.update(changes) or record
    runs.window_spans = lambda identity: {}
    runs.follow("held", [{"stage": "inputs", "state": "done", "windows": 7}, {"stage": "window", "state": "running", "window": 5, "windows": 7, "fraction": 0.0}])
    assert saved["windowsTotal"] == 4 and saved["progress"]["window"] == 2 and saved["progress"]["windows"] == 4
