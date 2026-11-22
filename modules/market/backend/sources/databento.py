"""Databento historical API (paid, billed by usage): US equity bars from the exchanges' own feeds.

``POST hist.databento.com/v0/timeseries.get_range`` with HTTP basic auth (the key as user
name), ``dataset`` (XNAS.ITCH: Nasdaq TotalView-ITCH from 2018-05-01), ``schema``
(ohlcv-1m or ohlcv-1d), ``symbols`` and ``stype_in=raw_symbol``; with
``encoding=json``, ``pretty_px`` and ``pretty_ts`` each line is one bar ``{"hd": {"ts_event":
ISO time, "rtype", "publisher_id", "instrument_id"}, "open": "192.900000000", ..., "volume":
"50080539"}`` stamped at the bar's start. Daily bars are UTC days, which are the trading
dates of US sessions. Market builds session-aligned hours from regular-session minutes.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import numpy as np
from talyx_sdk.i18n import mark, t

from .. import bars, calendars
from ..common import NS, MarketError, now_ns
from .base import RPM_SETTING, History, MeteredSource, coverage, empty_history, row
from .gateways import _intraday_columns

URL = "https://hist.databento.com/v0/timeseries.get_range"
_SCHEMA = {"1m": "ohlcv-1m", "1d": "ohlcv-1d"}
_DATASETS = {"XNAS.ITCH": "2018-05-01", "DBEQ.BASIC": "2023-03-28", "EQUS.MINI": "2023-03-28",
             "EQUS.SUMMARY": "2024-07-01"}


class DatabentoSource(MeteredSource):
    default_rpm = 100
    id = "databento"
    descriptor = {"name": "Databento", "sourceIds": ["databento"], "kinds": ["bars"],
                  "assetClasses": ["equity", "fund"], "credentials": "required", "paid": True,
                  "supports": ["history", "configure"],
                  "settings": [RPM_SETTING, {"name": "apiKey", "label": mark("Databento API key"), "type": "string", "secret": True,
                                "required": True},
                               {"name": "dataset", "label": mark("Dataset (billed per use)"), "type": "choice",
                                "choices": list(_DATASETS), "secret": False, "required": True}]}
    secret_settings = ("apiKey",)

    def _dataset(self) -> str:
        value = self.settings.get("dataset") or "XNAS.ITCH"
        if value not in _DATASETS:
            raise MarketError("invalid_input", t("Databento dataset {value} is not supported", value=value), source_id=self.id)
        return value

    async def describe(self, source_id: str) -> dict:
        dataset = self._dataset()
        periods = [(tf, _DATASETS[dataset], 10000) for tf in (("1d",) if dataset == "EQUS.SUMMARY" else _SCHEMA)]
        return coverage([row({"markets": ["US"], "assetClasses": ["equity", "fund"]}, "bars", periods)],
                        concurrency=2, rpm=self.rpm, plan=dataset)

    async def history(self, source_id: str, target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        if kind != "bars" or timeframe not in _SCHEMA or self._dataset() == "EQUS.SUMMARY" and timeframe != "1d":
            raise MarketError("unsupported", f"Databento has no {kind} {timeframe or ''}".strip(), source_id=self.id)
        key = await self.secret(self.id, "apiKey")
        if not key:
            raise MarketError("unauthorized", t("Databento needs an API key"), source_id=self.id)
        stop = min(end, now_ns())
        if stop <= start:
            return empty_history("source_has_none")
        # Daily records are stamped at UTC midnight, before the local-midnight series key.
        begin = start - start % (86400 * NS) if timeframe == "1d" else start
        params = {"dataset": self._dataset(), "schema": _SCHEMA[timeframe], "symbols": target.code_at(),
                  "stype_in": "raw_symbol", "start": _iso(begin), "end": _iso(stop), "encoding": "json",
                  "pretty_px": "true", "pretty_ts": "true"}
        auth = base64.b64encode(f"{key}:".encode()).decode()
        body = await self.request(self.egress.bytes, URL, method="POST", data=params, headers={"Authorization": f"Basic {auth}"},
                                  timeout=120)
        items = [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
        calendar = target.instrument["calendarId"]
        values = {name: np.asarray([float(item[name]) for item in items], dtype=np.float64)
                  for name in ("open", "high", "low", "close", "volume")}
        times = np.asarray([_ns(item["hd"]["ts_event"]) for item in items], dtype=np.int64)
        if timeframe == "1d":
            days = times.astype("datetime64[ns]").astype("datetime64[D]")
            opens, closes = calendars.daily_times(calendar, days)
            columns = bars.sort_unique({"time": opens, "close_time": closes, **values}) if items else bars.empty()
        else:
            columns = _intraday_columns(times, values, calendar, timeframe)
        columns = bars.window(columns, start, end)
        history = History(columns)
        if not bars.length(columns):
            history.empty_reason = "source_has_none"
        return history


def _iso(moment: int) -> str:
    return datetime.fromtimestamp(moment // NS, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ns(text) -> int:
    if isinstance(text, (int, float)) or str(text).isdigit():
        return int(text)
    value = str(text).replace("Z", "")
    whole, _dot, fraction = value.partition(".")
    seconds = int(datetime.fromisoformat(whole).replace(tzinfo=UTC).timestamp())
    return seconds * NS + int((fraction + "000000000")[:9])
