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

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from itertools import batched

import numpy as np
from talyx_sdk.i18n import mark, t

from .. import bars, calendars
from ..common import NS, MarketError, now_ns
from .base import RPM_SETTING, History, MeteredSource, coverage, empty_history, row

URL = "https://data.nasdaq.com/api/v3/datatables/SHARADAR"
SCOPE = {"markets": ["US"], "assetClasses": ["equity"]}


class SharadarSource(MeteredSource):
    catalog_family = "delisted_listings"
    default_rpm = 200  # 2,000 calls per ten minutes with a free Nasdaq Data Link key
    id = "sharadar"
    descriptor = {"name": "Sharadar (Nasdaq Data Link)", "sourceIds": ["sharadar"], "kinds": ["bars", "listings"],
                  "assetClasses": ["equity"], "credentials": "required", "paid": True,
                  "supports": ["history", "catalog"],
                  "settings": [RPM_SETTING, {"name": "apiKey", "label": mark("Nasdaq Data Link API key"), "type": "string",
                                "secret": True, "required": True}]}
    secret_settings = ("apiKey",)

    async def describe(self, source_id: str) -> dict:
        return coverage([row(SCOPE, "bars", [("1d", "1998-01-02", 10000)], delisted=True),
                         row(SCOPE, "listings", delisted=True)],
                        concurrency=1, rpm=self.rpm, plan="SEP daily prices")

    async def _page(self, table, params, cursor=None):
        key = await self.secret(self.id, "apiKey")
        if not key:
            raise MarketError("unauthorized", t("Sharadar needs a Nasdaq Data Link API key"), source_id=self.id)
        params = {**params, "api_key": key}
        if cursor:
            params["qopts.cursor_id"] = cursor
        data = await self.request(self.egress.json, f"{URL}/{table}.json", params=params, timeout=60)
        answer = data["datatable"]
        return [c["name"] for c in answer["columns"]], answer["data"], data["meta"]["next_cursor_id"]

    async def catalog(self, source_id, scope, environment, cursor):
        columns = "ticker,permaticker,name,isdelisted,firstpricedate,lastpricedate,relatedtickers"
        fields, rows, following = await self._page("TICKERS", {"table": "SEP", "qopts.columns": columns}, cursor)
        tickers = [dict(zip(fields, values, strict=True)) for values in rows if values[fields.index("isdelisted")] == "Y"]
        changes = defaultdict(list)
        for group in batched((item["ticker"] for item in tickers if item.get("relatedtickers")), 100):
            page = None
            while True:
                fields, values, page = await self._page("ACTIONS", {"action": "tickerchangefrom",
                                                        "ticker": ",".join(group)}, page)
                for value in values:
                    action = dict(zip(fields, value, strict=True))
                    changes[action["ticker"]].append((action["date"], action["contraticker"]))
                if not page:
                    break
        candidates = []
        for item in tickers:
            first, last = item["firstpricedate"], item["lastpricedate"]
            if not first or not last:
                continue
            # Nasdaq's SEP symbols are name-adjusted. Recycled exchange codes carry a numeric suffix.
            ticker = item["ticker"]
            until = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
            begin = first
            for stop, symbol in [*sorted(changes[ticker]), (until, ticker)]:
                if stop <= begin or stop > until:
                    continue
                candidates.append({"type": "listing", "market": "US", "code": symbol.rstrip("0123456789"),
                                   "name": item["name"], "assetClass": "equity", "currency": "USD",
                                   "calendarId": "XNYS", "sourceKey": str(item["permaticker"]),
                                   "listedFrom": first, "delistedAt": until,
                                   "codeFrom": begin, "codeUntil": stop})
                begin = stop
        date_fields = ("listedFrom", "delistedAt", "codeFrom", "codeUntil")
        days = sorted({item[field] for item in candidates for field in date_fields})
        stamps = calendars.local_midnight("XNYS", np.array(days, dtype="datetime64[D]"))
        midnights = dict(zip(days, map(str, stamps), strict=True))
        for item in candidates:
            for field in date_fields:
                item[field] = midnights[item[field]]
        return candidates, following

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        if kind != "bars" or timeframe != "1d":
            raise MarketError("unsupported", t("Sharadar gives daily bars only (asked {kind} {timeframe})", kind=kind, timeframe=timeframe),
                              source_id=self.id)
        stop = min(end, now_ns())
        if stop <= start:
            return empty_history("source_has_none")
        calendar = target.instrument["calendarId"]
        ticker = target.code_at()
        permanent = target.source_key
        if permanent:
            fields, rows, _ = await self._page("TICKERS", {"table": "SEP", "permaticker": permanent,
                                                           "qopts.columns": "ticker,permaticker"})
            ticker = next((r[fields.index("ticker")] for r in rows
                           if str(r[fields.index("permaticker")]) == permanent), None)
            if ticker is None:
                return empty_history("source_has_none")
        params = {"ticker": ticker, "date.gte": _day(start, calendar), "date.lte": _day(stop - 1, calendar)}
        rows: list[list] = []
        cursor = None
        while True:
            columns_meta, page, cursor = await self._page("SEP", params, cursor)
            rows.extend(page)
            if not cursor:
                break
        columns = bars.window(_columns(columns_meta, rows, calendar), start, end)
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
