"""Market 2.0 local library, imports, panel export and delete (build criteria 33-35), replaying recorded data."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from modules.market.backend import calendars
from modules.market.backend.app import Market
from modules.market.backend.common import MarketError, iso_of_ns, ns_of_date
from tests.unit.test_market_research import NASDAQ, STOCKS, YAHOO, Upstream, request_2024
from vocabulary.instruments import MarketInstrument, ohlcv_series

AI = {"caller": {"module": "platform", "principal": "client", "source": {"kind": "internal_ai"}}}


def run(coro):
    return asyncio.run(coro)


async def open_market(root: Path, egress=None, *, okx=False) -> Market:
    market = Market(None, root, egress=egress or Upstream(YAHOO, NASDAQ))
    await market.start(load_catalogs=False)
    await market.instruments.load("nasdaqtrader", "US", "listings")
    if okx:
        await market.instruments.load("okx", "okx", "contracts")
    return market


async def resolve(market_, text, **options):
    return (await market_.instruments.resolve({"text": text, **options}))["instrument"]


async def stocks_2024(market):
    records = [await resolve(market, code, market="US") for code in STOCKS]
    result = await market.jobs.fetch(request_2024(records), workspace_id=None)
    assert result["completed"] == 20
    return records


# ── 33. the local library ──────────────────────────────────────────────────


# ── 34. importing a folder ─────────────────────────────────────────────────


def write_daily(path: Path, rows: list[list], *, code: str | None = None) -> None:
    with open(path, "w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow((["symbol"] if code else []) + ["date", "open", "high", "low", "close", "volume"])
        for row in rows:
            writer.writerow(([code] if code else []) + row)


def sessions_2024() -> list[str]:
    opens, _closes = calendars.bar_grid("XNYS", "1d", ns_of_date("2024-01-01"), ns_of_date("2025-01-01"))
    return [str(day) for day in calendars.trading_day("XNYS", opens)]


def prices(days: list[str], base: float) -> list[list]:
    return [[day, base + i, base + i + 2, base + i - 1, base + i + 1, 1000 + i] for i, day in enumerate(days)]


def test_import_keeps_files_as_their_own_series_and_refuses_bad_rows_one_by_one(tmp_path):
    async def scenario():
        market = await open_market(tmp_path / "market")
        folder = tmp_path / "workspace" / "imports"
        folder.mkdir(parents=True)
        days = sessions_2024()
        assert len(days) == 252
        write_daily(folder / "AAPL_1d_2024.csv", prices(days, 180.0))
        write_daily(folder / "XYZQ_1d_2024.csv", prices(days, 10.0))
        bad = prices(days[:30], 50.0)
        bad[3][0] = "2024-02-30"
        bad[7][4] = "n/a"
        write_daily(folder / "BAD_1d_2024.csv", bad)

        async def read(path):
            return (tmp_path / "workspace" / path).read_bytes()

        try:
            aapl = await resolve(market, "AAPL", market="US")
            await market.data.read({"instrument": aapl, "series": ohlcv_series(MarketInstrument.from_wire(aapl), "1d")
                                    .to_dict(), "start": str(ns_of_date("2024-01-01")),
                                    "end": str(ns_of_date("2025-01-01")), "limit": 1000})
            paths = [f"imports/{name}" for name in ("AAPL_1d_2024.csv", "XYZQ_1d_2024.csv", "BAD_1d_2024.csv")]
            result = await market.write_data.run({"operation": {"op": "import", "paths": paths}}, read=read)
            assert result["rows"] == 252 + 252 + 28
            assert (result["matched"], result["unmatchedCount"], sorted(result["unmatched"])) == (1, 2, ["BAD", "XYZQ"])
            assert result["rejectedCount"] == 2
            assert [row["line"] for row in result["rejectedRows"]] == [5, 9]
            assert "2024-02-30" in result["rejectedRows"][0]["reason"]
            assert "close is not a number" in result["rejectedRows"][1]["reason"]
            # AAPL: one more series under the same instrument; the instrument and its default series are unchanged.
            found = await market.instruments.search({"query": "AAPL", "markets": ["US"]})
            assert [r["instrument"]["symbol"] for r in found["results"]].count("AAPL") == 1
            keys = [key for key in market.store.keys(aapl["instrumentId"]) if key.import_id == result["importId"]]
            assert len(keys) == 1 and market.store.pointer(keys[0])["rows"] == 252
            default = await market.data.read({"instrument": aapl, "series": ohlcv_series(
                MarketInstrument.from_wire(aapl), "1d").to_dict(), "limit": 5, "fill": "none",
                "end": str(ns_of_date("2025-01-01"))})
            assert default["source"]["sourceId"] == "yfinance"
            imported = [item for item in market.extra_series(aapl)]
            page = await market.data.read({"instrument": aapl, "series": imported[0]["series"], "limit": 1000})
            assert len(page["columns"]["time"]) == 252
            assert iso_of_ns(int(page["columns"]["time"][0])) == "2024-01-02T05:00:00Z"
            # XYZQ: a new listing of its own, found by search, with its 252 rows.
            xyzq = (await market.instruments.search({"query": "XYZQ", "markets": ["US"]}))["results"]
            assert [r["instrument"]["symbol"] for r in xyzq] == ["XYZQ"]
            record = xyzq[0]["instrument"]
            series = market.extra_series(record)[0]["series"]
            assert len((await market.data.read({"instrument": record, "series": series, "limit": 1000}))
                       ["columns"]["time"]) == 252
            panel = await market.panel.call({"op": "library", "kind": f"import:{result['importId']}"})
            symbol = panel["series"]["columns"].index("symbol")
            assert sorted(row[symbol] for row in panel["series"]["rows"]) == ["AAPL", "BAD", "XYZQ"]
        finally:
            await market.close()

    run(scenario())


# ── 35. panel export, delete, cancel ───────────────────────────────────────


def test_workspace_export_and_external_folder_import(tmp_path, monkeypatch):
    from modules.market.backend import entry, models, public_inputs

    async def scenario():
        market = await open_market(tmp_path / "market")
        monkeypatch.setattr(entry, "market", market)
        workspace = tmp_path / "workspace"
        outside = tmp_path / "external"
        outside.mkdir()
        write_daily(outside / "AAPL_1d.csv", prices(["2024-01-02"], 20))
        call = SimpleNamespace(workspace_id="desk", progress=None)
        market.ctx = SimpleNamespace(platform=SimpleNamespace(workspace_folder=AsyncMock(return_value=str(workspace))))
        try:
            scan = await entry.scan_import(public_inputs.ScanImport(folder=str(outside)), call)
            assert scan["total"] == 1 and "AAPL" in scan["rows"][0]
            assert market.write_data.imports.ids() == []
            result = await entry.write_data(models.WriteDataInput(operation={"op": "import", "folder": str(outside)}), call)
            assert result["matched"] == result["rows"] == 1
            named = models.WriteDataInput(operation={"op": "import", "paths": [str(outside / "AAPL_1d.csv")]})
            assert (await entry.write_data(named, call))["matched"] == 1
            record = await resolve(market, "AAPL", market="US")
            series = market.extra_series(record)[0]["series"]
            request = public_inputs.Export(series=[{"series": series, "instrumentId": record["instrumentId"]}],
                                          destination={"folder": "exports"}, format="csv", fill="none")
            result = await entry.export(request, call)
            path = Path(result["files"][0]["path"])
            assert path.parent == workspace / "exports"
            assert len(path.read_text().splitlines()) == 2
            assert market.ctx.platform.workspace_folder.await_args.args == ("desk",)
            with pytest.raises(MarketError, match="bound workspace"):
                await entry.export(request, SimpleNamespace(workspace_id=None, progress=None))
        finally:
            await market.close()

    run(scenario())


def test_export_writes_one_csv_to_absolute_folder(tmp_path, monkeypatch):
    async def scenario():
        market = await open_market(tmp_path / "market")
        try:
            records = await stocks_2024(market)
            selections = [{"instrument": r, "series": ohlcv_series(MarketInstrument.from_wire(r), "1d").to_dict()}
                          for r in records]
            value = {"selections": selections, "start": str(ns_of_date("2024-01-01")),
                     "end": str(ns_of_date("2025-01-01")), "format": "csv", "adjustment": "split_dividend",
                     "destination": {"folder": str(tmp_path / "exports" / "us-daily")}}
            result = await market.exports.export(value)
            (file,) = result["files"]
            path = Path(file["path"])
            assert path.suffix == ".csv" and path.parent == tmp_path / "exports" / "us-daily"
            with open(path, encoding="utf-8", newline="") as stream:
                lines = list(csv.DictReader(stream))
            assert len(lines) == 5040
            aapl = [row for row in lines if row["code"] == "AAPL"]
            assert aapl[-1]["time"][:10] == "2024-12-31"
            assert round(float(aapl[-1]["close"]), 2) == 250.42
            prior = path.read_bytes()
            empty = {**value, "fill": "none", "start": str(ns_of_date("2024-08-03")),
                     "end": str(ns_of_date("2024-08-05"))}
            with pytest.raises(MarketError, match="Nothing to export"):
                await market.exports.export(empty)
            assert path.read_bytes() == prior and list(path.parent.iterdir()) == [path]

            original_write = market.exports.write_series
            def fail_after_write(*args):
                original_write(*args)
                raise OSError("simulated write failure")

            monkeypatch.setattr(market.exports, "write_series", fail_after_write)
            with pytest.raises(OSError, match="simulated write failure"):
                await market.exports.export({**value, "fill": "none"})
            assert path.read_bytes() == prior and list(path.parent.iterdir()) == [path]
        finally:
            await market.close()

    run(scenario())


def test_deleting_a_source_keeps_instruments_and_a_cancelled_job_keeps_what_it_finished(tmp_path):
    async def scenario():
        egress = Upstream(YAHOO, NASDAQ, delay=0.01, spread=0.03)
        market = await open_market(tmp_path / "market", egress)
        try:
            records = [await resolve(market, code, market="US") for code in STOCKS]

            def progress(event):
                if event["done"] == 8 and not closing.done():
                    closing.set_result(None)
                    asyncio.ensure_future(market.jobs.workspace_closed("w"))

            closing = asyncio.get_running_loop().create_future()
            first = await market.jobs.fetch(request_2024(records), workspace_id="w", progress=progress)
            assert first["state"] == "interrupted"
            job = market.jobs.get(first["jobId"])
            done = [item["instrument"]["instrumentId"] for item in job.record["items"] if item["state"] == "done"]
            assert len(done) == 8
            assert (await market.panel.call({"op": "cancel", "jobId": job.id}, "w"))["state"] == "cancelled"
            jobs = (await market.panel.call({"op": "jobs"}, "w"))["jobs"]
            assert jobs[0]["state"] == "cancelled" and jobs[0]["done"] == 8
            library = (await market.panel.call({"op": "library"}))["series"]
            assert sorted(row[library["columns"].index("instrumentId")] for row in library["rows"]) == sorted(done)
            # Delete everything Yahoo gave: rows go, instruments stay.
            deleted = await market.write_data.run({"operation": {"op": "delete", "sourceId": "yfinance"}},
                                                  read=None)
            assert deleted["deletedBytes"] > 0 and deleted["instruments"] == 8
            assert (await market.panel.call({"op": "library"}))["series"]["rows"] == []
            local = await market.public.coverage({})
            assert local["rows"] == []
            aapl = await resolve(market, "AAPL", market="US")
            assert aapl["instrumentId"] == records[0]["instrumentId"]
            assert market.identity.record(records[0]["instrumentId"]) is not None
        finally:
            await market.close()

    run(scenario())


def test_public_download_estimate_and_workspace_controls(tmp_path, monkeypatch):
    from modules.market.backend import entry, public_inputs

    async def scenario():
        market = await open_market(tmp_path)
        monkeypatch.setattr(entry, "market", market)
        monkeypatch.setattr(market.jobs, "free_bytes", lambda: 17)
        call = SimpleNamespace(workspace_id="desk", progress=None)
        params = public_inputs.Fetch(symbols=["AAPL"], data=[{"kind": "bars", "timeframe": "1d"}],
                                    start="2024-01-01", end="2024-12-31")

        async def downloads(op, **args):
            return await entry.downloads(public_inputs.Downloads(operation={"op": op, **args}), call)

        try:
            estimate = await entry.estimate_fetch(params, call)
            assert estimate["estimatedBytes"] > estimate["freeBytes"] == 17
            assert await downloads("list") == {"jobs": [], "nextCursor": None}
            with pytest.raises(MarketError) as denied:
                await entry.fetch(params, call)
            assert denied.value.details == {key: estimate[key] for key in ("estimatedBytes", "freeBytes")}
            monkeypatch.setattr(market.jobs, "free_bytes", lambda: 10**9)
            request = await market.public.fetch_request(entry._value(params))
            job = await market.jobs.create(request, market.jobs.key(request), workspace_id="desk")
            job.record["state"] = "interrupted"
            assert (await downloads("list"))["jobs"][0]["jobId"] == job.id
            call.workspace_id = "another"
            assert not (await downloads("list"))["jobs"]
            with pytest.raises(MarketError, match="this workspace"):
                await downloads("cancel", jobId=job.id)
            call.workspace_id = "desk"
            assert (await downloads("resume", jobId=job.id))["state"] == "queued"
            await asyncio.sleep(0)
            assert (await downloads("cancel", jobId=job.id))["state"] == "cancelled"
            assert (await downloads("list"))["jobs"][0]["state"] == "cancelled"
            assert market.jobs.result(job)["state"] == "cancelled"
        finally:
            await market.close()

    run(scenario())


def test_ai_imports_and_deletes_ask_the_platform_first(tmp_path):
    async def scenario():
        market = await open_market(tmp_path / "market")
        asked = []

        async def approve(operation):
            asked.append(operation["op"])
            raise MarketError("permission_denied", "The user declined")

        try:
            with pytest.raises(MarketError):
                await market.write_data.run({"operation": {"op": "delete", "sourceId": "yfinance"}}, read=None,
                                            approve=approve)
            assert asked == ["delete"]
        finally:
            await market.close()

    run(scenario())


def test_dates_without_a_time_are_that_days_session(tmp_path):
    columns = np.asarray([ns_of_date("2024-06-03")], dtype=np.int64)
    opens, closes = calendars.daily_times("XNYS", columns.astype("datetime64[ns]").astype("datetime64[D]"))
    assert iso_of_ns(int(opens[0])) == "2024-06-03T04:00:00Z" and iso_of_ns(int(closes[0])) == "2024-06-03T20:00:00Z"


def test_import_matches_each_trading_date_and_preserves_unknown_volume(tmp_path):
    async def scenario():
        market = Market(None, tmp_path / "market", sources=[])
        identity = market.identity
        old, = identity.upsert_listings("US", [{"code": "REUSE", "codeFrom": "1",
                                                "codeUntil": str(ns_of_date("2020-01-01"))}], source="history")
        new, = identity.upsert_listings("US", [{"code": "REUSE", "codeFrom": str(ns_of_date("2024-01-01"))}],
                                        source="history")
        folder = tmp_path / "prices"
        folder.mkdir()
        (folder / "REUSE.csv").write_text("date,open,high,low,close,volume\n"
            "2015-02-03,1,1,1,1,\n2025-02-03,10,10,10,10,0\n2025-02-04T00:00:00-05:00,11,11,11,11,2\n")
        scan = await market.write_data.scan_folder(folder, {"timeframe": "1d"})
        assert {(r["instrumentId"], r["rows"]) for r in scan["files"]} == {(old, 1), (new, 2)}
        result = await market.write_data.import_folder(folder, {"timeframe": "1d"})
        assert result["instruments"] == 2
        a, = market.store.keys(old)
        b, = market.store.keys(new)
        assert np.isnan(market.store.read(a)["volume"][0])
        rows = market.store.read(b)
        assert rows["volume"].tolist() == [0, 2]
        assert rows["close"].tolist() == [10, 11]
        assert [iso_of_ns(int(t)) for t in rows["time"]] == ["2025-02-03T05:00:00Z", "2025-02-04T05:00:00Z"]

    run(scenario())


@pytest.mark.parametrize(("timeframe", "first", "last"), [
    ("1mo", "2024-02-01T05:00:00Z", "2024-02-29T21:00:00Z"),
    ("1y", "2024-01-01T05:00:00Z", "2024-12-31T21:00:00Z")])
def test_import_calendar_periods(tmp_path, timeframe, first, last):
    async def scenario():
        market = Market(None, tmp_path / "market", sources=[])
        folder = tmp_path / "prices"
        folder.mkdir()
        (folder / "ABC.csv").write_text("date,open,high,low,close\n2024-02-26,5,8,3,4\n")
        await market.write_data.import_folder(folder, {"timeframe": timeframe})
        key, = market.store.keys()
        rows = market.store.read(key)
        assert iso_of_ns(int(rows["time"][0])) == first
        assert iso_of_ns(int(rows["close_time"][0])) == last
        assert np.isnan(rows["volume"][0])
    run(scenario())


def test_mixed_date_labels_and_zoned_instants_use_exchange_dates(tmp_path):
    async def scenario():
        market = Market(None, tmp_path / "market", sources=[])
        folder = tmp_path / "prices"
        folder.mkdir()
        (folder / "ABC.csv").write_text("date,open,high,low,close,volume\n"
            "2025-02-03,1,1,1,1,0\n2025-02-04T00:00:00-05:00,2,2,2,2,1\n")
        await market.write_data.import_folder(folder, {"market": "US", "timeframe": "1d"})
        key, = market.store.keys()
        assert [iso_of_ns(int(t)) for t in market.store.read(key)["time"]] == [
            "2025-02-03T05:00:00Z", "2025-02-04T05:00:00Z"]
    run(scenario())


def test_price_only_csv_has_unknown_volume_on_the_public_page(tmp_path):
    async def scenario():
        market = Market(None, tmp_path / "market", sources=[])
        folder = tmp_path / "prices"
        folder.mkdir()
        (folder / "ABC.csv").write_text("date,open,high,low,close\n2025-02-03,1,1,1,1\n")
        await market.write_data.import_folder(folder, {"timeframe": "1d"})
        key, = market.store.keys()
        record = market.identity.record(key.instrument_id)
        series = market.extra_series(record)[0]["series"]
        page = await market.data.read({"instrument": record, "series": series, "fill": "none"})
        assert page["columns"]["volume"] == [None]
    run(scenario())
