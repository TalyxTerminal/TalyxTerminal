"""OKX's historical data files (static.okx.com, listed at okx.com/historical-data).

One-minute candlesticks of spot pairs (from 2020-01) and perpetual swaps (from 2021-09), a
month or a day per file, and swap funding rates by month:
``candlesticks/<monthly/YYYYMM | daily/YYYYMMDD>/<instId>-candlesticks-<YYYY-MM[-DD]>.zip`` with rows
``instrument_name, open, high, low, close, vol, vol_ccy, vol_quote, open_time (ms), confirm``
(swap volume in contracts, vol_ccy in the base currency) and
``swaprates/monthly/YYYYMM/<instId>-fundingrates-YYYY-MM.zip`` with
``instrument_name, funding_rate, funding_time (ms)``. Days and months are cut at midnight
UTC+8. A day's file was not out 17 hours after the day (checked 2026-11-15) and a month's
within 15 days; declared conservatively, a file later than that falls to the API, which
reaches back to 2018.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime

import numpy as np

from .. import bars
from ..common import NS, MarketError
from ..routing import in_worker, zone_offset
from .base import History, archive, row

FILES = "https://static.okx.com/cdn/okex/traderecords"
ZONE = "+08:00"
SPOT_START = "2020-01-01"
SWAP_START = "2021-09-01"
MINUTE = 60 * NS
MS = 1_000_000


def _files(start: str) -> dict:
    return {"archives": [archive("month", start, "P15D", day_zone=ZONE),
                         archive("day", start, "P2D", day_zone=ZONE)]}


def _candles(rows: list[list[str]], spot: bool) -> bars.Columns:
    array = np.asarray([[float(value) for value in line[1:9]] for line in rows], dtype=np.float64).reshape(-1, 8)
    times = array[:, 7].astype(np.int64) * MS
    # Spot vol is in the base currency; a swap's vol counts contracts and vol_ccy is the base (as on the API).
    volume = array[:, 4] if spot else array[:, 5]
    return bars.sort_unique({"time": times, "close_time": times + MINUTE, "open": array[:, 0], "high": array[:, 1],
                             "low": array[:, 2], "close": array[:, 3], "volume": volume})


def _csv(body: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(body)) as packed:
        text = packed.read(next(name for name in packed.namelist() if name.endswith(".csv"))).decode("utf-8")
    return [line for line in csv.reader(io.StringIO(text)) if line and line[-1] != "" and line[0] != "instrument_name"]


class OkxFiles:
    def __init__(self, source):
        self.source = source
        self.egress = source.egress

    def declare(self, scope: dict, periods: list[tuple]) -> list[dict]:
        """OKX's history rows: one-minute files for spot pairs and perpetual swaps, monthly funding files."""
        def deeper(start: str) -> list[tuple]:
            return [(tf, depth, rows_, {**more, **_files(start)}) if tf == "1m" else (tf, depth, rows_, more)
                    for tf, depth, rows_, more in periods]
        swap = {**scope, "marketTypes": ["swap"]}
        return [row({**scope, "marketTypes": ["spot"]}, "bars", deeper(SPOT_START), live="realtime",
                    live_periods=["1m"]),
                row(swap, "bars", deeper(SWAP_START), live="realtime", live_periods=["1m"]),
                row({**scope, "marketTypes": ["future", "option"]}, "bars", periods, live="realtime",
                    live_periods=["1m"]),
                row(swap, "funding_rate", [("8h", "P90D", 100, {"archives": [archive("month", SWAP_START, "P15D",
                                                                                     day_zone=ZONE)]})],
                    live="realtime")]

    async def history(self, target, kind: str, timeframe: str | None, basis: str, start: int, end: int,
                      file: tuple[str, int] | None) -> History:
        if file is None:
            return await self.source._rest_history("okx", target, kind, timeframe, basis, start, end)
        unit, first = file
        local = datetime.fromtimestamp((first + zone_offset(ZONE)) // NS, UTC)
        inst = target.instrument["nativeId"]
        if kind == "funding_rate":
            rows = await self._file(f"{FILES}/swaprates/monthly/{local:%Y%m}/{inst}-fundingrates-{local:%Y-%m}.zip")
            if rows is None:
                return History(bars.empty(), missing=True)
            times = np.asarray([int(line[2]) * MS for line in rows], dtype=np.int64)
            rates = np.asarray([float(line[1]) for line in rows], dtype=np.float64)
            intervals = np.diff(times, prepend=times[:1] - 8 * 3600 * NS) / (3600 * NS) if len(times) else times
            columns = bars.sort_unique({"time": times, "rate": rates,
                                        "intervalHours": np.asarray(intervals, dtype=np.float64)})
            return History(bars.window(columns, start, end))
        name = f"monthly/{local:%Y%m}/{inst}-candlesticks-{local:%Y-%m}" if unit == "month" else \
            f"daily/{local:%Y%m%d}/{inst}-candlesticks-{local:%Y-%m-%d}"
        rows = await self._file(f"{FILES}/candlesticks/{name}.zip")
        if rows is None:
            return History(bars.empty(), missing=True)
        # A month of minutes is 44,640 rows of text: read in a worker thread, not on the event loop.
        columns = await in_worker(_candles, rows, target.instrument["marketType"] == "spot")
        return History(bars.window(columns, start, end))

    async def _file(self, url: str) -> list[list[str]] | None:
        status, body, _headers = await self.egress.request("okx", url, accept_status=(404,), timeout=60)
        if status == 404:
            return None
        try:
            return await in_worker(_csv, body)
        except (zipfile.BadZipFile, StopIteration, ValueError, UnicodeDecodeError) as exc:
            raise MarketError("source_unavailable", f"okx file {url.rsplit('/', 1)[-1]} is unreadable: {exc}",
                              source_id="okx") from exc
