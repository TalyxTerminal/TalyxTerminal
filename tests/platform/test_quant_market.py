"""Quant's data preparation on the real Market, each in its own process (Market on a local source, nothing goes to the
network): a strategy's frozen bars and a research read's adjusted and raw bars are exactly what Market reads for the
same series, each bar known at its close; Shanghai daily bars keep their dates, and a mixed list keeps each market's
own days."""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from modules.market.backend.common import ns_of_date
from modules.quant.backend import object_data, research_calendar, research_data, runtime
from modules.quant.backend.materials import plain
from modules.quant.native.stock_execution import conditions
from tests.platform.harness import FIXTURES, REPO, real_modules, route, run
from vocabulary.instruments import MarketInstrument, ohlcv_series

MS = 1_000_000
CHINESE = MarketInstrument(
    identityVersion=2, instrumentId="vi2_" + "9" * 32, venueId="CN", symbol="600519.SH", displaySymbol="600519.SH",
    marketType="spot", assetClass="equity", securityType="common_stock", calendarId="XSHG", currency="CNY",
    adjustmentEligible=True).to_dict()


def modules_root(tmp_path: Path) -> Path:
    """The local Market under Market's own contract (its declarations are the real Market's)."""
    root = tmp_path / "modules"
    shutil.copytree(FIXTURES / "localmarket", root / "localmarket")
    (root / "localmarket" / "public").mkdir()
    shutil.copyfile(REPO / "modules" / "market" / "public" / "contract.json",
                    root / "localmarket" / "public" / "contract.json")
    return root


class Platform:
    """The SDK as Quant's process holds it: calls go to the Host as module quant, for the workspace."""

    def __init__(self, platform, workspace: str):
        self.platform, self.workspace = platform, workspace

    async def call(self, name, input, *, workspace=None):
        caller = {"origin": "module", "module": "quant", "workspaceId": workspace or self.workspace, "session": None}
        return await self.platform.call(name, input, caller=caller)


def quant(platform, workspace: str, tmp_path: Path) -> runtime.Quant:
    runtime.WORKSPACE.set(workspace)
    folder = tmp_path / "quant"
    return runtime.Quant(Platform(platform, workspace), {"dataDir": folder / "data", "userDir": folder / "user",
                                                         "environmentsDir": folder / "environments"})


