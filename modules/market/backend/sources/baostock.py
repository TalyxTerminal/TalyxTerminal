"""Baostock: the A-share list with delisted stocks, daily and 5/15/30/60-minute bars,
adjustment factors with their dividend details, CSRC industries and quarterly share counts.

Facts this code relies on (measured 11-09 and 11-10): the library speaks a line protocol
over one TCP connection to port 10030 and keeps the socket in module globals, so Market
holds one session for the process, logs in once and runs every query on one thread, one
at a time (``network.patch_baostock`` tunnels the connection through the proxy). Queries
page by 2,000 rows inside the library. The list (``query_stock_basic``) has delisted
stocks with ipoDate and outDate and no Beijing listings. Minute bars start 2020-01-02 and
are labelled by their end time (Beijing time); suspended days come back as rows with
tradestatus 0 and empty prices. ``query_adjust_factor`` gives every ex-date's cumulative
factors in one request; ``query_dividend_data`` gives one year's cash and stock
dividends; ``query_stock_industry(date=…)`` gives the whole market's CSRC industry at a
date in one query; share counts come only per stock and quarter (``query_profit_data``).
After an A-share code change both codes carry the full history and the new code's
ipoDate is the old one's (000043 → 001914): ``after_catalog`` finds such pairs and has
Market merge them.
"""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np

from vocabulary.identity import timeframe_nanoseconds

from .. import bars, calendars, network
from ..codes import baostock_code
from ..common import DAY_NS, NS, MarketError, now_ns
from ..routing import in_worker
from .base import History, Source, coverage, empty_history, row

CALENDAR = "XSHG"
SCOPE = {"markets": ["CN"]}
_FREQUENCY = {"5m": "5", "15m": "15", "30m": "30", "1h": "60", "1d": "d"}
_MINUTES_FROM = "2020-01-02"
_MINUTE_SPAN = 366 * DAY_NS
_LOST = {"10001001", "10002001", "10002002", "10002003", "10002004", "10002005", "10002006", "10002007",
         "10002008"}
_TYPES = {"1": ("equity", "common_stock"), "2": ("index", "index"), "5": ("fund", "etf")}
CODE_CHANGE = "code_change"
# Daily rows compared on both codes of a candidate code change: the weeks before the old code ended.
_CHECK_DAYS = 20


class _Lost(Exception):
    """The Baostock connection answered nothing."""


def _quiet(*_args, **_kwargs) -> None:
    return None


