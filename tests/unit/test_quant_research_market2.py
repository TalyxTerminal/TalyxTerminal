"""Market 2.0 research invariants: raw economics, known-time shares, immutable revisions and membership pages."""
import asyncio
import pickle
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import factor_data as D
from modules.quant.backend import index_universe as U
from modules.quant.backend import research_inputs as R
from modules.quant.native.factor_work import Panel, shares_table
from tests.unit.quant_runtime_fakes import adapt


def test_amount_and_size_use_raw_prices_while_returns_keep_adjusted_prices():
    bars = pd.DataFrame({"column": ["A", "A"], "label": [0, 1], "open": [50., 51.], "high": [50., 51.],
        "low": [50., 51.], "close": [50., 51.], "volume": [2000., 2000.], "rawClose": [100., 102.], "rawVolume": [1000., 1000.]})
    panel = Panel.from_bars(bars, {"annualization": 252, "instruments": [{"column": "A"}]}, shares=np.array([[1e6], [1e6]]))
    assert panel.derived("amount")[:, 0].tolist() == [100000., 102000.]
    assert panel.derived("size")[:, 0].tolist() == [1e8, 1.02e8]
    assert panel.fields["close"][:, 0].tolist() == [50., 51.]
    assert panel.derived("returns")[1, 0] == pytest.approx(.02)


def test_shares_wait_for_announcement_and_never_fill_before_first_known_value():
    labels = np.array([0, 10, 20, 30])
    clocks = np.array([[5., 5.], [15., 12.], [25., 25.], [35., 35.]])
    values = shares_table(labels, ["A", "B"], {"A": [[14, 9e7], [30, 1e8]], "B": [[14, 9e7], [30, 1e8]]}, clocks=clocks)
    assert np.isnan(values[0]).all() and np.isnan(values[1, 1])
    assert values[1, 0] == 9e7 and values[2].tolist() == [9e7, 9e7] and values[3].tolist() == [1e8, 1e8]


def test_repeated_unmapped_count_is_not_added_across_pages():
    asked = []
    async def read(args):
        asked.append(args)
        page = len(asked)
        return {"columns":[{"name":"instrumentId"},{"name":"symbol"}], "rows":[[None,str(page)]],"meta":[], "unmapped": 30,
                "note": "Historical codes retained", "nextCursor": str(page) if page < 3 else None}
    ctx = adapt(SimpleNamespace(require=lambda name: SimpleNamespace(read=read)))
    result = asyncio.run(R.read_pages(ctx, {"kind": "members", "index": {}, "asOf": "100", "fill": "await"}))
    assert result["unmapped"] == 30 and len(result["rows"]) == 3
    assert [item["cursor"] for item in asked] == [None, "1", "2"]
    assert [item["fill"] for item in asked] == ["await", "none", "none"]
    assert all(item["asOf"] == "100" for item in asked)


def test_unmapped_members_and_mapped_members_without_prices_are_distinct():
    rows = [{"instrumentId": identity, "symbol":code,"start":"1970-01-01","end":"1970-01-01T00:00:00.009Z","knownAt":"1970-01-01"}
            for identity, code in [("id:A", "OLD"), (None, "GONE")]]
    index = U.freeze_rows({"instrument": {"instrumentId": "index"}}, 0, 20, rows,
        {"id:A": {"instrumentId": "id:A", "displaySymbol": "NEW"}}, unmapped=30)
    U.exclude(index, "id:A", 0, 9, "no_prices", source_reason="source has no rows")
    assert U.is_member(index, "id:A", 9) and not U.is_member(index, "id:A", 10)
    assert U.gaps(index, 1) == {"unmapped": 30, "worst": 1, "year": 1970, "withoutPrices": 1}
    assert {item["kind"] for item in index["exclusions"]} == {"unmapped", "no_prices"}


def test_old_index_selection_is_rejected():
    with pytest.raises(ContractError):
        U.check({"materialId": "m", "version": "v1", "poolId": "p"})


