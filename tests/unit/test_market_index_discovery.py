"""First instrument searches, before a directory or a previous lookup has supplied the index."""

import asyncio

import pytest

from modules.market.backend.app import Market
from modules.market.backend.common import MarketError
from modules.market.backend.identity import IdentityStore
from tests.fixtures.market.replay import ReplayEgress


def test_directory_indexes_only_the_final_name_when_a_listing_repeats(tmp_path):
    identity = IdentityStore(tmp_path / "identity.sqlite")
    row = {"code": "000300.SH", "assetClass": "index"}

    def load(*names):
        return identity.upsert_listings("CN", [{**row, "name": name} for name in names], source="directory")

    first, same = load("First spelling", "Second spelling")
    assert first == same
    assert identity.search("First spelling") == []
    assert load("Third spelling") == [first]
    assert identity.search("Second spelling") == []
    assert identity.search("Third spelling")[0]["instrument"]["instrumentId"] == first
    assert load("沪深 300 指数") == [first]
    assert identity.search("沪深 300")[0]["instrument"]["instrumentId"] == first


def test_full_index_name_beats_partial_etf_names_even_on_the_first_page(tmp_path):
    async def scenario():
        egress = ReplayEgress(("yfinance", "chart-daily"))
        egress.add("/v1/finance/search", {"quotes": [
            {"symbol": "^GSPC", "quoteType": "INDEX", "exchange": "SNP", "shortname": "S&P 500"}
        ]})
        market = Market(None, tmp_path, egress=egress)
        await market.start(load_catalogs=False)
        try:
            market.instruments.ingest([
                {"type": "listing", "market": "US", "code": f"ETF{i}", "assetClass": "fund",
                 "name": f"S&P 500 tracking fund {i}"} for i in range(210)
            ], "directory")
            page = await market.public.search({"query": "S&P 500", "limit": 1})
            index = page["results"][0]["instrument"]
            assert (index["symbol"], index["assetClass"]) == ("^GSPC", "index")
            match = (await market.public.get({"symbols": ["^GSPC"]}))["items"][0]
            assert match["instrument"]["instrumentId"] == index["instrumentId"]
            second = await market.public.search({"query": "S&P 500", "limit": 1, "cursor": page["nextCursor"]})
            assert second["results"][0]["instrument"]["assetClass"] == "fund"
            assert egress.count("/v1/finance/search") == 1
        finally:
            await market.close()
    asyncio.run(scenario())


def test_chinese_index_name_before_and_after_the_domestic_directory_loads(tmp_path):
    async def scenario():
        egress = ReplayEgress()
        egress.add("/v1/finance/search", {"quotes": [
            {"symbol": "000300.SS", "quoteType": "INDEX", "exchange": "SHH", "shortname": "CSI 300 Index"}
        ]})
        egress.add("/v8/finance/chart/000300.SS", {"chart": {"result": [{"timestamp": [1]}]}})
        market = Market(None, tmp_path, egress=egress)
        await market.start(load_catalogs=False)
        try:
            request = {"query": "沪深 300", "market": "CN", "assetClass": "index"}
            first = (await market.public.search(request))["results"][0]["instrument"]
            assert (first["symbol"], first["name"]) == ("000300.SH", "CSI 300 Index")
            market.instruments.ingest([{"type": "listing", "market": "CN", "code": "000300.SH",
                                        "assetClass": "index", "name": "沪深300指数"}], "baostock")
        finally:
            await market.close()
        # The named domestic listing survives a restart with no search provider.
        market = Market(None, tmp_path, sources=[])
        try:
            local = (await market.public.search(request))["results"][0]["instrument"]
            assert (local["instrumentId"], local["name"]) == (first["instrumentId"], "沪深300指数")
        finally:
            await market.close()
    asyncio.run(scenario())


def test_concurrent_code_search_and_quant_lookup_share_the_pending_answer(tmp_path, monkeypatch):
    async def scenario():
        egress = ReplayEgress(("yfinance", "chart-daily"))
        market = Market(None, tmp_path, egress=egress)
        await market.start(load_catalogs=False)
        started, release = asyncio.Event(), asyncio.Event()
        source = market.registry.entry("yfinance").source
        prices = source.has_prices

        async def delayed(code, venue):
            started.set()
            await release.wait()
            return await prices(code, venue)

        monkeypatch.setattr(source, "has_prices", delayed)
        try:
            search = asyncio.create_task(market.public.search({"query": "^GSPC"}))
            await asyncio.wait_for(started.wait(), 10)
            local_lookup = asyncio.Event()
            loop = asyncio.get_running_loop()
            local_search = market.identity.search

            def searched(*args, **kwargs):
                results = local_search(*args, **kwargs)
                loop.call_soon_threadsafe(local_lookup.set)
                return results

            monkeypatch.setattr(market.identity, "search", searched)
            lookup = asyncio.create_task(market.public.get({"symbols": ["^GSPC"]}))
            await asyncio.wait_for(local_lookup.wait(), 10)  # both calls saw the empty directory
            release.set()
            page, batch = await asyncio.gather(search, lookup)
            assert batch["items"][0]["status"] == "found"
            assert batch["items"][0]["instrument"]["instrumentId"] == page["results"][0]["instrumentId"]
            assert egress.count("/v1/finance/search") == 1
        finally:
            release.set()
            await market.close()
    asyncio.run(scenario())


def test_a_longer_local_index_code_does_not_hide_the_exact_code(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(("yfinance", "chart-daily")))
        await market.start(load_catalogs=False)
        try:
            market.instruments.ingest([{"type": "listing", "market": "US", "code": "^GSPC-TEST",
                                        "name": "Test index", "assetClass": "index"}], "directory")
            result = await market.public.search({"query": "^GSPC", "limit": 1})
            assert result["results"][0]["symbol"] == "^GSPC"
        finally:
            await market.close()
    asyncio.run(scenario())


def test_transient_price_verification_error_can_be_retried_immediately(tmp_path, monkeypatch):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(("yfinance", "chart-daily")))
        await market.start(load_catalogs=False)
        source = market.registry.entry("yfinance").source
        prices = source.has_prices

        async def unavailable(code, venue):
            raise MarketError("source_unavailable", "price service timed out", source_id="yfinance")

        try:
            monkeypatch.setattr(source, "has_prices", unavailable)
            with pytest.raises(MarketError, match="price service timed out"):
                await market.public.get({"symbols": ["^GSPC"]})
            monkeypatch.setattr(source, "has_prices", prices)
            assert (await market.public.get({"symbols": ["^GSPC"]}))["items"][0]["status"] == "found"
        finally:
            await market.close()
    asyncio.run(scenario())
