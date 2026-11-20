from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from modules.quant import talyx_quant as q
from modules.quant.backend import library_inputs
from tests.unit.test_quant_preset_quantities import preset

DAY = 86_400_000


def test_notebook_library_signal_keeps_actual_publication_time(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    date = pd.Timestamp("2025-01-02", tz="UTC")
    published = pd.Timestamp("2025-01-10", tz="UTC")
    data = pd.DataFrame({"date": [date], "instrumentId": ["A"], "value": [3], "availableAt": [published]})
    path = Path(q._signal_file(data))
    frame = library_inputs.replacement(path, {"A": "A.X"}, "value", "factor", DAY)["frame"]
    assert frame["ts_event"].tolist() == [date.value]
    assert frame["ts_init"].tolist() == [published.value]


def test_ranked_preset_caps_two_technology_stocks_at_thirty_percent(tmp_path, monkeypatch):
    strategy, _ = preset(monkeypatch, "ranked", 1)
    scope = strategy.sectors.__globals__
    scope["q"] = q
    monkeypatch.setattr(q, "_strategy", SimpleNamespace(clock=SimpleNamespace(timestamp_ns=lambda: DAY * 10**6)))
    paths = {}
    for name in ("A", "B"):
        path = tmp_path / f"{name}.parquet"
        pd.DataFrame({"time": [0, 2 * DAY], "value": ["Technology", "Finance"]}).to_parquet(path)
        paths[name] = str(path)
    monkeypatch.setattr(q, "_extras", {"industry": paths})
    monkeypatch.setattr(q, "_extra_frames", {})
    strategy.by_name = {"A": "A", "B": "B"}
    sectors = strategy.sectors(["A", "B"])
    weights = scope["fit"]({"A": 1, "B": 1}, .95, group_of=sectors, group_cap=.30)
    assert weights == pytest.approx({"A": .15, "B": .15})


def test_signal_without_publication_time_uses_frozen_early_close(tmp_path):
    day = pd.Timestamp("2024-11-29", tz="UTC")
    close = day + pd.Timedelta(hours=18)  # US early close, 13:00 New York
    prices, signal = tmp_path / "prices.parquet", tmp_path / "signal.parquet"
    pd.DataFrame({"instrument": ["SPY.X"], "label": [day.value // 10**6], "knownAt": [close.value // 10**6]}).to_parquet(prices)
    pd.DataFrame({"instrument": ["SPY"], "datetime": [day], "value": [.8]}).to_parquet(signal)
    frame = library_inputs.replacement(signal, {"SPY": "SPY.X"}, "score", "test", prices)["frame"]
    assert frame["ts_init"].tolist() == [close.value]