def test_same_day_new_revision_creates_new_frozen_data_and_keeps_old_prices(tmp_path, monkeypatch):
    instrument = {"instrumentId": "a", "displaySymbol": "A", "assetClass": "crypto", "calendarId": "24_7"}
    selection = {"symbols": [{"instrument": instrument}], "timeframe": "1d", "since": "2024-01-01", "until": "2024-01-02"}
    state = {"revision": "r1", "close": 100., "reads": 0}
    async def prepare(ctx, data, owner, directory, **kwargs):
        state["reads"] += 1
        path = directory / "bars.parquet"
        pd.DataFrame({"label": [0], "close": [state["close"]]}).to_parquet(path)
        info = {"since": 0, "until": 1, "timeframe": "1d", "adjusted": True, "revisions": {"series": state["revision"]},
                "instruments": [{"instrumentId": "a", "column": "A", "instrument": instrument,
                    "series": {"sessionScope": "continuous"}, "revision": state["revision"], "source": {"sourceId": "source"}}]}
        return {"research": {"barsPath": str(path), "info": info}, "references": [],
                "summary": {"requested": 1, "fetchRows": [{"instrumentId": "a", "ticker": "A", "result": "fetched"}]}}
    async def describe(ctx, instruments):
        return {"sources": [], "fields": [], "limits": []}
    monkeypatch.setattr(D.research_data, "prepare", prepare)
    monkeypatch.setattr(R, "describe", describe)
    data = D.FactorData(None, tmp_path / "data", SimpleNamespace(members=lambda name: []), None, clock=lambda: 100.)
    async def unchanged(ready):  # Market's revision reads (FactorData.unchanged, tested in test_quant_library)
        return ready["info"]["revisions"]["series"] == state["revision"]
    monkeypatch.setattr(data, "unchanged", unchanged)
    async def scenario():
        first = await data.prepare(selection)
        state.update(revision="r2", close=50.)
        second = await data.prepare(selection)
        unchanged = await data.prepare(selection)
        return first, second, unchanged
    first, second, unchanged = asyncio.run(scenario())
    old = next((tmp_path / "data").glob(first["key"] + "~*"))
    assert pd.read_parquet(old / "bars.parquet")["close"].tolist() == [100.]
    assert pd.read_parquet(second["barsPath"])["close"].tolist() == [50.]
    assert data.ready(old.name)["barsPath"] == str(old / "bars.parquet")
    assert second["frozenAt"] > first["frozenAt"]
    assert first["fingerprint"] != second["fingerprint"] and unchanged["how"] == "unchanged"
    assert state["reads"] == 2 and not list((tmp_path / "data").glob(".preparing-*"))  # the unchanged one read nothing


def test_shares_freeze_uses_announcement_time_instead_of_statistical_date(tmp_path, monkeypatch):
    def stamp(day):
        return pd.Timestamp(day, tz="UTC").value // 1000000
    previous, announcement, until = stamp("2024-01-01"), stamp("2024-04-30"), stamp("2024-05-01")
    async def describe(ctx, instruments):
        return {"fields": [{"key": "industry", "basis": None}, {"key": "shares", "basis": "shares"}]}
    async def task(ctx, name, request, progress=None):
        assert name == "market.fetch" and request["data"] == [{"kind": "shares"}]
        return {"failures": []}
    async def read(ctx, request):
        assert request["asOf"] == R.timestamp(until) and request["fill"] == "none"
        return {"coverage": [], "rows": [
            {"date": str(stamp("2023-12-31") * R.MS), "knownAt": R.timestamp(previous), "kind": "outstanding", "shares": "90000000"},
            {"date": str(stamp("2024-03-31") * R.MS), "knownAt": R.timestamp(announcement), "kind": "outstanding", "shares": "100000000"}]}
    monkeypatch.setattr(R, "describe", describe)
    monkeypatch.setattr(R, "read_pages", read)
    monkeypatch.setattr(D, "run_task", task)
    data = D.FactorData(None, tmp_path, None, None)
    rows = [{"ticker": "A"}]
    facts = asyncio.run(data._facts([{"assetClass":"equity","instrumentId":"i:A"}], rows, rows, tmp_path, 0, until, True, "1d", lambda *args: None))
    assert facts["shares"]["A"] == [(previous, 90000000.), (announcement, 100000000.)]
    values = shares_table(np.array([stamp("2023-12-31"), stamp("2024-04-29"), announcement]), ["A"], facts["shares"])
    assert np.isnan(values[0, 0]) and values[1:, 0].tolist() == [90000000., 100000000.]


