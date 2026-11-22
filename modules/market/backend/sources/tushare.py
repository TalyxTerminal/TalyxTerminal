"""Tushare Pro: A-share daily bars, and the whole market's day in one request.

Every call is one HTTP POST to ``api.tushare.pro`` naming the API, the user's token and
parameters; answers are ``{code, msg, data: {fields, items}}``. The free tier (120
points) defaults to 50 requests a minute for unadjusted daily bars (``daily``);
``daily`` by ``trade_date`` gives every stock that traded that day. Volume is in lots of
100 shares and amount in thousands of yuan; Market stores shares. A token without the
points an API needs answers code 40203 with the reason in words: the source is then
set aside for that data kind until its settings change or it reconnects. Adjustment factors
(``adj_factor``) and the list with delisted stocks need 2,000 points and are not used.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np
from talyx_sdk.i18n import mark, t

from .. import bars, calendars
from ..common import DAY_NS, NS, MarketError, now_ns
from .baostock import day_ns
from .base import RPM_SETTING, History, MeteredSource, coverage, empty_history, row
from .indices import CN_INDICES

URL = "https://api.tushare.pro"
CALENDAR = "XSHG"
SCOPE = {"markets": ["CN"], "assetClasses": ["equity"]}
_SPAN_DAYS = 365 * 20  # 6,000 rows a request at most; twenty years of trading days stay below it


class TushareSource(MeteredSource):
    default_rpm = 50
    id = "tushare"
    descriptor = {"name": "Tushare Pro", "sourceIds": ["tushare"], "kinds": ["bars", "weights", "shares"],
                  "assetClasses": ["equity", "index"], "credentials": "required", "paid": True,
                  "supports": ["history", "bulk", "research"],
                  "settings": [RPM_SETTING, {"name": "token", "label": mark("Tushare token"), "type": "string", "secret": True,
                                "required": True}]}
    secret_settings = ("token",)

    async def describe(self, source_id: str) -> dict:
        return coverage([row(SCOPE, "bars", [("1d", "1990-12-19", 6000)], delisted=True),
                         row(SCOPE, "shares", [("1d", "1990-12-19", 6000)], point_in_time=True),
                         row({"markets": ["CN"], "assetClasses": ["index"]}, "weights", point_in_time=True)],
                        concurrency=2, rpm=self.rpm, plan="Tushare Pro")

    async def _call(self, api: str, params: dict, fields: str = "") -> tuple[list[str], list[list]]:
        token = await self.secret(self.id, "token")
        if not token:
            raise MarketError("unauthorized", t("Tushare needs a token in its settings"), source_id=self.id)
        data = await self.request(self.egress.json, URL, method="POST",
                                      json_body={"api_name": api, "token": token, "params": params, "fields": fields},
                                      timeout=60)
        code = data.get("code")
        if code not in (0, "0"):
            error = _error(code, str(data.get("msg") or ""))
            if error.code == "forbidden":
                error.details["dataKind"] = {"daily": "bars", "index_weight": "weights", "daily_basic": "shares"}[api]
            raise error
        body = data.get("data") or {}
        return list(body.get("fields") or []), list(body.get("items") or [])

    def research_parts(self, source_id, target, kind, start, end):
        if kind not in ("weights", "shares"):
            return None
        if kind == "weights" and target.code_at() not in CN_INDICES:
            raise MarketError("unsupported", t("No index weights for {symbol}", symbol=target.code_at()))
        today = date.fromisoformat(_day(now_ns()))
        last = min(date.fromisoformat(_day((end or now_ns()) - 1)), today)
        first = date.fromisoformat(_day(start if start is not None else
                                       int(target.instrument.get("listedFrom") or now_ns())))
        first = min(first, last) if start is None else first
        begin = first.replace(day=1, month=first.month if kind == "weights" else 1)
        parts = []
        while begin <= last:
            following = ((begin + timedelta(days=32)).replace(day=1) if kind == "weights"
                         else begin.replace(year=begin.year + 1))
            stop = min(following - timedelta(days=1), today)
            parts.append(f"{begin:%Y%m%d}:{stop:%Y%m%d}")
            begin = following
        return parts

    async def research_part(self, source_id, target, kind, part):
        start, end = part.split(":")
        if kind == "shares":
            fields, items = await self._call("daily_basic", {"ts_code": target.code_at(), "start_date": start,
                                                            "end_date": end}, "trade_date,total_share,float_share")
            rows = []
            for values in items:
                item = dict(zip(fields, values, strict=True))
                day = day_ns(date.fromisoformat(item["trade_date"]).isoformat())
                for field, label in (("total_share", "outstanding"), ("float_share", "float")):
                    if item[field] is not None:
                        rows.append({"date": str(day), "knownAt": str(day + 17 * 3600 * NS), "kind": label,
                                     "shares": str(int(Decimal(str(item[field])) * 10000))})
            return rows
        # A month starts with the last published weights of the preceding month.
        begin = date.fromisoformat(start)
        previous = (begin - timedelta(days=1)).replace(day=1)
        fields, items = await self._call("index_weight", {"index_code": CN_INDICES[target.code_at()][1],
                                        "start_date": previous.strftime("%Y%m%d"), "end_date": end})
        snapshots = {}
        for values in items:
            item = dict(zip(fields, values, strict=True))
            snapshots.setdefault(item["trade_date"], []).append(item)
        rows = []
        days = sorted(snapshots)
        for i, day in enumerate(days):
            until = date.fromisoformat(days[i + 1]) if i + 1 < len(days) else date.fromisoformat(end) + timedelta(days=1)
            if until <= begin:
                continue
            known = day_ns(date.fromisoformat(day).isoformat())
            effective = max(known, day_ns(begin.isoformat()))
            rows.extend({"code": item["con_code"], "weight": item["weight"], "from": str(effective),
                         "until": str(day_ns(until.isoformat())), "knownAt": str(known)} for item in snapshots[day])
        return rows

    # ── one instrument ──────────────────────────────────────────────────────

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        if kind != "bars" or timeframe != "1d":
            raise MarketError("unsupported", t("Tushare gives daily bars only (asked {kind} {timeframe})", kind=kind, timeframe=timeframe),
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
            raise MarketError("unsupported", t("Tushare's whole-market answer is daily bars"), source_id=self.id)
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
