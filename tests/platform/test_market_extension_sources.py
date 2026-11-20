"""A third-party module's data source (``market.data_source``) on the real Market, each in its own process: Market
finds it in the catalog, asks it to describe itself, searches, reads history and live through it, and keeps
running."""

from __future__ import annotations

from modules.market.backend.common import ns_of_date
from tests.platform.harness import prepare, run, running, until
from tests.platform.test_quant_market import modules_root
from vocabulary.instruments import MarketInstrument, ohlcv_series


def test_a_third_party_source_is_found_described_searched_read_and_followed_live(tmp_path):
    async def scenario():
        root = prepare(modules_root(tmp_path), ("extsource",))
        async with running(tmp_path, official_root=root, wait=("market", "extsource")) as platform:
            market = platform.process("market").process.pid
            await until(lambda: platform.call("market.read_source", {"view": "list"}),
                        lambda value: any(item["source"]["sourceId"] == "extx" and item["state"] == "working"
                                          for item in value["sources"]))
            assert "describe" in await platform.call("extsource.asked")
            # Yahoo would be asked first and goes to the network; the third-party source is then the one asked.
            task = await platform.call("market.write_source", {"operation": {"op": "disable", "source": "yfinance"}})
            await until(lambda: platform.call("platform.tasks.get", {"taskId": task["taskId"]}),
                        lambda value: value["state"] == "completed")

            found = await platform.call("market.instruments.search", {"query": "ZZXT"})
            instrument = found["results"][0]["instrument"]
            assert instrument["symbol"] == "ZZXT" and "search" in await platform.call("extsource.asked")

            series = ohlcv_series(MarketInstrument.from_wire(instrument), "1d", adjustment="raw").to_dict()
            page = await platform.call("market.read_series", {
                "instrumentId": instrument["instrumentId"], "series": series, "start": str(ns_of_date("2024-06-03")),
                "end": str(ns_of_date("2024-06-06") - 1), "fill": "await"})
            assert page["columns"]["close"] == ["10", "11", "12"]

            items = await platform.call("extsource.follow", {"params": {"instrument": instrument, "series": series}})
            assert "live" in await platform.call("extsource.asked")
            assert items[-1]["type"] not in ("replace", "status"), items
            assert "13.5" in str(items[-1])

            assert platform.process("market").state == "ready" and platform.process("market").process.pid == market

    run(scenario())