def test_quant_freezes_what_market_reads_adjusted_and_raw_and_each_bar_is_known_at_its_close(tmp_path):
    async def scenario():
        async with real_modules(tmp_path, "quant", root=modules_root(tmp_path)) as platform:
            ctx = quant(platform, await platform.workspace(), tmp_path)
            instrument = plain(await ctx.call("market.instruments.get", {"symbols": ["US:MSFT"]}))["items"][0]["instrument"]
            data = {"symbols": [{"instrument": instrument}], "timeframe": "1d", "since": "2024-06-03",
                    "until": "2024-06-05"}
            strategy = await object_data.prepare(ctx, {"data": data, "account": {}}, "integration-strategy",
                                                 tmp_path / "strategy")
            raw_strategy = await object_data.prepare(ctx, {"data": {**data, "adjustment": "raw"}, "account": {}},
                                                     "integration-raw-strategy", tmp_path / "strategy-raw")
            adjusted = await research_data.prepare(ctx, data, "integration-research", tmp_path / "adjusted")
            raw = await research_data.prepare(ctx, {**data, "adjustment": "raw"}, "integration-raw", tmp_path / "raw")
            pages = {}
            for adjustment in ("raw", "split", "split_dividend"):
                series = ohlcv_series(MarketInstrument.from_wire(instrument), "1d", adjustment=adjustment).to_dict()
                pages[adjustment] = plain(await ctx.call("market.read_series", {
                    "instrumentId": instrument["instrumentId"], "series": series, "start": str(ns_of_date("2024-06-03")),
                    "end": str(ns_of_date("2024-06-06")), "fill": "none", "limit": 10000}))
            return strategy, raw_strategy, adjusted, raw, pages

    strategy, raw_strategy, adjusted, raw, pages = run(scenario())
    native = pd.read_parquet(strategy["barsPath"])
    prices = pd.read_parquet(strategy["pricesPath"])
    adjusted_frame = pd.read_parquet(adjusted["research"]["barsPath"])
    raw_frame = pd.read_parquet(raw["research"]["barsPath"])
    expected, original = pages["split_dividend"]["columns"], pages["raw"]["columns"]
    assert [Decimal(value) for value in expected["close"]] == [Decimal(49)] * 3
    assert [Decimal(value) for value in original["close"]] == [Decimal(100), Decimal(50), Decimal(49)]
    assert [Decimal(value) for value in pd.read_parquet(raw_strategy["barsPath"])["close"]] == [Decimal(100), Decimal(50), Decimal(49)]
    for field in ("open", "high", "low", "close", "volume"):
        assert [Decimal(value) for value in native[field]] == [Decimal(value) for value in expected[field]]
        np.testing.assert_allclose(adjusted_frame[field], np.asarray(pages["split_dividend"]["columns"][field], dtype=float))
        np.testing.assert_allclose(raw_frame[field], np.asarray(original[field], dtype=float))
    assert prices["time"].tolist() == [int(value) // MS for value in expected["time"]]
    assert native["ts_event"].tolist() == [int(value) for value in expected["closeTime"]]
    # Known at the close; the 15-minute publish delay only decides when Market fetches the bar.
    assert native["ts_init"].tolist() == [int(value) for value in expected["closeTime"]]
    assert adjusted_frame["closeAt"].tolist() == [int(value) // MS for value in expected["closeTime"]]
    assert adjusted_frame["knownAt"].tolist() == adjusted_frame["closeAt"].tolist()
    assert pd.to_datetime(adjusted_frame["time"], unit="ms", utc=True).dt.tz_convert(
        "America/New_York").dt.hour.tolist() == [0, 0, 0]
    assert adjusted_frame["rawClose"].tolist() == raw_frame["close"].tolist()
    assert adjusted["research"]["info"]["instruments"][0]["revision"] == pages["split_dividend"]["revision"]


def test_shanghai_dates_are_kept_and_a_mixed_list_keeps_each_markets_own_days(tmp_path):
    async def scenario():
        async with real_modules(tmp_path, "quant", root=modules_root(tmp_path)) as platform:
            ctx = quant(platform, await platform.workspace(), tmp_path)
            data = {"symbols": [{"instrument": CHINESE}], "timeframe": "1d", "since": "2024-01-02",
                    "until": "2024-01-03"}
            strategy = await object_data.prepare(ctx, {"data": data, "account": {}}, "cn-strategy",
                                                 tmp_path / "cn-strategy")
            research = await research_data.prepare(ctx, data, "cn-research", tmp_path / "cn-research")
            native = pd.read_parquet(strategy["barsPath"])
            frame = pd.read_parquet(research["research"]["barsPath"])
            assert native["close"].tolist() == ["2", "3"] and frame["close"].tolist() == [2., 3.]
            days = ["2024-01-02", "2024-01-03"]
            assert pd.to_datetime(frame["label"], unit="ms", utc=True).dt.strftime("%Y-%m-%d").tolist() == days
            assert pd.to_datetime(frame["time"], unit="ms", utc=True).dt.tz_convert(
                "Asia/Shanghai").dt.strftime("%Y-%m-%d").tolist() == days
            info = research["research"]["info"]
            assert info["since"] == object_data.day_ms("2024-01-02", False)
            assert info["until"] == object_data.day_ms("2024-01-03", True)
            zone, dates = await research_calendar.date_boundaries(ctx, info["calendarId"], info["timeframe"],
                                                                  info["sessionScope"], info["since"], info["until"])
            assert [research_calendar.label_of(opened, "1d", zone) for opened, _ in dates] == frame["label"].tolist()
            american = plain(await ctx.call("market.instruments.get", {"symbols": ["US:MSFT"]}))["items"][0]["instrument"]
            mixed = await object_data.prepare(ctx, {"data": {
                **data, "symbols": [{"instrument": CHINESE}, {"instrument": american}], "since": "2024-06-03",
                "until": "2024-06-04"}, "account": {}}, "mixed", tmp_path / "mixed")
            prices = pd.read_parquet(mixed["pricesPath"])
            for source in mixed["sources"]:
                chosen = prices[prices["instrument"] == source["nativeInstrument"]]
                zone = "Asia/Shanghai" if source["instrument"]["venueId"] == "CN" else "America/New_York"
                assert pd.to_datetime(chosen["time"], unit="ms", utc=True).dt.tz_convert(zone).dt.strftime(
                    "%Y-%m-%d").tolist() == ["2024-06-03", "2024-06-04"]
                assert source["coverage"]["missingBars"] == 0

    run(scenario())


def test_shanghai_etf_and_stock_keep_exchange_ticks_and_stamp_duty(tmp_path):
    async def scenario():
        etf = {**CHINESE, "instrumentId": "vi2_" + "8" * 32, "symbol": "510300.SH", "displaySymbol": "510300.SH",
               "assetClass": "fund", "securityType": "etf"}
        async with real_modules(tmp_path, "quant", root=modules_root(tmp_path)) as platform:
            ctx = quant(platform, await platform.workspace(), tmp_path)
            data = {"symbols": [{"instrument": item} for item in (etf, CHINESE)], "timeframe": "1d",
                    "since": "2024-01-02", "until": "2024-01-03"}
            return await object_data.prepare(ctx, {"data": data, "account": {}}, "shanghai", tmp_path / "shanghai")

    prepared = run(scenario())
    instruments = {item["id"]: item for item in prepared["instruments"]}
    sessions = pd.read_parquet(prepared["stockSessions"])
    sources = {source["instrument"]["symbol"]: source["nativeInstrument"] for source in prepared["sources"]}
    bars = pd.read_parquet(prepared["barsPath"])
    etf_prices = bars.loc[bars.instrument == sources["510300.SH"], ["open", "high", "low", "close"]]
    assert etf_prices.shape == (2, 4) and (etf_prices == "3.12").all().all()
    for symbol, step, precision, sell_duty in [("510300.SH", "0.001", 3, 0), ("600519.SH", "0.01", 2, 500)]:
        item = instruments[sources[symbol]]
        assert (item["priceIncrement"], item["pricePrecision"]) == (step, precision)
        item["stock"]["sessions"] = [{**{key: row[key] for key in ("sessionDate", "openAt", "closeAt", "lower", "upper", "priceIncrement")},
                                      "priceMap": {"scale": row["priceScale"], "offset": row["priceOffset"], "maxError": row["priceMaxError"]}}
                                     for row in sessions[sessions["instrument"] == item["id"]].to_dict("records")]
        days = conditions(item)["rules"]["sessions"]
        assert [day["date"] for day in days] == ["2024-01-02", "2024-01-03"]
        for day in days:
            assert Decimal(day["priceIncrement"]) == Decimal(step)
            assert Decimal(day["buyTaxRate"]) * 1_000_000 == 0
            assert Decimal(day["sellTaxRate"]) * 1_000_000 == sell_duty


def test_new_workspace_preflights_and_freezes_cached_spy_for_the_crossover(tmp_path):
    async def scenario():
        async with real_modules(tmp_path, "quant", root=modules_root(tmp_path)) as platform:
            history = await platform.workspace("market-history")
            cached = await platform.call("market.read_data", {
                "symbols": ["SPY"], "timeframe": "1d", "start": "2025-01-02", "end": "2025-01-03", "adjustment": "raw",
            }, workspace=history)
            assert len(cached["rows"]) == 2
            workspace = await platform.workspace("qa-agent")
            obj = await platform.call("quant.create_object", {
                "kind": "strategy", "preset": "crossover", "params": {"fast": 20, "slow": 100},
                "data": {"symbols": ["SPY"], "timeframe": "1d", "start": "2025-01-01", "end": "2025-12-31"},
            }, workspace=workspace)
            settings = (await route(platform, "quant", "GET", f"/objects/{obj['objectId']}", workspace))["settings"]
            ctx = quant(platform, workspace, tmp_path)
            selected, = settings["data"]["symbols"]
            # The strategy check asks Market which bar sizes it can serve before freezing the requested prices.
            await object_data.check_routes(ctx, [selected["instrument"]], settings["data"]["timeframe"])
            prepared = await object_data.prepare(ctx, settings, "crossover-2025", tmp_path / "crossover")
            summary = prepared["summary"]
            assert (summary["requested"], summary["traded"], summary["rows"]) == (1, 1, 250)
            assert [item["symbol"] for item in summary["instruments"]] == ["SPY"]
            assert summary["skipped"] == []

    run(scenario())
