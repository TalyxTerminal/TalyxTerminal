"""The real Market (its declarations, handlers and process) on a local, deterministic source; nothing goes to the
network. US listings come from the recorded Nasdaq directory. MSFT has three daily bars from 2024-06-03 with a 2:1
split on the second and a $1 dividend on the third. 600519.SH and 510300.SH each have six daily bars, the ETF at 3.12.
SPY has a daily bar for each 2025 US exchange session.
Yahoo's daily bars are published 15 minutes after the close."""

from __future__ import annotations

import numpy as np

from modules.market.backend import app, bars, calendars
from modules.market.backend.common import ns_of_date
from modules.market.backend.entry import module  # noqa: F401 - the real Market's declarations
from modules.market.backend.sources.base import History
from tests.fixtures.market.replay import ReplayEgress


class LocalMarket(app.Market):
    def __init__(self, ctx, root, **options):
        super().__init__(ctx, root, egress=ReplayEgress(("nasdaqtrader", "symbol-directory")), **options)

    async def start(self, *, load_catalogs=True):
        await super().start(load_catalogs=False)
        await self.instruments.load("nasdaqtrader", "US", "listings")
        start, end = ns_of_date("2024-06-03"), ns_of_date("2024-06-06")
        opened, closed = calendars.bar_grid("XNYS", "1d", start, end)
        us = {"time": np.asarray(opened, dtype=np.int64), "close_time": np.asarray(closed, dtype=np.int64),
              **{field: np.array([100., 50., 49.]) for field in ("open", "high", "low", "close")},
              "volume": np.array([1000., 2000., 2000.])}
        actions = [{"exDate": str(int(opened[1])), "kind": "split", "ratio": 2.0},
                   {"exDate": str(int(opened[2])), "kind": "cash_dividend", "amount": 1.0, "currency": "USD"}]
        self.identity.upsert_listings("US", [{"code": "SPY", "assetClass": "fund", "securityType": "etf",
                                             "mic": "ARCX", "currency": "USD"}], source="nasdaqtrader")
        spy_open, spy_close = calendars.bar_grid("XNYS", "1d", ns_of_date("2025-01-01"), ns_of_date("2026-01-01"))
        spy = {"time": spy_open, "close_time": spy_close,
               **{field: np.full(len(spy_open), 500.) for field in ("open", "high", "low", "close")},
               "volume": np.full(len(spy_open), 1000.)}
        cn_opened, cn_closed = calendars.daily_times("XSHG", np.asarray([
            "2024-01-02", "2024-01-03", "2024-01-04", "2024-06-03", "2024-06-04", "2024-06-05"], dtype="datetime64[D]"))
        cn = {"time": cn_opened, "close_time": cn_closed,
              **{field: np.array([2., 3., 4., 103., 104., 105.]) for field in ("open", "high", "low", "close")},
              "volume": np.full(6, 1000.)}

        etf = {**cn, **{field: np.full(len(cn_opened), 3.12) for field in ("open", "high", "low", "close")}}
        histories = {"MSFT": (us, actions), "SPY": (spy, []), "600519.SH": (cn, []), "510300.SH": (etf, [])}

        async def history(source_id, target, kind, timeframe, price_basis, start, end, archive=None):
            data, events = histories[target.instrument["symbol"]]
            return History(bars.window(data, start, end) if kind == "bars" else bars.empty(("time",)),
                           extra={"actions": [item for item in events if start <= int(item["exDate"]) < end]})

        for source_id in ("yfinance", "baostock"):
            self.registry.entry(source_id).source.history = history
        for row in self.registry.entry("yfinance").coverage["rows"]:
            if row["kind"] == "bars":
                row["publishDelay"] = "PT15M"


app.Market = LocalMarket
