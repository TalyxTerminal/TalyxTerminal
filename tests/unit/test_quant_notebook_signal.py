import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest
from talyx_sdk import ContractError

import modules.quant.backend.notebook_routes as routes
from modules.quant.backend import library_inputs
from modules.quant.backend.notebook_routes import NotebookServices


def services(library_params):
    item = NotebookServices.__new__(NotebookServices)
    started = []

    async def saved_source(strategy):
        return "src"

    async def start(identity, args):
        started.append(args)
        return {"id": "r1", "number": 4}
    item.runs = SimpleNamespace(saved_source=saved_source, start=start)
    item.store = SimpleNamespace(parameters=lambda source: {"parameters": [{"name": name, "type": "library"} for name in library_params]})
    return item, started


@pytest.fixture(autouse=True)
def strategy(monkeypatch):
    class Materials:
        def __init__(self, *_):
            pass

        def objects_named(self, *_args, **_kw):
            return {"id": "s1", "name": "Reversal long-short"}
    monkeypatch.setattr(routes, "RunMaterials", Materials)


def test_backtest_ambiguous_signal():
    item, started = services(["signal", "filter"])
    with pytest.raises(ContractError) as caught:
        asyncio.run(item.start_backtest({"strategy": "Reversal long-short", "signal": {"param": None, "path": "x.parquet"}}))
    assert "reads 2 library items (signal, filter)" in caught.value.message and 'signal="signal"' in caught.value.message and started == []


def test_signal_replaces_the_one_library_input_and_run_is_tagged_notebook(monkeypatch):
    from tests.unit.quant_public import service_actions
    item, started = services(["signal"])
    act = service_actions(monkeypatch, routes.handle_notebook_actions, item)
    assert asyncio.run(act("start_notebook_backtest", {"strategy":"Reversal long-short","signal":{"param":None,"path":"x.parquet"}})) == {"runId":"r1","number":4}
    assert started[0]["signalOverride"] == {"param": "signal", "path": "x.parquet"} and started[0]["tags"] == ["notebook"]
    with pytest.raises(ContractError):
        asyncio.run(item.start_backtest({"strategy": "x", "signal": {"param": "nope", "path": "x.parquet"}}))


def calendar_prices(directory, name):
    path = directory / "prices.parquet"
    dates = pd.date_range("2024-01-02", periods=2, tz="UTC").as_unit("ms").astype("int64")
    pd.DataFrame({"label": dates, "instrument": name, "knownAt": dates + 86_400_000}).to_parquet(path)
    return path


def test_replacement_values_arrive_when_their_bar_closes(tmp_path):
    path = tmp_path / "s.parquet"
    pd.DataFrame({"datetime": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-02"], utc=True), "instrument": ["m1", "m1", "other"], "value": [1.0, 2.0, 9.0]}).to_parquet(path)
    got = library_inputs.replacement(str(path), {"m1": "NATIVE.1"}, "score", "Reversal", calendar_prices(tmp_path, "NATIVE.1"))
    frame = got["frame"]
    assert list(frame["instrument"]) == ["NATIVE.1", "NATIVE.1"] and list(frame["field"].unique()) == ["score"]
    assert (frame["ts_init"] - frame["ts_event"]).tolist() == [86_400_000 * 10**6] * 2
    with pytest.raises(ContractError):
        library_inputs.replacement(str(path), {"zzz": "Z"}, "score", "Reversal", calendar_prices(tmp_path, "NATIVE.1"))


def test_a_notebook_may_name_instruments_by_their_displayed_symbol(tmp_path):
    path = tmp_path / "s.parquet"
    pd.DataFrame({"datetime": pd.to_datetime(["2024-01-02"], utc=True), "instrument": ["600642.XSHG"], "value": [1.0]}).to_parquet(path)
    sources = [{"marketInstrument": {"instrumentId": "vi1_x", "displaySymbol": "600642.XSHG", "symbol": "600642"}, "nativeInstrument": "NATIVE.1"}]
    names = {symbol: source["nativeInstrument"] for source in sources for symbol in (source["marketInstrument"].get("displaySymbol"), source["marketInstrument"].get("symbol")) if symbol}
    got = library_inputs.replacement(str(path), {**names, "vi1_x": "NATIVE.1"}, "score", "Reversal", calendar_prices(tmp_path, "NATIVE.1"), 3)
    assert list(got["frame"]["instrument"]) == ["NATIVE.1"] and got["frame"]["version"].tolist() == [3]


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_replacement_times_do_not_depend_on_the_resolution_of_the_notebooks_datetimes(tmp_path, unit):
    path = tmp_path / "s.parquet"
    stamps = pd.DatetimeIndex(["2024-01-02", "2024-01-03"], tz="UTC").as_unit(unit)
    pd.DataFrame({"datetime": stamps, "instrument": ["m1", "m1"], "value": [1.0, 2.0]}).to_parquet(path)
    frame = library_inputs.replacement(str(path), {"m1": "NATIVE.1"}, "score", "Reversal", calendar_prices(tmp_path, "NATIVE.1"))["frame"]
    first = int(pd.Timestamp("2024-01-02", tz="UTC").timestamp()) * 10**9
    assert frame["ts_event"].tolist()[0] == first and frame["ts_init"].tolist()[0] == first + 86_400_000 * 10**6


def test_a_table_read_from_the_library_keeps_its_millisecond_datetimes(tmp_path):
    path = tmp_path / "s.parquet"
    first = int(pd.Timestamp("2024-01-02", tz="UTC").timestamp()) * 1000
    pd.DataFrame({"datetime": [first, first + 86_400_000], "instrument": ["m1", "m1"], "value": [1.0, 2.0]}).to_parquet(path)
    frame = library_inputs.replacement(str(path), {"m1": "NATIVE.1"}, "score", "Reversal", calendar_prices(tmp_path, "NATIVE.1"))["frame"]
    assert frame["ts_event"].tolist()[0] == first * 10**6