@pytest.mark.parametrize("missing", ["rawClose", "rawVolume"])
def test_research_panels_require_raw_economic_inputs(missing):
    bars = pd.DataFrame({"column": ["A"], "label": [0], "open": [50.], "high": [50.], "low": [50.],
                         "close": [50.], "volume": [2000.], "rawClose": [100.], "rawVolume": [1000.]})
    with pytest.raises(KeyError, match=missing):
        Panel.from_bars(bars.drop(columns=missing), {"instruments": [{"column": "A"}]})


def test_a_strategy_run_on_unchanged_data_copies_the_last_freeze_instead_of_preparing(tmp_path, monkeypatch):
    # / every strategy run went through Market name by name, read the trading terms and exported everything (172 s for the
    # S&P 500 over ten years with all of it on this machine); the same settings again, with Market's revisions as frozen, now copy the freeze.
    from modules.quant.backend import object_data
    calls, revision = [], {"s": 1}
    async def prepare(ctx, settings, owner, directory, report=None):
        calls.append(owner)
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("bars.parquet", "prices.parquet", "stock-sessions.parquet"):
            (directory / name).write_text(f"{name} r{revision['s']}")
        stock = {"lotSize": 1}
        return {"barsPath": str(directory / "bars.parquet"), "pricesPath": str(directory / "prices.parquet"), "summary": {"rows": 1},
                "stockSessions": str(directory / "stock-sessions.parquet"),
                "instruments": [{"id": "A.US", "type": "equity", "stock": stock}], "sources": [{"nativeInstrument": "A.US", "stock": stock}],
                "frozen": {"until": 9, "selections": [{"series": {"seriesId": "s"}}], "revisions": {"s": revision["s"]}}}
    async def revisions(ctx, selections, until):
        assert until == 9
        return {"s": {"revision": revision["s"], "source": "x"}}
    monkeypatch.setattr(object_data, "prepare", prepare)
    monkeypatch.setattr(object_data.M, "revisions", revisions)
    kept = tmp_path / "strategy-data" / "k"
    # Old freezes inferred the price step from bars, even when Market supplied it.
    kept.mkdir(parents=True)
    (kept / "prepared.pickle").write_bytes(pickle.dumps({
        "stockSessions": None, "frozen": {"until": 9, "selections": [], "revisions": {"s": 1}},
        "instruments": [{"id": "510300.CN", "priceIncrement": "0.01"}],
    }))
    async def run(name):
        return await object_data.prepare_reusing(None, {}, name, tmp_path / name, kept)
    async def concurrent_runs():
        return await asyncio.gather(run("run-1"), run("run-2"))
    first, second = asyncio.run(concurrent_runs())
    assert calls == ["run-1"] and second["barsPath"] == str(tmp_path / "run-2" / "bars.parquet")
    assert (tmp_path / "run-2" / "bars.parquet").read_text() == "bars.parquet r1" and second["summary"] == first["summary"]
    # the stocks' sessions are kept in their own file, not in the freeze every run unpickles (440 MB for the S&P 500 over ten years)
    assert second["stockSessions"] == str(tmp_path / "run-2" / "stock-sessions.parquet") and (tmp_path / "run-2" / "stock-sessions.parquet").is_file()
    assert second["instruments"][0]["stock"] == {"lotSize": 1} and second["sources"][0]["stock"] == {"lotSize": 1}
    revision["s"] = 2  # Market corrected a bar: prepared again, and that freeze is the one kept
    asyncio.run(run("run-3"))
    asyncio.run(run("run-4"))
    assert calls == ["run-1", "run-3"] and (tmp_path / "run-4" / "bars.parquet").read_text() == "bars.parquet r2"
