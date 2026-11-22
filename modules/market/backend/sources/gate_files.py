"""Gate's monthly data files (download.gatedata.org): K lines and funding beyond the API's reach.

Gate's API gives at most 10,000 bars back (an hour chart reaches about 14 months); its
files hold whole months from 2018 (spot) and 2020 (USDT perpetuals):
``<market>/candlesticks_<1m|5m|1h|1d>/<YYYYMM>/<PAIR>-<YYYYMM>.csv.gz`` with rows
``time (s), volume, close, high, low, open`` (perpetual volume in contracts) and
``<market>/funding_applies/...`` with ``time, rate``. A month's file appears in the first
days of the next month. Spot one- and five-minute files stop after 2024-03. They are
declared as archives; Market's planner asks one month at a time.
"""

from __future__ import annotations

import csv
import gzip
import io
from datetime import UTC, datetime

import numpy as np
from talyx_sdk.i18n import t

from vocabulary.identity import timeframe_nanoseconds

from .. import bars
from ..common import NS, MarketError
from ..routing import in_worker
from .base import History, archive, row

FILES = "https://download.gatedata.org"
TIMEFRAMES = ("1m", "5m", "1h", "1d")
SPOT_START = "2018-01-01"
SPOT_SHORT_UNTIL = "2024-04-01"  # spot 1m and 5m files stop after 2024-03
FUTURES_START = "2020-01-01"


def market_of(record: dict) -> str | None:
    if record["marketType"] == "spot":
        return "spot"
    if record["marketType"] == "swap" and record.get("settle") == "USDT":
        return "futures_usdt"
    return None


def _rows(body: bytes) -> list[list[str]]:
    text = gzip.decompress(body).decode("utf-8", "replace")
    return [row for row in csv.reader(io.StringIO(text)) if row and row[0].strip().isdigit()]



def _candles(rows: list[list[str]], step: int, size: float) -> bars.Columns:
    """Gate's columns: time (s), volume (contracts for futures, times ``size`` is the base), close, high, low, open."""
    array = np.asarray([[float(v) for v in row[:6]] for row in rows], dtype=np.float64).reshape(-1, 6)
    times = array[:, 0].astype(np.int64) * NS
    return bars.sort_unique({"time": times, "close_time": times + step, "open": array[:, 5], "high": array[:, 3],
                             "low": array[:, 4], "close": array[:, 2], "volume": array[:, 1] * size})

class GateFiles:
    def __init__(self, source):
        self.source = source
        self.egress = source.egress

    def declare(self, scope: dict, periods: list[tuple]) -> list[dict]:
        """Gate's history rows: spot and USDT perpetual K lines and funding with its month files."""
        def deeper(start: str, short_until: str | None) -> list[tuple]:
            return [(tf, depth, rows_, {"archives": [archive(
                "month", start, "P3D", until=short_until if tf in ("1m", "5m") else None)]})
                    if tf in TIMEFRAMES else (tf, depth, rows_, more) for tf, depth, rows_, more in periods]
        swap = {**scope, "marketTypes": ["swap"]}
        return [row({**scope, "marketTypes": ["spot"]}, "bars", deeper(SPOT_START, SPOT_SHORT_UNTIL), live="realtime",
                    live_periods=["1m"]),
                row(swap, "bars", deeper(FUTURES_START, None), live="realtime", live_periods=["1m"]),
                row({**scope, "marketTypes": ["future", "option"]}, "bars", periods, live="realtime",
                    live_periods=["1m"]),
                row(swap, "funding_rate", [("8h", "P90D", 100, {"archives": [archive("month", FUTURES_START, "P3D")]})],
                    live="realtime")]

    async def history(self, target, kind: str, timeframe: str | None, basis: str, start: int, end: int,
                      file: tuple[str, int] | None) -> History:
        if file is None:
            return await self.source._rest_history("gate", target, kind, timeframe, basis, start, end)
        record = target.instrument
        market = market_of(record)
        if market is None:  # coin-settled perpetuals: Gate keeps files only for USDT ones
            return History(bars.empty(), missing=True)
        if kind == "funding_rate":
            rows = await self._file(self._url("futures_usdt", "funding_applies", record, file[1]))
            if rows is None:
                return History(bars.empty(), missing=True)
            columns = bars.sort_unique({"time": np.asarray([int(row[0]) * NS for row in rows], dtype=np.int64),
                                        "rate": np.asarray([float(row[1]) for row in rows], dtype=np.float64)})
            return History(bars.window(columns, start, end))
        rows = await self._file(self._url(market, f"candlesticks_{timeframe}", record, file[1]))
        if rows is None:
            return History(bars.empty(), missing=True)
        size = float(record.get("contractSize") or 1) if market != "spot" else 1.0
        # A month of minutes is 44,640 rows of text: read in a worker thread, not on the event loop.
        columns = await in_worker(_candles, rows, timeframe_nanoseconds(timeframe), size)
        return History(bars.window(columns, start, end))

    async def _file(self, url: str) -> list[list[str]] | None:
        status, body, _headers = await self.egress.request("gate", url, accept_status=(404,), timeout=60)
        if status == 404:
            return None
        try:
            return await in_worker(_rows, body)
        except (OSError, EOFError, ValueError) as exc:
            raise MarketError("source_unavailable", t("gate file {value} is unreadable: {exc}", value=url.rsplit('/', 1)[-1], exc=exc),
                              source_id="gate") from exc

    def _url(self, market: str, kind: str, record: dict, month: int) -> str:
        label = datetime.fromtimestamp(month // NS, UTC).strftime("%Y%m")
        return f"{FILES}/{market}/{kind}/{label}/{record['nativeId']}-{label}.csv.gz"
