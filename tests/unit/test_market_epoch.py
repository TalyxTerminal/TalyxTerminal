"""Market keeps no bar before 1970: vocabulary times are non-negative nanoseconds.

Fetching S&P 500 members' whole daily history from Yahoo stored IBM, Boeing and others from
1962; their listedFrom became negative, every record failed to load and the panel stopped updating.
"""

from __future__ import annotations

import asyncio

import numpy as np

from modules.market.backend.app import Market
from modules.market.backend.common import DAY_NS
from modules.market.backend.sources.base import History, Source, coverage, row
from tests.fixtures.market.replay import ReplayEgress
from vocabulary.instruments import MarketInstrument, ohlcv_series


class OldListing(Source):
    """A daily source whose answer starts in 1969, whatever start is asked."""

    id = "old"
    descriptor = {"name": "Old", "sourceIds": ["old"], "kinds": ["bars"], "assetClasses": ["equity"],
                  "credentials": "none", "paid": False, "supports": ["history"]}
    asked: list[int] = []

    async def describe(self, source_id):
        return coverage([row({"markets": ["US"]}, "bars", [("1d", "1962-01-02", 10000)])], concurrency=1, rpm=None)

    async def history(self, source_id, target, kind, timeframe, price_basis, start, end, archive=None):
        OldListing.asked.append(start)
        times = np.arange(-3 * DAY_NS, 5 * DAY_NS, DAY_NS, dtype=np.int64) + 5 * 3600 * 10**9
        ones = np.ones(len(times))
        return History({"time": times, "close_time": times + 16 * 3600 * 10**9, "open": ones, "high": ones,
                        "low": ones, "close": ones, "volume": ones})


def test_nothing_before_1970_is_asked_for_kept_or_dated(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(), sources=[])
        market.registry.add(OldListing(market))
        await market.start(load_catalogs=False)
        try:
            record = {"instrumentId": "vi2_" + "1" * 32, "identityVersion": 2, "venueId": "US", "symbol": "IBMQ",
                      "displaySymbol": "IBMQ", "marketType": "spot", "assetClass": "equity", "calendarId": "XNYS",
                      "adjustmentEligible": False}
            record = (await market.public.get({"records": [record]}))["items"][0]["instrument"]
            series = ohlcv_series(MarketInstrument.from_wire(record), "1d").to_dict()
            page = await market.data.read({"instrument": record, "series": series, "fill": "await",
                                           "start": "0", "end": str(6 * DAY_NS)})
            times = [int(value) for value in page["columns"]["time"]]
            assert times and min(times) >= 0
            assert min(OldListing.asked) >= 0
            stored = market.identity.record(record["instrumentId"])
            MarketInstrument.from_wire(stored)          # a record that loads: listedFrom is not negative
            assert int(stored["listedFrom"]) == times[0]
        finally:
            await market.close()

    asyncio.run(scenario())
