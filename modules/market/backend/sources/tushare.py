"""Tushare Pro: A-share daily bars, and the whole market's day in one request.

Every call is one HTTP POST to ``api.tushare.pro`` naming the API, the user's token and
parameters; answers are ``{code, msg, data: {fields, items}}``. The free tier (120
points) has unadjusted daily bars (``daily``) at 50 requests a minute and 8,000 a day;
``daily`` by ``trade_date`` gives every stock that traded that day. Volume is in lots of
100 shares and amount in thousands of yuan; Market stores shares. A token without the
points an API needs answers code 40203 with the reason in words: the source is then
set aside in the ranking (``forbidden``) until its settings change. Adjustment factors
(``adj_factor``) and the list with delisted stocks need 2,000 points and are not used.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

import numpy as np

from .. import bars, calendars
from ..common import DAY_NS, NS, MarketError, now_ns
from .base import History, Source, coverage, empty_history, row

URL = "https://api.tushare.pro"
CALENDAR = "XSHG"
SCOPE = {"markets": ["CN"], "assetClasses": ["equity"]}
_SPAN_DAYS = 365 * 20  # 6,000 rows a request at most; twenty years of trading days stay below it


class TushareSource(Source):
    id = "tushare"
    descriptor = {"name": "Tushare Pro", "sourceIds": ["tushare"], "kinds": ["bars"],
                  "assetClasses": ["equity"], "credentials": "required", "paid": True,
                  "supports": ["history", "bulk"],
                  "settings": [{"name": "token", "label": "Tushare token", "type": "string", "secret": True,
                                "required": True}]}
    secret_settings = ("token",)

    async def describe(self, source_id: str) -> dict:
        return coverage([row(SCOPE, "bars", [("1d", "1990-12-19", 6000)], delisted=True)],
                        concurrency=2, rpm=50, per_day=8000, plan="free (120 points): unadjusted daily bars")

    async def configure(self, source_id: str, values: dict) -> dict:
        result = await super().configure(source_id, values)
        entry = self.market.registry.entries.get(source_id)
        if entry is not None and "token" in values:
            entry.rejected = None  # a new token is tried again
        return result

    async def _call(self, api: str, params: dict, fields: str = "") -> tuple[list[str], list[list]]:
        token = await self.secret(self.id, "token")
        if not token:
            raise MarketError("unauthorized", "Tushare needs a token in its settings", source_id=self.id)
        data = await self.egress.json(self.id, URL, method="POST",
                                      json_body={"api_name": api, "token": token, "params": params, "fields": fields},
                                      timeout=60)
        code = data.get("code")
        if code not in (0, "0"):
            raise _error(code, str(data.get("msg") or ""))
        body = data.get("data") or {}
        return list(body.get("fields") or []), list(body.get("items") or [])

    # ── one instrument ──────────────────────────────────────────────────────

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        if kind != "bars" or timeframe != "1d":
            raise MarketError("unsupported", f"Tushare gives daily bars only (asked {kind} {timeframe})",
                              source_id=self.id)
        stop = min(end, now_ns())
        if stop <= start:
            return empty_history("source_has_none")
        chunk_end = min(stop, start + _SPAN_DAYS * DAY_NS)
        fields, items = await self._call("daily", {"ts_code": target.code_at(), "start_date": _day(start),
                                                   "end_date": _day(chunk_end - 1)})
        columns = bars.window(_columns(fields, items), start, chunk_end)
        history = History(columns, next=chunk_end if chunk_end < stop else None)
        if not bars.length(columns) and history.next is None:
            history.empty_reason = "source_has_none"
        return history

    # ── the whole market by date ────────────────────────────────────────────

    async def bulk(self, source_id: str, kind: str, timeframe: str | None, scope: dict, codes: list[str],
                   start: int, end: int) -> History:
        """One trading day of every A share (``codes`` empty) or of the listed codes: columns carry ``code``."""
        if kind != "bars" or timeframe != "1d":
            raise MarketError("unsupported", "Tushare's whole-market answer is daily bars", source_id=self.id)
        fields, items = await self._call("daily", {"trade_date": _day(start)})
        wanted = set(codes)
        if wanted:
            index = fields.index("ts_code")
            items = [item for item in items if item[index] in wanted]
        columns = _columns(fields, items, keep_code=True)
        return History(columns, next=None)


def _columns(fields: list[str], items: list[list], *, keep_code: bool = False) -> bars.Columns:
    if not items:
        return bars.empty(("time", "close_time", "open", "high", "low", "close", "volume")
                          + (("code",) if keep_code else ()))
    index = {name: fields.index(name) for name in ("ts_code", "trade_date", "open", "high", "low", "close", "vol")}
    days = np.asarray([np.datetime64(f"{d[:4]}-{d[4:6]}-{d[6:8]}", "D")
                       for d in (str(item[index["trade_date"]]) for item in items)])
    opens, closes = calendars.daily_times(CALENDAR, days)
    columns = {"time": opens, "close_time": closes}
    for name in ("open", "high", "low", "close"):
        columns[name] = np.asarray([np.nan if item[index[name]] is None else float(item[index[name]])
                                    for item in items], dtype=np.float64)
    columns["volume"] = np.round(np.asarray([np.nan if item[index["vol"]] is None else float(item[index["vol"]])
                                             for item in items], dtype=np.float64) * 100, 4)
    if keep_code:
        columns["code"] = np.asarray([str(item[index["ts_code"]]) for item in items], dtype=object)
        order = np.lexsort((columns["time"], columns["code"]))
        return {name: values[order] for name, values in columns.items()}
    return bars.sort_unique(columns)


def _error(code, message: str) -> MarketError:
    text = f"Tushare {code}: {message}"[:300]
    if "每分钟" in message:
        return MarketError("rate_limited", text, source_id="tushare", retry_at=now_ns() + 60 * NS)
    if "每天" in message or "每小时" in message:
        tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time(), UTC)
        return MarketError("rate_limited", text, source_id="tushare", retry_at=int(tomorrow.timestamp()) * NS)
    if str(code) in ("40101", "-2001") or "token" in message.lower():
        return MarketError("unauthorized", text, source_id="tushare")
    if str(code) == "40203":
        return MarketError("forbidden", text, source_id="tushare")
    return MarketError("source_unavailable", text, source_id="tushare")


def _day(moment: int) -> str:
    """The Beijing date holding ``moment`` as YYYYMMDD."""
    return time.strftime("%Y%m%d", time.gmtime(moment // NS + 8 * 3600))
