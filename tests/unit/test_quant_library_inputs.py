"""A strategy's library item reaches the backtest as a file of values, each stamped with when it became available."""
import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import library_inputs
from modules.quant.backend.library import Library
from tests.unit.quant_runtime_fakes import adapt

DAY = 86_400_000
BTC = "vi1_" + "a" * 64
ETH = "vi1_" + "b" * 64
SOURCES = [{"marketInstrument": {"instrumentId": BTC}, "nativeInstrument": "BTCUSDT.OKX"}, {"marketInstrument": {"instrumentId": ETH}, "nativeInstrument": "ETHUSDT.OKX"}]


def library(tmp_path, rows, *, kind="factor", state="updating", version=1):
    records = SimpleNamespace(root=tmp_path)
    store = Library(adapt(SimpleNamespace(track=lambda release: None)), records)
    spec = {"name": "Momentum", "kind": kind, "meta": {"fields": ["factor"], "timeframe": "1d"}, "rows": rows, "objectId": "o", "runId": "r", "workspaceId": "ws",
            "state": state, "cursor": 3 * DAY, "checks": {}, "versionId": f"v{version}"}
    store.store.commit_versions([spec])
    return store


def prepare(store, items=(("signal", "Momentum"),), since=DAY, until=10 * DAY, path=None):
    return asyncio.run(library_inputs.prepare(store, list(items), SOURCES, since, until, "1d", path))


def test_values_of_the_runs_symbols_arrive_when_they_became_available_not_when_they_are_about(tmp_path):
    rows = [(BTC, "factor", 1 * DAY, 0.5, 2 * DAY, 9 * DAY, "history"), (ETH, "factor", 1 * DAY, 0.7, 2 * DAY + 3_600_000, 9 * DAY, "catch_up"),
            ("vi1_" + "c" * 64, "factor", 1 * DAY, 9.0, 2 * DAY, 9 * DAY, "history"), (BTC, "factor", 2 * DAY, 0.9, 3 * DAY, 9 * DAY, "history"),
            (BTC, "factor", 20 * DAY, 1.0, 21 * DAY, 9 * DAY, "history")]
    store = library(tmp_path, rows)
    path = tmp_path / "values.parquet"
    result = prepare(store, path=path)
    frame = pd.read_parquet(path)
    assert list(frame["instrument"]) == ["BTCUSDT.OKX", "ETHUSDT.OKX", "BTCUSDT.OKX"] and list(frame["value"]) == [0.5, 0.7, 0.9]
    assert list(frame["ts_init"]) == [2 * DAY * 1_000_000, (2 * DAY + 3_600_000) * 1_000_000, 3 * DAY * 1_000_000]
    assert list(frame["ts_event"]) == [1 * DAY * 1_000_000, 1 * DAY * 1_000_000, 2 * DAY * 1_000_000] and set(frame["version"]) == {1} and set(frame["name"]) == {"Momentum"}
    item, = result["items"]
    assert (item["parameter"], item["name"], item["version"], item["rows"], item["instruments"], item["fields"]) == ("signal", "Momentum", 1, 3, 2, ["factor"])
    assert (item["firstAvailableAt"], item["lastAvailableAt"]) == (2 * DAY, 3 * DAY)


def test_a_value_dated_after_it_became_available_is_about_the_moment_it_could_first_be_known(tmp_path):
    store = library(tmp_path, [(BTC, "factor", 5 * DAY, 0.5, 3 * DAY, 9 * DAY, "history")])
    path = tmp_path / "values.parquet"
    prepare(store, path=path)
    assert list(pd.read_parquet(path)["ts_event"]) == [3 * DAY * 1_000_000]


def test_no_chosen_item_means_nothing_is_read_and_a_missing_one_is_an_error(tmp_path):
    store = library(tmp_path, [(BTC, "factor", DAY, 0.5, 2 * DAY, 9, "history")])
    assert prepare(store, items=[]) == {"path": None, "items": []}
    with pytest.raises(ContractError, match="nothing named Other"):
        prepare(store, items=[("signal", "Other")], path=tmp_path / "x.parquet")


