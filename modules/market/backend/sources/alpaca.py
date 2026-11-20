"""Alpaca market data (free key): US stock bars from 2016 and the code changes behind renames.

Facts this code relies on (measured 11-10 with a free key): ``/v2/stocks/bars`` takes several
symbols, ``limit`` up to 10,000 bars counted over all symbols and ``next_page_token``; bars are
stamped at their start in UTC (daily bars at New York midnight); ``adjustment=raw`` gives
prices and volumes as traded; intraday bars include pre- and after-market trading; hourly bars
are clock-aligned (13:00Z), not session-aligned, so Market takes 1m, 5m and 15m and builds
hours from them. The free plan serves SIP history except the latest 15 minutes (a bar is
known 15 minutes after its close: ``publishDelay``). ``/v1/corporate-actions`` with
``types=name_change`` lists every rename (old and new symbol, CUSIP, process date) since
2017-04 in four pages: Market keeps each listing's code periods from it (FB until
2022-06-09, META from then). Keys are two secrets (key id and secret key).
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from vocabulary.identity import timeframe_nanoseconds

from .. import bars, calendars
from ..codes import alpaca_symbol
from ..common import DAY_NS, NS, MarketError, now_ns
from .base import History, Source, coverage, empty_history, row

DATA = "https://data.alpaca.markets"
SCOPE = {"markets": ["US"], "assetClasses": ["equity", "fund"]}
FIRST = "2016-01-04"
_TIMEFRAME = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1d": "1Day"}
_SPAN = {"1m": 7 * DAY_NS, "5m": 30 * DAY_NS, "15m": 90 * DAY_NS, "1d": 40 * 365 * DAY_NS}
DELAY = 15 * 60 * NS


class AlpacaSource(Source):
    id = "alpaca"
    descriptor = {"name": "Alpaca", "sourceIds": ["alpaca"], "kinds": ["bars", "code_history"],
                  "assetClasses": ["equity", "fund"], "credentials": "required", "paid": False,
                  "supports": ["history"],
                  "settings": [{"name": "keyId", "label": "API key id", "type": "string", "secret": True,
                                "required": True},
                               {"name": "secretKey", "label": "API secret key", "type": "string", "secret": True,
                                "required": True}]}
    secret_settings = ("keyId", "secretKey")

    async def describe(self, source_id: str) -> dict:
        periods = [(tf, FIRST, 10000) for tf in ("1m", "5m", "15m", "1d")]
        return coverage([row(SCOPE, "bars", periods, publish_delay="PT15M"),
                         row({"markets": ["US"]}, "code_history")], concurrency=4, rpm=200)

    async def _headers(self) -> dict:
        key, secret = await self.secret(self.id, "keyId"), await self.secret(self.id, "secretKey")
        if not key or not secret:
            raise MarketError("unauthorized", "Alpaca needs its key id and secret key in its settings",
                              source_id=self.id)
        return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    async def _get(self, path: str, params: dict) -> dict:
        return await self.egress.json(self.id, DATA + path, params=params, headers=await self._headers(), timeout=60)

    # ── bars ────────────────────────────────────────────────────────────────

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        if kind != "bars" or timeframe not in _TIMEFRAME:
            raise MarketError("unsupported", f"Alpaca has no {kind} {timeframe or ''}".strip(), source_id=self.id)
        first = int(calendars.local_midnight("XNYS", np.asarray([np.datetime64(FIRST, "D")]))[0])
        stop = min(end, now_ns() - DELAY)
        begin = max(start, first)
        if stop <= begin:
            return empty_history("source_has_none")
        chunk_end = min(stop, begin + _SPAN[timeframe])
        symbol = alpaca_symbol(target.code_at())
        items: list[dict] = []
        token = None
        while True:
            params = {"symbols": symbol, "timeframe": _TIMEFRAME[timeframe], "start": _rfc3339(begin),
                      "end": _rfc3339(chunk_end), "limit": 10000, "adjustment": "raw", "feed": "sip"}
            if token:
                params["page_token"] = token
            data = await self._get("/v2/stocks/bars", params)
            items += (data.get("bars") or {}).get(symbol) or []
            token = data.get("next_page_token")
            if not token:
                break
        calendar = target.instrument["calendarId"]
        columns = self._daily(items, calendar) if timeframe == "1d" else self._intraday(items, calendar, timeframe)
        columns = bars.window(columns, begin, chunk_end)
        history = History(columns, next=chunk_end if chunk_end < stop else None)
        if not bars.length(columns) and history.next is None:
            history.empty_reason = "source_has_none"
        return history

    @staticmethod
    def _values(items: list[dict]) -> tuple[np.ndarray, dict]:
        times = np.asarray([_ns(item["t"]) for item in items], dtype=np.int64)
        values = {name: np.asarray([float(item[key]) for item in items], dtype=np.float64)
                  for name, key in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"))}
        return times, values

    def _daily(self, items: list[dict], calendar: str) -> bars.Columns:
        if not items:
            return bars.empty()
        times, values = self._values(items)
        days = calendars.trading_day(calendar, times)
        opens, closes = calendars.daily_times(calendar, days)
        return bars.sort_unique({"time": opens, "close_time": closes, **values})

    def _intraday(self, items: list[dict], calendar: str, timeframe: str) -> bars.Columns:
        """Regular-session bars only (Alpaca also sends pre- and after-market trading)."""
        if not items:
            return bars.empty()
        times, values = self._values(items)
        step = timeframe_nanoseconds(timeframe)
        grid_open, grid_close = calendars.bar_grid(calendar, timeframe, int(times[0]), int(times[-1]) + step)
        slot = np.searchsorted(grid_open, times)
        inside = (slot < len(grid_open)) & (grid_open[np.minimum(slot, len(grid_open) - 1)] == times)
        slot = slot[inside]
        columns = {"time": times[inside], "close_time": grid_close[slot],
                   **{name: array[inside] for name, array in values.items()}}
        columns = bars.sort_unique(columns)
        return bars.take(columns, columns["close_time"] <= now_ns())

    # ── renames ─────────────────────────────────────────────────────────────

    async def code_history(self, source_id: str) -> list[dict]:
        """Every rename Alpaca knows: (market, old code, new code, from)."""
        changes, token = [], None
        today = datetime.now(UTC).date().isoformat()
        while True:
            params = {"types": "name_change", "start": "2016-01-01", "end": today, "limit": 1000}
            if token:
                params["page_token"] = token
            data = await self._get("/v1/corporate-actions", params)
            for item in (data.get("corporate_actions") or {}).get("name_changes") or []:
                old, new, day = item.get("old_symbol"), item.get("new_symbol"), item.get("process_date")
                if old and new and day and old != new:
                    at = int(calendars.local_midnight("XNYS", np.asarray([np.datetime64(day, "D")]))[0])
                    changes.append({"market": "US", "old": old, "new": new, "at": str(at),
                                    "evidence": item.get("new_cusip") or item.get("id")})
            token = data.get("next_page_token")
            if not token:
                break
        return changes


def _rfc3339(moment: int) -> str:
    return datetime.fromtimestamp(moment // NS, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ns(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()) * NS
