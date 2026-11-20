"""Yahoo Finance: US, HK and A-share bars, indices, FX, futures and yields.

Facts this code relies on (measured): Yahoo's prices, volumes and dividend amounts are
already adjusted for every later split, so rows are un-adjusted before they leave
this source; values are float32-rounded (124.80750274658203), so raw prices are
rounded to 4 decimals; depth is 30 days for 1m (8 days a request), 60 days for
5m/15m, 730 days for 1h; a symbol Yahoo does not know answers 404 or a shell
without prices; there is no multi-symbol bars endpoint.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np

from vocabulary.identity import timeframe_nanoseconds

from .. import bars, calendars
from ..codes import market_from_yahoo, yahoo_symbol
from ..common import DAY_NS, NS, MarketError, iso_of_ns, now_ns, ns_of_date
from ..routing import in_worker
from .base import History, Source, Target, coverage, empty_history, row
from .quotes import make_quote

BASE = "https://query2.finance.yahoo.com"
_INTERVAL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "60m", "1d": "1d"}
# How far back each interval reaches and how much one request may span (seconds).
_DEPTH = {"1m": 30 * 86400, "5m": 60 * 86400, "15m": 60 * 86400, "1h": 730 * 86400}
_SPAN = {"1m": 7 * 86400, "5m": 59 * 86400, "15m": 59 * 86400, "1h": 729 * 86400}
_TYPES = {"EQUITY": "equity", "ETF": "fund", "MUTUALFUND": "fund", "INDEX": "index", "CURRENCY": "fx",
          "FUTURE": "commodity", "CRYPTOCURRENCY": None}
_SECURITY = {"EQUITY": "common_stock", "ETF": "etf", "MUTUALFUND": "mutual_fund", "INDEX": "index"}
SCOPE = {"markets": ["US", "HK", "CN", "FX"]}


class YahooSource(Source):
    id = "yfinance"
    descriptor = {
        "name": "Yahoo Finance", "sourceIds": ["yfinance"],
        "kinds": ["bars", "quotes", "actions", "classification", "shares"],
        "assetClasses": ["equity", "fund", "index", "fx", "commodity", "rate"],
        "credentials": "none", "paid": False, "supports": ["search", "history", "live", "research"],
    }

    def __init__(self, market):
        super().__init__(market)
        self._events: dict[str, tuple[float, dict]] = {}
        self._crumb: tuple[float, str, dict] | None = None

    async def describe(self, source_id: str) -> dict:
        intraday = [("1m", "P30D", 2730), ("5m", "P60D", 4600), ("15m", "P60D", 1550)]
        hourly = ("1h", "P730D", 3500)
        # The day's last intraday bar is final only with the closing auction row (Hong Kong's ends by 16:10 and
        # Yahoo's Hong Kong data runs 15 minutes late): 30 minutes, taken conservatively, not measured.
        return coverage([
            row({"markets": ["US", "CN", "FX"]}, "bars", [*intraday, hourly], live="delayed", live_limit=None,
                live_periods=["1m", "5m", "15m", "1h"], publish_delay="PT30M"),
            # Hong Kong 60m bars run from :30 in the afternoon (12:30-16:30), off the exchange's 13:00-16:00 hours:
            # Market builds 1h from 15m there, 60 days deep.
            row({"markets": ["HK"]}, "bars", intraday, live="delayed", live_limit=None,
                live_periods=["1m", "5m", "15m"], publish_delay="PT30M"),
            # A daily bar is final some hours after the close: US consolidated daily data settles around 19:00-20:00
            # New York (the CSI data schedule), about four hours after the 16:00 close; asked for an hour after the
            # close, Yahoo gave unsettled bars (low above open) and no SPY bar at all. Not measured on Yahoo itself
            # (unreachable when this was set): the published schedule, taken conservatively.
            row(SCOPE, "bars", [("1d", "1962-01-02", 100000)], live="delayed", live_limit=None, live_periods=["1d"],
                publish_delay="PT4H"),
            row(SCOPE, "quotes", live="delayed"),
            row({"markets": ["US", "HK", "CN"]}, "actions", [("1d", "1962-01-02", 100000)]),
            row({"markets": ["US"]}, "classification"),
            row({"markets": ["US"]}, "shares", [("1d", "2015-10-01", 100000)]),
        ], concurrency=8, rpm=None)

    # ── search ──────────────────────────────────────────────────────────────

    async def search(self, source_id: str, query: str, scope: dict, limit: int) -> list[dict]:
        data = await self.egress.json(self.id, f"{BASE}/v1/finance/search",
                                      params={"q": query, "quotesCount": min(limit, 20), "newsCount": 0})
        candidates = []
        for quote in data.get("quotes", []):
            asset = _TYPES.get(quote.get("quoteType"))
            symbol = quote.get("symbol")
            if asset is None or not symbol:
                continue
            located = market_from_yahoo(symbol, quote.get("exchange"))
            if located is None:
                continue
            market, code = located
            if quote.get("quoteType") == "INDEX" and symbol.startswith("^") and symbol[1:4] in ("TNX", "IRX", "FVX", "TYX"):
                asset = "rate"
            candidates.append({"type": "listing", "market": market, "code": code,
                               "name": quote.get("longname") or quote.get("shortname"), "assetClass": asset,
                               "securityType": _SECURITY.get(quote.get("quoteType")), "currency": None,
                               "mic": None, "calendarId": "24_7" if asset == "fx" else None})
        return candidates

    async def has_prices(self, code: str, market: str) -> bool:
        data = await self._chart(yahoo_symbol(market, code), {"range": "5d", "interval": "1d"}, missing_ok=True)
        return bool(data and data.get("timestamp"))

    # ── history ─────────────────────────────────────────────────────────────

    async def _chart(self, symbol: str, params: dict, *, missing_ok: bool = False) -> dict | None:
        try:
            data = await self.egress.json(self.id, f"{BASE}/v8/finance/chart/{symbol}", params=params)
        except MarketError as exc:
            if exc.code == "not_found" and missing_ok:
                return None
            if exc.code == "not_found":
                raise MarketError("not_found", f"Yahoo has no symbol {symbol}", source_id=self.id, status=404) from exc
            raise
        chart = data.get("chart") or {}
        if chart.get("error"):
            if missing_ok:
                return None
            raise MarketError("not_found", f"Yahoo: {chart['error'].get('description') or chart['error']}",
                              source_id=self.id)
        result = (chart.get("result") or [None])[0]
        if result is None and not missing_ok:
            raise MarketError("not_found", f"Yahoo returned nothing for {symbol}", source_id=self.id)
        return result

    async def _later_events(self, symbol: str, start: int) -> dict:
        """Splits and dividends from ``start`` until now (for un-adjusting older rows)."""
        cached = self._events.get(symbol)
        if cached and time.monotonic() - cached[0] < 3600 and cached[1]["from"] <= start:
            return cached[1]
        data = await self._chart(symbol, {"period1": max(start // NS - 86400, 0), "period2": now_ns() // NS + 86400,
                                          "interval": "1d", "events": "div,splits"}, missing_ok=True)
        value = {"from": start, "events": (data or {}).get("events") or {}}
        self._events[symbol] = (time.monotonic(), value)
        return value

    async def history(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        market = target.market
        symbol = yahoo_symbol(market, target.code_at())
        calendar = target.instrument["calendarId"]
        if kind == "actions":
            return await self._actions(symbol, calendar, start, end)
        if kind != "bars":
            raise MarketError("unsupported", f"Yahoo has no {kind}", source_id=self.id)
        interval = _INTERVAL.get(timeframe)
        if interval is None:
            raise MarketError("unsupported", f"Yahoo has no {timeframe} bars", source_id=self.id)
        now = now_ns()
        if timeframe == "1d":
            data = await self._chart(symbol, {"period1": max(start // NS, 0) - 86400,
                                              "period2": now_ns() // NS + 86400, "interval": "1d",
                                              "events": "div,splits"})
            events = data.get("events") or {}
            self._events[symbol] = (time.monotonic(), {"from": start, "events": events})
            # Parsing ten years of rows is milliseconds of Python: in a worker, off the event loop.
            columns = await in_worker(self._daily, data, calendar, events)
            history = History(bars.window(columns, start, end))
            history.extra["actions"] = await in_worker(_actions_rows, events, calendar, columns)
            if not bars.length(history.columns):
                history.empty_reason = "source_has_none"
            return history
        depth = now - _DEPTH[timeframe] * NS + 3600 * NS
        if end <= depth:
            return empty_history("source_has_none")
        begin = max(start, depth)
        stop = min(end, begin + _SPAN[timeframe] * NS, now)
        data = await self._chart(symbol, {"period1": begin // NS, "period2": max(stop // NS, begin // NS + 60),
                                          "interval": interval})
        later = await self._later_events(symbol, begin)
        columns = await in_worker(self._intraday, data, calendar, timeframe, later["events"])
        columns = bars.window(columns, begin, stop)
        history = History(columns, next=stop if stop < end and stop < now else None)
        if not bars.length(columns) and history.next is None:
            history.empty_reason = "source_has_none"
        return history

    def _quote_columns(self, data: dict) -> tuple[np.ndarray, dict] | None:
        stamps = data.get("timestamp")
        if not stamps:
            return None
        quote = data["indicators"]["quote"][0]
        values = {name: np.asarray([np.nan if v is None else v for v in quote.get(name, [])], dtype=np.float64)
                  for name in ("open", "high", "low", "close", "volume")}
        times = np.asarray(stamps, dtype=np.int64) * NS
        keep = ~np.isnan(values["close"])
        return times[keep], {name: array[keep] for name, array in values.items()}

    def _daily(self, data: dict, calendar: str, events: dict) -> bars.Columns:
        parsed = self._quote_columns(data)
        if parsed is None:
            return bars.empty()
        times, values = parsed
        days = calendars.trading_day(calendar, times)
        days, first = np.unique(days, return_index=True)
        values = {name: array[first] for name, array in values.items()}
        opens, closes = calendars.daily_times(calendar, days)
        columns = {"time": opens, "close_time": closes, **values}
        split_times, ratios = _splits(events, calendar)
        return bars.unadjust_splits(columns, split_times, ratios)

    def _intraday(self, data: dict, calendar: str, timeframe: str, events: dict) -> bars.Columns:
        parsed = self._quote_columns(data)
        if parsed is None:
            return bars.empty()
        columns = _on_slots(*parsed, calendar, timeframe)
        # The forming bar is not closed; live data supplies it.
        columns = bars.take(columns, columns["close_time"] <= now_ns())
        split_times, ratios = _splits(events, calendar)
        return bars.unadjust_splits(columns, split_times, ratios)

    async def _actions(self, symbol: str, calendar: str, start: int, end: int) -> History:
        data = await self._chart(symbol, {"period1": max(start // NS, 0) - 86400, "period2": now_ns() // NS + 86400,
                                          "interval": "1d", "events": "div,splits"})
        events = data.get("events") or {}
        columns = await in_worker(self._daily, data, calendar, events)
        rows = [item for item in await in_worker(_actions_rows, events, calendar, columns)
                if start <= int(item["exDate"]) < end]
        history = History(bars.empty(("time",)))
        history.extra["actions"] = rows
        return history

    # ── research ────────────────────────────────────────────────────────────

    async def _session(self) -> tuple[str, dict]:
        if self._crumb and time.monotonic() - self._crumb[0] < 3000:
            return self._crumb[1], self._crumb[2]
        _status, _body, headers = await self.egress.request(self.id, "https://fc.yahoo.com/", accept_status=range(400, 600))
        cookies = {}
        for value in headers.getall("Set-Cookie", []):
            name, _, rest = value.partition("=")
            cookies[name.strip()] = rest.split(";", 1)[0]
        crumb = (await self.egress.bytes(self.id, f"{BASE}/v1/test/getcrumb", cookies=cookies)).decode().strip()
        self._crumb = (time.monotonic(), crumb, cookies)
        return crumb, cookies

    async def profile(self, code: str, market: str) -> dict:
        crumb, cookies = await self._session()
        data = await self.egress.json(self.id, f"{BASE}/v10/finance/quoteSummary/{yahoo_symbol(market, code)}",
                                      params={"modules": "assetProfile", "crumb": crumb}, cookies=cookies)
        result = ((data.get("quoteSummary") or {}).get("result") or [{}])[0]
        return result.get("assetProfile") or {}

    async def shares(self, code: str, market: str, start: int, end: int) -> list[dict]:
        """Shares outstanding as Yahoo reports them on each date: raw counts, not adjusted for later splits."""
        symbol = yahoo_symbol(market, code)
        data = await self.egress.json(
            self.id, f"{BASE}/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}",
            params={"symbol": symbol, "period1": max(start // NS, 0), "period2": min(end, now_ns()) // NS})
        rows: dict[int, dict] = {}
        for item in (data.get("timeseries") or {}).get("result") or []:
            values, stamps = item.get("shares_out") or [], item.get("timestamp") or []
            for stamp, value in zip(stamps, values, strict=False):
                if value is None:
                    continue
                day = ns_of_date(datetime.fromtimestamp(int(stamp), UTC).date())  # Yahoo stamps the date at 00:00 UTC
                rows[day] = {"date": str(day), "shares": str(int(value)), "kind": "outstanding", "knownAt": str(day)}
        return [rows[day] for day in sorted(rows)]

    async def research(self, source_id: str, target: Target, kind: str) -> list[dict]:
        code, market = target.code_at(), target.market
        if kind == "classification":
            profile = await self.profile(code, market)
            known = str(now_ns())
            rows = []
            for level in ("sector", "industry"):
                name = profile.get(level)
                if name:
                    rows.append({"scheme": "yahoo", "level": level, "code": profile.get(f"{level}Key") or name,
                                 "name": name, "from": None, "until": None, "knownAt": known})
            return rows
        if kind == "shares":
            return await self.shares(code, market, 0, now_ns())
        raise MarketError("unsupported", f"{source_id} has no {kind}", source_id=source_id)

    # ── live (polled) and quotes ────────────────────────────────────────────

    poll_seconds = {"open": 15.0, "closed": 300.0}

    def live(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str = "last",
             depth: int | None = None):
        return self._poll(target, kind, timeframe)

    async def _poll(self, target: Target, kind: str, timeframe: str | None):
        symbol = yahoo_symbol(target.market, target.code_at())
        calendar = target.instrument["calendarId"]
        if kind == "quotes":
            announced = False
            while True:
                data = await self._chart(symbol, {"range": "1d", "interval": "1d"})
                if not announced:
                    announced = True
                    yield {"type": "subscribed", "mode": "delayed"}
                yield {"type": "ticker", "quote": _quote_of_meta(data.get("meta") or {})}
                await asyncio.sleep(self._pause(calendar))
            return
        if kind != "bars" or timeframe not in _INTERVAL:
            raise MarketError("unsupported", f"Yahoo has no live {kind} {timeframe or ''}".strip(), source_id=self.id)
        intraday = timeframe != "1d"
        announced = False
        while True:
            data = await self._chart(symbol, {"range": "1d" if intraday else "5d", "interval": _INTERVAL[timeframe]})
            columns = self._live_rows(data, calendar, timeframe)
            if not announced:
                announced = True
                yield {"type": "subscribed", "mode": "delayed"}
            now = now_ns()
            for i in range(bars.length(columns)):
                row_value = {name: (int(values[i]) if name in ("time", "close_time") else float(values[i]))
                             for name, values in columns.items()}
                yield {"type": "bar", "row": row_value, "closed": row_value["close_time"] <= now}
            await asyncio.sleep(self._pause(calendar))

    def _pause(self, calendar: str) -> float:
        now = now_ns()
        opens, closes = calendars.bar_grid(calendar, "1d", now - 2 * DAY_NS, now + DAY_NS) \
            if calendars.canonical(calendar) != calendars.CONTINUOUS else (np.array([now]), np.array([now + 1]))
        trading = bool(np.any((opens <= now) & (closes > now)))
        return self.poll_seconds["open" if trading else "closed"]

    def _live_rows(self, data: dict, calendar: str, timeframe: str) -> bars.Columns:
        if timeframe == "1d":
            return self._daily(data, calendar, {})
        parsed = self._quote_columns(data)
        if parsed is None:
            return bars.empty()
        return _on_slots(*parsed, calendar, timeframe)

    async def quotes(self, source_id: str, targets: list[Target], basis: str) -> dict[str, dict]:
        async def one(target: Target):
            symbol = yahoo_symbol(target.market, target.code_at())
            try:
                data = await self._chart(symbol, {"range": "1d", "interval": "1d"})
                return target.instrument["instrumentId"], {"quote": _quote_of_meta(data.get("meta") or {})}
            except MarketError as exc:
                return target.instrument["instrumentId"], {"problem": exc.problem()}
        return dict(await asyncio.gather(*(one(target) for target in targets)))

    async def quote(self, codes: list[tuple[str, str]]) -> dict[str, dict]:
        """Last price and previous close for (market, code) pairs from the chart meta."""
        async def one(market, code):
            data = await self._chart(yahoo_symbol(market, code), {"range": "1d", "interval": "1d"}, missing_ok=True)
            return (market, code), (data or {}).get("meta")
        results = await asyncio.gather(*(one(m, c) for m, c in codes), return_exceptions=True)
        return {key: meta for item in results if not isinstance(item, BaseException) for key, meta in [item]}


def _on_slots(times: np.ndarray, values: dict, calendar: str, timeframe: str) -> bars.Columns:
    """Yahoo's regular-session rows on the exchange's own bar slots.

    A row after the session's last slot, on the same day, is the closing auction (the 16:08 row in Hong Kong
    1m data; the zero-volume 16:00 row, or 13:00 on a US half day, whose 12:30 row is empty) and joins that last
    slot, so the day's last bar closes at the auction price. During a session the last row is the latest trade
    (17:37:22 after the 17:30 row, volume 0) and joins the slot it falls in. Any other row off a slot means this
    interval does not fit the market's bars (Yahoo's 60m bars in Hong Kong run from :30 in the afternoon)."""
    step = timeframe_nanoseconds(timeframe)
    if calendars.canonical(calendar) == calendars.CONTINUOUS:
        return {"time": times, "close_time": times + step, **values}
    opens, closes = calendars.bar_grid(calendar, timeframe, int(times[0]), int(times[-1]) + 1)
    slot = np.searchsorted(opens, times, "right") - 1
    found = slot >= 0
    slot = np.maximum(slot, 0)
    key, close = opens[slot], closes[slot]
    day = calendars.trading_day(calendar, key)
    auction = (times >= close) & (close == calendars.session_closes(calendar, day)) \
        & (calendars.trading_day(calendar, times) == day)
    latest = np.zeros(len(times), dtype=bool)
    latest[-1] = times[-1] < close[-1]
    off = ~found | ~((key == times) | auction | latest)
    if off.any():
        raise MarketError("unsupported", f"Yahoo's {timeframe} bar at {iso_of_ns(int(times[off][0]))} is off the "
                          f"{calendars.canonical(calendar)} bar times", source_id="yfinance")
    return bars.group({"time": times, "close_time": close, **values}, key, close)


def _quote_of_meta(meta: dict) -> dict:
    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose") if meta.get("previousClose") is None else meta.get("previousClose")
    # Differences in Decimal of the printed values: a float difference carries noise at the price's own scale.
    change = Decimal(str(price)) - Decimal(str(previous)) if price is not None and previous else None
    stamp = meta.get("regularMarketTime")
    return make_quote(price=price, last=price, change=change,
                      change_percent=None if change is None else change / Decimal(str(previous)) * 100,
                      basis="previous_session_close" if change is not None else None,
                      as_of=int(stamp) * NS if stamp else None, delayed=True)


def _splits(events: dict, calendar: str) -> tuple[np.ndarray, np.ndarray]:
    items = list((events.get("splits") or {}).values())
    if not items:
        return np.empty(0, np.int64), np.empty(0)
    days = calendars.trading_day(calendar, np.asarray([int(i["date"]) * NS for i in items], dtype=np.int64))
    times = calendars.local_midnight(calendar, days)
    ratios = np.asarray([float(i["numerator"]) / float(i["denominator"]) for i in items])
    return times, ratios


def _actions_rows(events: dict, calendar: str, raw_daily: bars.Columns) -> list[dict]:
    """Splits and cash dividends; dividend amounts are un-adjusted for later splits."""
    split_times, ratios = _splits(events, calendar)
    rows = []
    for ex, ratio in zip(split_times.tolist(), ratios.tolist(), strict=True):
        rows.append({"exDate": str(ex), "kind": "split", "ratio": _text(ratio), "amount": None, "knownAt": str(ex)})
    for item in (events.get("dividends") or {}).values():
        day = calendars.trading_day(calendar, np.asarray([int(item["date"]) * NS], dtype=np.int64))
        ex = int(calendars.local_midnight(calendar, day)[0])
        later = ratios[split_times > ex].prod() if len(ratios) else 1.0
        rows.append({"exDate": str(ex), "kind": "cash_dividend", "ratio": None,
                     "amount": _text(round(float(item["amount"]) * later, 6)), "knownAt": str(ex)})
    rows.sort(key=lambda r: (int(r["exDate"]), r["kind"]))
    return rows


def _text(value: float) -> str:
    return np.format_float_positional(value, trim="-")


def ns_from_date(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()) * NS + 0 * DAY_NS