def test_values_outside_the_run_or_for_other_symbols_are_reported_as_no_values(tmp_path):
    store = library(tmp_path, [(BTC, "factor", 30 * DAY, 0.5, 31 * DAY, 9, "history"), ("vi1_" + "c" * 64, "factor", DAY, 1.0, 2 * DAY, 9, "history")])
    with pytest.raises(ContractError) as error:
        prepare(store, path=tmp_path / "x.parquet")
    assert error.value.code == "unavailable"
    assert str(error.value) == "Momentum v1 has values for BTCUSDT.OKX from Feb 1, 1970 to Feb 1, 1970, none between Jan 2, 1970 and Jan 11, 1970."
    other = library(tmp_path / "other", [("vi1_" + "c" * 64, "factor", DAY, 1.0, 2 * DAY, 9, "history")])
    with pytest.raises(ContractError, match=r"^Momentum v1 has no values for BTCUSDT.OKX, ETHUSDT.OKX; its 1 names are other instruments.$"):
        prepare(other, path=tmp_path / "y.parquet")


def test_data_put_in_from_a_notebook_is_read_by_the_symbols_it_was_written_with(tmp_path):
    # q.library.publish keeps a notebook's instrument column as written (600642.XSHG); a strategy trading that name must read it, not say it has no values.
    sources = [{"marketInstrument": {"instrumentId": BTC, "displaySymbol": "600642.XSHG"}, "nativeInstrument": "600642.XSHG"},
               {"marketInstrument": {"instrumentId": ETH, "symbol": "AAPL"}, "nativeInstrument": "AAPL.YF"}]
    store = library(tmp_path, [("600642.XSHG", "mom20", DAY, 0.5, 2 * DAY, 9, "history"), ("AAPL", "mom20", DAY, 0.7, 2 * DAY, 9, "history"),
                               ("600519.XSHG", "mom20", DAY, 0.9, 2 * DAY, 9, "history")])
    path = tmp_path / "values.parquet"
    result = asyncio.run(library_inputs.prepare(store, [("signal", "Momentum")], sources, DAY, 10 * DAY, "1d", path))
    frame = pd.read_parquet(path)
    assert sorted(zip(frame["instrument"], frame["value"])) == [("600642.XSHG", 0.5), ("AAPL.YF", 0.7)] and result["items"][0]["rows"] == 2
    with pytest.raises(ContractError, match=r"^Momentum v1 has no values for SPY; its values are for 600519.XSHG, 600642.XSHG, AAPL.$"):
        asyncio.run(library_inputs.prepare(store, [("signal", "Momentum")], [{"marketInstrument": {"instrumentId": "vi1_" + "d" * 64, "displaySymbol": "SPY"},
                                                                              "nativeInstrument": "SPY.YF"}], DAY, 10 * DAY, "1d", tmp_path / "z.parquet"))


def test_a_model_without_predictions_and_an_archived_version_cannot_be_read_but_a_stopped_one_still_can(tmp_path):
    model = library(tmp_path / "a", [], kind="model", state="fixed")
    model.store.commit_versions([{"name": "Bare", "kind": "model", "versionId": "m1", "meta": {"fields": []}, "rows": [], "objectId": "o", "runId": "r",
                                  "workspaceId": "ws", "state": "fixed", "cursor": None, "checks": {}}])
    with pytest.raises(ContractError, match="holds no values"):
        prepare(model, items=[("signal", "Bare")], path=tmp_path / "x.parquet")
    stopped = library(tmp_path / "b", [(BTC, "factor", DAY, 0.5, 2 * DAY, 9, "history")])
    stopped.store.stop("v1")
    assert prepare(stopped, path=tmp_path / "y.parquet")["items"][0]["rows"] == 1
    stopped.store.archive("v1")
    with pytest.raises(ContractError, match="archived") as error:
        prepare(stopped, path=tmp_path / "x.parquet")
    assert error.value.code == "not_found"


def test_the_items_come_from_the_library_parameters_the_panel_filled_in():
    definitions = [{"name": "signal", "type": "library"}, {"name": "top", "type": "int"}, {"name": "second", "type": "library"}]
    assert library_inputs.named_items(definitions, {"signal": "Momentum", "top": 5, "second": ""}) == [("signal", "Momentum")]


def test_a_models_one_version_holds_its_predictions_and_is_read_by_either_name_of_its_kind(tmp_path):
    store = library(tmp_path, [(BTC, 'factor', DAY, .5, 2 * DAY, 9, 'history')], kind='model')
    prepared = prepare(store, path=tmp_path / 'values.parquet')
    assert prepared['items'][0]['versionId'] == 'v1'
    assert store.resolve('Momentum', kind='prediction')['versionId'] == store.resolve('Momentum', kind='model')['versionId'] == 'v1'