class BaostockClient:
    """One Baostock session for the process: one login, one query at a time, on one thread."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(1, thread_name_prefix="talyx-baostock")
        self._logged_in = False

    async def query(self, name: str, *args) -> tuple[list[str], list[list[str]]]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._run, name, args)

    def _prepare(self):
        import baostock
        import baostock.util.socketutil as socketutil

        network.patch_baostock()
        for name, module in list(sys.modules.items()):
            if name.startswith("baostock") and module is not None:
                module.print = _quiet  # the library prints progress and errors to stdout
        if not getattr(socketutil.send_msg, "talyx", False):
            original = socketutil.send_msg

            def send(message):
                answer = original(message)
                if answer is None or not answer.strip():
                    raise _Lost()
                return answer

            send.talyx = True
            socketutil.send_msg = send
        return baostock

    def _run(self, name: str, args: tuple):
        baostock = self._prepare()
        for attempt in (0, 1):
            try:
                if not self._logged_in:
                    result = baostock.login()
                    if result.error_code != "0":
                        raise _error(result.error_code, result.error_msg)
                    self._logged_in = True
                result = getattr(baostock, name)(*args)
                rows = []
                if result.error_code == "0":
                    while result.next():
                        rows.append(result.get_row_data())
                    if result.error_code == "0":
                        return list(result.fields or []), rows
                if result.error_code in _LOST and attempt == 0:
                    self._logged_in = False
                    continue
                raise _error(result.error_code, result.error_msg)
            except (_Lost, OSError) as exc:
                self._logged_in = False
                if attempt == 0:
                    continue
                raise MarketError("source_unavailable", f"Baostock connection failed: {exc or 'no answer'}",
                                  source_id="baostock") from exc
        raise MarketError("source_unavailable", "Baostock gave no answer", source_id="baostock")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def _error(code: str, message: str) -> MarketError:
    if code in ("10004011", "10004015"):
        return MarketError("not_found", f"Baostock: {message}", source_id="baostock")
    if code in ("10001006", "10001011"):
        return MarketError("forbidden", f"Baostock: {message}", source_id="baostock")
    return MarketError("source_unavailable", f"Baostock {code}: {message}", source_id="baostock")


def market_code(bcode: str) -> str:
    exchange, number = bcode.split(".")
    return f"{number}.{exchange.upper()}"


def day_ns(text: str) -> int:
    """A Beijing trading date as its local midnight."""
    return int(calendars.local_midnight(CALENDAR, np.asarray([np.datetime64(text, "D")]))[0])


def _date_text(moment: int) -> str:
    """The Beijing date holding ``moment``."""
    return str((np.datetime64(moment + 8 * 3600 * NS, "ns")).astype("datetime64[D]"))


def _float(values: list[str]) -> np.ndarray:
    return np.asarray([np.nan if v in ("", None) else float(v) for v in values], dtype=np.float64)


class BaostockSource(Source):
    id = "baostock"
    descriptor = {"name": "Baostock", "sourceIds": ["baostock"],
                  "kinds": ["listings", "bars", "actions", "classification", "shares"],
                  "assetClasses": ["equity", "fund", "index"], "credentials": "none", "paid": False,
                  "supports": ["catalog", "history", "research"]}

    def __init__(self, market):
        super().__init__(market)
        self.client = BaostockClient()
        self._basic: list[list[str]] | None = None

    async def describe(self, source_id: str) -> dict:
        minutes = [(tf, _MINUTES_FROM, 12000) for tf in ("5m", "15m", "30m", "1h")]
        return coverage([
            row(SCOPE, "listings", delisted=True),
            row(SCOPE, "bars", [*minutes, ("1d", "1990-12-19", 100000)], delisted=True),
            row(SCOPE, "actions", [("1d", "1990-12-19", 100000)], delisted=True),
            row(SCOPE, "classification", delisted=True),
            row(SCOPE, "shares", [("1d", "2007-03-31", 1)], delisted=True),
        ], concurrency=1, rpm=None)

    async def close(self) -> None:
        self.client.close()

    # ── list ────────────────────────────────────────────────────────────────

    async def catalog(self, source_id: str, scope: dict, environment: str, cursor: str | None):
        _fields, rows = await self.client.query("query_stock_basic")
        self._basic = rows
        changes = await in_worker(self.market.identity.segments_of, "CN", CODE_CHANGE)
        merged_old = {old for old, value in changes.items() if value.get("newCode")}
        starts = {value["newCode"]: value["until"] for value in changes.values() if value.get("newCode")}
        candidates = []
        for bcode, name, ipo, out, kind, status in (r[:6] for r in rows):
            if kind not in _TYPES or bcode in merged_old:
                continue
            asset, security = _TYPES[kind]
            code = market_code(bcode)
            start = day_ns(ipo) if ipo else None
            until = day_ns(out) if out and status == "0" else None
            listed = start
            if bcode in starts:
                start = int(starts[bcode])  # the new code of a code change starts where the old one ended
            candidates.append({
                "type": "listing", "market": "CN", "code": code, "name": name, "assetClass": asset,
                "securityType": security, "currency": "CNY", "mic": "XSHG" if code.endswith(".SH") else "XSHE",
                "calendarId": CALENDAR, "sourceKey": bcode,
                "codeFrom": None if start is None else str(start), "codeUntil": None if until is None else str(until),
                "listedFrom": None if listed is None else str(listed),
                "delistedAt": None if until is None else str(until)})
        return candidates, None

    # ── code changes ────────────────────────────────────────────────────────

    async def after_catalog(self, source_id: str, venue: str, family: str) -> None:
        """Find A-share code changes: a delisted code and a listed one with the same listing date whose
        daily rows before the old code ended are the same. They become one instrument."""
        if venue != "CN" or not self._basic:
            return
        rows = self._basic
        checked = await in_worker(self.market.identity.segments_of, "CN", CODE_CHANGE)
        listed: dict[str, list[str]] = {}
        for item in rows:
            if item[4] == "1" and item[5] == "1" and item[2]:
                listed.setdefault(item[2], []).append(item[0])
        for bcode, name, ipo, out, kind, status in (r[:6] for r in rows):
            if kind != "1" or status != "0" or not out or not ipo or bcode in checked:
                continue
            if "退" in name:  # delisted for cause, not a code change
                continue
            found = None
            if listed.get(ipo):
                found = await self._same_rows(bcode, listed[ipo], out)
            value = {"newCode": found, "until": str(day_ns(out)), "listedFrom": str(day_ns(ipo))}
            await in_worker(self.market.identity.save_code_change, "CN", bcode, value)
            if found:
                await self._merge(bcode, found, value)

    async def _same_rows(self, old: str, candidates: list[str], out: str) -> str | None:
        end = (date.fromisoformat(out) - timedelta(days=1)).isoformat()
        begin = (date.fromisoformat(out) - timedelta(days=_CHECK_DAYS)).isoformat()
        fields = "date,open,high,low,close,volume,tradestatus"
        _f, rows = await self.client.query("query_history_k_data_plus", old, fields, begin, end, "d", "3")
        reference = {r[0]: r[1:6] for r in rows if r[6] == "1"}
        if not reference:
            return None
        for candidate in candidates:
            _f, other = await self.client.query("query_history_k_data_plus", candidate, fields, begin, end, "d", "3")
            mine = {r[0]: r[1:6] for r in other if r[6] == "1"}
            if mine == reference:
                return candidate
        return None

    async def _merge(self, old: str, new: str, value: dict) -> None:
        identity = self.market.identity
        old_id = await in_worker(identity.find_source_key, self.id, old)
        new_id = await in_worker(identity.find_source_key, self.id, new)
        if old_id is None or new_id is None or old_id == new_id:
            return
        until = int(value["until"])
        periods = [(market_code(old), int(value["listedFrom"]), until), (market_code(new), until, None)]
        await self.market.merge_listings(new_id, old_id, market="CN", periods=periods)

    # ── history ─────────────────────────────────────────────────────────────

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        code = baostock_code(target.code_at())
        if kind == "actions":
            return await self._actions(code, start, end)
        if kind != "bars" or timeframe not in _FREQUENCY:
            raise MarketError("unsupported", f"Baostock has no {kind} {timeframe or ''}".strip(), source_id=self.id)
        now = now_ns()
        stop = min(end, now)
        if timeframe == "1d":
            if stop <= start:
                return empty_history("source_has_none")
            fields = "date,open,high,low,close,volume,tradestatus"
            _f, rows = await self.client.query("query_history_k_data_plus", code, fields, _date_text(start),
                                               _date_text(stop - 1), "d", "3")
            rows = [r for r in rows if r[6] == "1" and r[4]]
            columns = self._daily(rows)
            history = History(bars.window(columns, start, end))
            if not bars.length(history.columns):
                history.empty_reason = "source_has_none"
            return history
        first = day_ns(_MINUTES_FROM)
        if stop <= first:
            return empty_history("source_has_none")
        begin = max(start, first)
        chunk_end = min(stop, begin + _MINUTE_SPAN)
        fields = "date,time,open,high,low,close,volume"
        _f, rows = await self.client.query("query_history_k_data_plus", code, fields, _date_text(begin),
                                           _date_text(chunk_end - 1), _FREQUENCY[timeframe], "3")
        columns = bars.window(self._minutes(rows, timeframe), begin, chunk_end)
        history = History(columns, next=chunk_end if chunk_end < stop else None)
        if not bars.length(columns) and history.next is None:
            history.empty_reason = "source_has_none"
        return history

    def _daily(self, rows: list[list[str]]) -> bars.Columns:
        if not rows:
            return bars.empty()
        days = np.asarray([np.datetime64(r[0], "D") for r in rows])
        opens, closes = calendars.daily_times(CALENDAR, days)
        columns = {"time": opens, "close_time": closes}
        for index, name in enumerate(("open", "high", "low", "close", "volume"), start=1):
            columns[name] = _float([r[index] for r in rows])
        return bars.sort_unique(columns)

    def _minutes(self, rows: list[list[str]], timeframe: str) -> bars.Columns:
        rows = [r for r in rows if r[5]]
        if not rows:
            return bars.empty()
        step = timeframe_nanoseconds(timeframe)
        # Labels are the bar's end in Beijing time (UTC+8, no daylight saving since 1991).
        local = np.asarray([np.datetime64(f"{t[0:4]}-{t[4:6]}-{t[6:8]}T{t[8:10]}:{t[10:12]}:{t[12:14]}", "ns")
                            for t in (r[1] for r in rows)]).astype(np.int64)
        close_times = local - 8 * 3600 * NS
        columns = {"time": close_times - step, "close_time": close_times}
        for index, name in enumerate(("open", "high", "low", "close", "volume"), start=2):
            columns[name] = _float([r[index] for r in rows])
        columns = bars.sort_unique(columns)
        return bars.take(columns, columns["close_time"] <= now_ns())

    async def _actions(self, code: str, start: int, end: int) -> History:
        """Ex-dates with Baostock's own factor, split from its dividend details: ``factor`` rows carry the
        stock-dividend ratio and the remaining (cash) factor; cash and stock dividend rows are kept for reading."""
        _f, factors = await self.client.query("query_adjust_factor", code, "1990-01-01", _date_text(now_ns()))
        rows: list[dict] = []
        previous = None
        events = []
        for item in factors:
            back = float(item[3])
            if previous is not None and back > 0:
                events.append((item[1], previous / back))
            previous = back
        events = [(day, multiplier) for day, multiplier in events if start <= day_ns(day) < end]
        details: dict[str, list[str]] = {}
        for year in sorted({day[:4] for day, _m in events}):
            _f, dividends = await self.client.query("query_dividend_data", code, year, "operate")
            for item in dividends:
                if item[6]:
                    details[item[6]] = item
        for day, multiplier in events:
            ex = str(day_ns(day))
            detail = details.get(day)
            ratio = 1.0
            if detail is not None:
                ratio = 1.0 + _number(detail[11]) + _number(detail[13])
                cash = _number(detail[9])
                if cash:
                    rows.append({"exDate": ex, "kind": "cash_dividend", "ratio": None, "amount": _text(cash),
                                 "currency": "CNY", "recordDate": _day_text(detail[5]), "payDate": _day_text(detail[7]),
                                 "knownAt": ex})
                if ratio != 1.0:
                    rows.append({"exDate": ex, "kind": "stock_dividend", "ratio": _text(ratio), "amount": None,
                                 "knownAt": ex})
            rows.append({"exDate": ex, "kind": "factor", "ratio": _text(ratio),
                         "dividendFactor": _text(round(multiplier * ratio, 10)), "basis": "source", "knownAt": ex})
        history = History(bars.empty(("time",)))
        history.extra["actions"] = sorted(rows, key=lambda r: (int(r["exDate"]), r["kind"]))
        return history

    # ── research ────────────────────────────────────────────────────────────

    async def research(self, source_id: str, target, kind: str) -> list[dict]:
        code = baostock_code(target.code_at())
        if kind == "classification":
            _f, rows = await self.client.query("query_stock_industry", code, "")
            return [_industry_row(item)[1] for item in rows]
        raise MarketError("unsupported", f"Baostock gives {kind} only in parts", source_id=source_id)

    def research_parts(self, source_id: str, target, kind: str, start: int | None, end: int | None) -> list[str] | None:
        """Share counts come per quarter: one part (and one request) per quarter in [start, end)."""
        if kind != "shares":
            return None
        stop = min(end or now_ns(), now_ns())
        begin = start if start is not None else stop - 2 * 365 * DAY_NS
        first = int(target.instrument["listedFrom"]) if target.instrument.get("listedFrom") else begin
        begin = max(begin, first, day_ns("2007-01-01"))
        parts = []
        day = date.fromisoformat(_date_text(begin))
        year, quarter = day.year, (day.month - 1) // 3 + 1
        while True:
            quarter_start = date(year, quarter * 3 - 2, 1)
            if quarter_start.isoformat() >= _date_text(stop):
                break
            parts.append(f"{year}Q{quarter}")
            year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
        return parts

    async def research_part(self, source_id: str, target, kind: str, part: str) -> list[dict]:
        code = baostock_code(target.code_at())
        year, quarter = part.split("Q")
        _f, rows = await self.client.query("query_profit_data", code, int(year), int(quarter))
        out = []
        for item in rows:
            stat, published = item[2], item[1]
            if not stat:
                continue
            known = str(day_ns(published)) if published else str(day_ns(stat))
            for index, share_kind in ((9, "outstanding"), (10, "float")):
                if item[index]:
                    out.append({"date": str(day_ns(stat)), "shares": str(int(float(item[index]))), "kind": share_kind,
                                "knownAt": known})
        return out

    async def research_market(self, source_id: str, market: str, kind: str, at: int) -> list[dict]:
        """The whole market's CSRC industry at a date, in one query; rows carry their code."""
        if kind != "classification" or market != "CN":
            raise MarketError("unsupported", f"Baostock has no market-wide {kind} for {market}", source_id=source_id)
        _f, rows = await self.client.query("query_stock_industry", "", _date_text(at))
        return [{"stock": stock, **value} for stock, value in map(_industry_row, rows)]


def _industry_row(item: list[str]) -> tuple[str, dict]:
    updated, bcode, _name, industry, scheme = item[:5]
    code = name = None
    if industry:
        split = next((i for i, ch in enumerate(industry) if not (ch.isascii() and ch.isalnum())), len(industry))
        code, name = industry[:split] or None, industry[split:] or industry
    known = str(day_ns(updated)) if updated else str(now_ns())
    return market_code(bcode), {"scheme": "csrc" if scheme == "证监会行业分类" else scheme, "level": "industry",
                                "code": code, "name": name, "from": None, "until": None, "knownAt": known}


def _number(text: str | None) -> float:
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def _text(value: float) -> str:
    return np.format_float_positional(round(value, 10), trim="-")


def _day_text(text: str | None) -> str | None:
    return str(day_ns(text)) if text else None
