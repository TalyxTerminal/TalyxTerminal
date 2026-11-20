"""Sharadar (Nasdaq Data Link, paid): US daily prices including delisted stocks.

``datatables/SHARADAR/SEP`` answers rows ``ticker, date, open, high, low, close, volume,
closeadj, closeunadj, lastupdated`` (10,000 rows a page, ``qopts.cursor_id`` to continue).
Prices and volume are adjusted for splits (not dividends); ``closeunadj`` is the close as
traded, so Market un-adjusts each row by ``closeunadj / close`` before storing. The free
sample a Nasdaq Data Link account gets covers a few months of 2018 (AAPL 2018-09-04 to
2018-12-21, 82 rows): enough to test the interface, not to use it. The key goes in the
``api_key`` parameter and never appears in Market's records of requests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from .. import bars, calendars
from ..common import NS, MarketError, now_ns
from .base import History, Source, coverage, empty_history, row

URL = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/SEP.json"
SCOPE = {"markets": ["US"], "assetClasses": ["equity", "fund"]}


class SharadarSource(Source):
    id = "sharadar"
    descriptor = {"name": "Sharadar (Nasdaq Data Link)", "sourceIds": ["sharadar"], "kinds": ["bars"],
                  "assetClasses": ["equity", "fund"], "credentials": "required", "paid": True,
                  "supports": ["history"],
                  "settings": [{"name": "apiKey", "label": "Nasdaq Data Link API key", "type": "string",
                                "secret": True, "required": True}]}
    secret_settings = ("apiKey",)

    async def describe(self, source_id: str) -> dict:
        return coverage([row(SCOPE, "bars", [("1d", "1998-01-02", 10000)], delisted=True)],
                        concurrency=2, rpm=300, plan="SEP daily prices")

    async def configure(self, source_id: str, values: dict) -> dict:
        result = await super().configure(source_id, values)
        entry = self.market.registry.entries.get(source_id)
        if entry is not None and "apiKey" in values:
            entry.rejected = None
        return result

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        if kind != "bars" or timeframe != "1d":
            raise MarketError("unsupported", f"Sharadar gives daily bars only (asked {kind} {timeframe})",
                              source_id=self.id)
        key = await self.secret(self.id, "apiKey")
        if not key:
            raise MarketError("unauthorized", "Sharadar needs a Nasdaq Data Link API key", source_id=self.id)
        stop = min(end, now_ns())
        if stop <= start:
            return empty_history("source_has_none")
        calendar = target.instrument["calendarId"]
        params = {"ticker": target.code_at(), "date.gte": _day(start, calendar), "date.lte": _day(stop - 1, calendar)}
        rows: list[list] = []
        columns_meta = None
        cursor = None
        while True:
            query = dict(params)
            if cursor:
                query["qopts.cursor_id"] = cursor
            data = await self.egress.json(self.id, URL, params={**query, "api_key": key}, timeout=60)
            table = data.get("datatable") or {}
            columns_meta = [c["name"] for c in table.get("columns") or []] or columns_meta
            rows += table.get("data") or []
            cursor = (data.get("meta") or {}).get("next_cursor_id")
            if not cursor:
                break
        columns = bars.window(_columns(columns_meta or [], rows, calendar), start, end)
        history = History(columns)
        if not bars.length(columns):
            history.empty_reason = "source_has_none"
        return history


def _columns(names: list[str], rows: list[list], calendar: str) -> bars.Columns:
    if not rows:
        return bars.empty()
    index = {name: names.index(name) for name in ("date", "open", "high", "low", "close", "volume", "closeunadj")}
    days = np.asarray([np.datetime64(r[index["date"]], "D") for r in rows])
    opens, closes = calendars.daily_times(calendar, days)

    def column(name):
        return np.asarray([np.nan if r[index[name]] is None else float(r[index[name]]) for r in rows], dtype=np.float64)

    close, raw = column("close"), column("closeunadj")
    factor = np.where((close > 0) & ~np.isnan(raw), raw / np.where(close > 0, close, 1.0), 1.0)
    result = {"time": opens, "close_time": closes}
    for name in ("open", "high", "low"):
        result[name] = np.round(column(name) * factor, 4)
    result["close"] = np.where(np.isnan(raw), close, raw)
    result["volume"] = np.round(column("volume") / factor, 0)
    return bars.sort_unique(result)


def _day(moment: int, calendar: str) -> str:
    day = calendars.trading_day(calendar, np.asarray([moment], dtype=np.int64))[0]
    return str(day) if day is not None else datetime.fromtimestamp(moment // NS, UTC).date().isoformat()
