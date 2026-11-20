"""A crypto venue declares only the periods it gives on the UTC grid; Market makes the rest.

HTX cuts its days at 16:00 UTC and has no 6h or 12h bars; Deribit cuts 6h, 12h and days at
08:00 UTC and has no 4h; Coinbase has no 4h (all seen 2026-11-10). A venue without UTC days
still has daily bars: Market builds them from the venue's intraday bars.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from modules.market.backend import bars
from modules.market.backend.app import Market
from modules.market.backend.common import DAY_NS, MarketError, now_ns, ns_of_date
from modules.market.backend.sources.base import History, Source, coverage, row
from modules.market.backend.sources.crypto import _ohlcv_columns
from tests.fixtures.market.replay import ReplayEgress
from vocabulary.instruments import MarketInstrument, ohlcv_series

HOUR = 3600 * 10**9


def periods(market, venue):
    rows = asyncio.run(market.registry.entry(venue).source.describe(venue))["rows"]
    return [p["timeframe"] for p in rows[0]["periods"]]


def test_venues_declare_only_the_periods_they_give_on_the_utc_grid(tmp_path):
    market = Market(None, tmp_path, egress=ReplayEgress())
    htx, deribit, coinbase, okx = (periods(market, venue) for venue in ("htx", "deribit", "coinbase", "okx"))
    assert not {"1d", "6h", "12h"} & set(htx) and "4h" in htx
    assert not {"1d", "12h", "6h", "4h"} & set(deribit) and "1h" in deribit
    assert "4h" not in coinbase and "1d" in coinbase
    assert {"1m", "4h", "12h", "1d"} <= set(okx)


def test_bars_off_the_utc_grid_are_refused():
    four_hours = [[(ns_of_date("2026-11-10") + 2 * HOUR) // 10**6, 1, 2, 0.5, 1.5, 10]]
    with pytest.raises(MarketError, match="not cut on the UTC grid"):
        _ohlcv_columns(four_hours, 4 * HOUR, "deribit", "4h")
    on_grid = [[(ns_of_date("2026-11-10") + 4 * HOUR) // 10**6, 1, 2, 0.5, 1.5, 10]]
    assert bars.length(_ohlcv_columns(on_grid, 4 * HOUR, "okx", "4h")) == 1


class HourlyVenue(Source):
    """A venue that gives hourly bars only (like HTX without UTC days)."""

    id = "hourly"
    descriptor = {"name": "Hourly venue", "sourceIds": ["hourly"], "kinds": ["bars"], "assetClasses": ["crypto"],
                  "credentials": "none", "paid": False, "supports": ["history"]}

    async def describe(self, source_id):
        return coverage([row({"venues": ["hourly"]}, "bars", [("1h", "2026-01-01", 1000)])], concurrency=1, rpm=None)

    async def history(self, source_id, target, kind, timeframe, price_basis, start, end, archive=None):
        assert timeframe == "1h"
        now = now_ns()
        times = np.arange(start - start % HOUR, min(end, now - now % HOUR), HOUR, dtype=np.int64)
        hour = (times % DAY_NS) // HOUR
        columns = {"time": times, "close_time": times + HOUR, "open": 100.0 + hour, "high": 101.0 + hour,
                   "low": 99.0 + hour, "close": 100.5 + hour, "volume": np.ones(len(times))}
        return History(columns)


def test_a_venue_without_utc_days_has_daily_bars_built_from_its_hours(tmp_path):
    async def scenario():
        market = Market(None, tmp_path, egress=ReplayEgress(), sources=[])
        market.registry.add(HourlyVenue(market))
        await market.start(load_catalogs=False)
        try:
            record = MarketInstrument(venueId="hourly", symbol="BTC/USDT", displaySymbol="BTC/USDT", marketType="spot",
                                      assetClass="crypto", base="BTC", quote="USDT").wire_dict()
            record = (await market.public.get({"records": [record]}))["items"][0]["instrument"]
            series = ohlcv_series(MarketInstrument.from_wire(record), "1d").to_dict()
            page = await market.data.read({"instrument": record, "series": series, "fill": "await",
                                           "start": str(ns_of_date("2026-11-01")), "end": str(ns_of_date("2026-11-03"))})
            columns = page["columns"]
            assert columns["time"] == [str(ns_of_date("2026-11-01")), str(ns_of_date("2026-11-02"))]
            assert [columns[name][0] for name in ("open", "high", "low", "close", "volume")] == \
                ["100", "124", "99", "123.5", "24"]
            # Today is still going on: its hours so far are not a closed day.
            latest = await market.data.read({"instrument": record, "series": series, "fill": "await",
                                             "start": str(now_ns() - 3 * DAY_NS)})
            moment = now_ns()
            today = moment - moment % DAY_NS
            assert latest["columns"]["time"][-1] == str(today - DAY_NS)
            assert int(latest["columns"]["closeTime"][-1]) == today
        finally:
            await market.close()

    asyncio.run(scenario())
