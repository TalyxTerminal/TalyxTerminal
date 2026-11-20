"""Binance public data files (data.binance.vision) and where a reused code changed hands.

The files are Binance's own archive: spot and futures K lines (last, mark, index,
premium), funding rates by month, and per-day ``metrics`` (open interest, long/short
ratios, taker volume every 5 minutes), including contracts the API no longer lists.
A month's file appears in the first days of the next month, a day's file 07:00–09:00
UTC the next day. They are declared as archives (``rows``); Market's planner asks one
file at a time, falls to the day files and the API where one is missing (monthly files
sometimes miss days the day files have) and to the files where the API no longer
serves a delisted futures contract.

Codes are reused (spot LUNAUSDT is old LUNA until 2022-05-13 and new LUNA from
2022-05-31). Market splits a code where the contract changed:

- futures: at ``onboardDate`` when the 7 days before it had no real trade (zero-trade
  filler rows or no rows); without ``onboardDate``, where real trading resumes after at
  least 7 quiet days. Filler rows (no volume, no trades, open = high = low = close) are
  never prices: the source has none there.
- spot: at a gap of at least 7 days across which the price moved 1,000-fold or more;
  a gap with matching prices is the same pair trading again (the gap has none).

The older part belongs to a retired contract (``retiredAt`` = the day of its last real
trade, frozen in the identity store); the current contract starts at the cut.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import zipfile
from datetime import UTC, datetime

import numpy as np

from vocabulary.identity import timeframe_nanoseconds

from .. import bars
from ..common import DAY_NS, NS, MarketError, now_ns
from ..routing import in_worker
from .base import History, archive, empty_history, row

FILES = "https://data.binance.vision/data"
LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
SPOT_API = "https://data-api.binance.vision/api/v3"
KLINES = {"last": "klines", "mark": "markPriceKlines", "index": "indexPriceKlines", "premium": "premiumIndexKlines"}
METRICS = {"open_interest": ("sum_open_interest", "sum_open_interest_value"),
           "top_trader_account_ratio": ("count_toptrader_long_short_ratio",),
           "top_trader_position_ratio": ("sum_toptrader_long_short_ratio",),
           "long_short_account_ratio": ("count_long_short_ratio",),
           "taker_volume": ("sum_taker_long_short_vol_ratio",)}
QUIET_NS = 7 * DAY_NS
SPOT_JUMP = 1000.0
SPOT_FILES = "2017-08-01"
FUTURES_START = "2019-09-01"
# Futures K lines older than a month come from the files: they keep delisted contracts and the zero-trade filler rows
# a reused code is cut by, and the API only the current contract.
FUTURES_API = "P30D"
RATIOS = ("long_short_account_ratio", "top_trader_account_ratio", "top_trader_position_ratio", "taker_volume")
SPOT_HISTORY_START_MS = 1_500_000_000_000  # 2017-07-14, before Binance's first pair
MS = 1_000_000


def family(record: dict) -> str:
    if record["marketType"] == "spot":
        return "spot"
    return "futures/cm" if record.get("contractOrientation") == "inverse" else "futures/um"


def _month_label(moment: int) -> str:
    return datetime.fromtimestamp(moment // NS, UTC).strftime("%Y-%m")


def _day_label(moment: int) -> str:
    return datetime.fromtimestamp(moment // NS, UTC).strftime("%Y-%m-%d")


def _unzip_csv(body: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        text = archive.read(name).decode("utf-8", "replace")
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    if rows and not rows[0][0].strip().lstrip("-").replace(".", "", 1).isdigit() and ":" not in rows[0][0]:
        rows = rows[1:]  # header (futures files carry one since 2022)
    return rows


def _instant(value: str) -> int:
    """File timestamps: milliseconds, microseconds (spot from 2025) or 'YYYY-MM-DD HH:MM:SS' (metrics)."""
    value = value.strip()
    if ":" in value:
        return int(datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp()) * NS
    number = int(float(value))
    return number * 1000 if number > 10**14 else number * MS


def kline_columns(rows: list[list[str]], step: int, volume_column: int = 5) -> bars.Columns:
    if not rows:
        return {**bars.empty(), "trades": np.empty(0, dtype=np.float64)}
    times = np.asarray([_instant(row[0]) for row in rows], dtype=np.int64)
    array = np.asarray([[float(row[i]) for i in (1, 2, 3, 4, volume_column)] for row in rows], dtype=np.float64)
    trades = np.asarray([float(row[8]) if len(row) > 8 and row[8] != "" else np.nan for row in rows],
                        dtype=np.float64)
    return bars.sort_unique({"time": times, "close_time": times + step, "open": array[:, 0], "high": array[:, 1],
                             "low": array[:, 2], "close": array[:, 3], "volume": array[:, 4], "trades": trades})


def filler(columns: bars.Columns) -> np.ndarray:
    """Rows a futures file carries after a contract stopped trading: no volume, no trades, a flat price."""
    if not bars.length(columns):
        return np.zeros(0, dtype=bool)
    flat = (columns["open"] == columns["high"]) & (columns["high"] == columns["low"]) & \
        (columns["low"] == columns["close"])
    quiet = (columns["volume"] == 0) & ((columns["trades"] == 0) | np.isnan(columns["trades"]))
    return flat & quiet


def spans_of(times: np.ndarray, step: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for moment in (int(t) for t in times):
        if out and out[-1][1] == moment:
            out[-1] = (out[-1][0], moment + step)
        else:
            out.append((moment, moment + step))
    return out


def find_cuts(daily: bars.Columns, *, spot: bool, onboard: int | None) -> dict:
    """Cuts and source-has-none spans from a code's daily bars (see the module docstring)."""
    real = bars.take(daily, ~filler(daily)) if bars.length(daily) else daily
    times = real["time"] if bars.length(real) else np.zeros(0, dtype=np.int64)
    cuts: list[dict] = []
    none: list[tuple[int, int]] = []
    if spot:
        for index in range(1, len(times)):
            before, after = int(times[index - 1]), int(times[index])
            if after - before <= DAY_NS:
                continue
            none.append((before + DAY_NS, after))
            previous, following = float(real["close"][index - 1]), float(real["open"][index])
            if after - before - DAY_NS >= QUIET_NS and previous > 0 and following > 0 and \
                    max(previous / following, following / previous) >= SPOT_JUMP:
                cuts.append({"retiredAt": str(before), "cut": str(after)})
    elif onboard is not None:
        earlier = times[times < onboard]
        if len(earlier) and onboard - (int(earlier[-1]) + DAY_NS) >= QUIET_NS:
            cuts.append({"retiredAt": str(int(earlier[-1])), "cut": str(onboard)})
    else:
        for index in range(1, len(times)):
            before, after = int(times[index - 1]), int(times[index])
            if after - before - DAY_NS >= QUIET_NS:
                cuts.append({"retiredAt": str(before), "cut": str(after)})
    return {"cuts": cuts, "none": [[str(a), str(b)] for a, b in none],
            "onboard": None if onboard is None else str(onboard), "scannedAt": str(now_ns())}


def bounds(segments: dict, retired_at: str | None) -> tuple[int | None, int | None]:
    """[lower, upper) of one contract on a reused code; None = open."""
    cuts = segments.get("cuts") or []
    if retired_at is None:
        return (int(cuts[-1]["cut"]) if cuts else None), None
    for index, cut in enumerate(cuts):
        if cut["retiredAt"] == str(retired_at):
            lower = int(cuts[index - 1]["cut"]) if index else None
            return lower, int(cut["retiredAt"]) + DAY_NS
    return None, int(retired_at) + DAY_NS


class BinanceFiles:
    def __init__(self, source):
        self.source = source
        self.market = source.market
        self.egress = source.egress
        self._segments: dict[tuple[str, str], dict] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._listings: dict[str, list[str]] = {}

    # ── files ───────────────────────────────────────────────────────────────

    async def rows(self, url: str) -> list[list[str]] | None:
        status, body, _headers = await self.egress.request("binance", url, accept_status=(404,), timeout=60)
        if status == 404:
            return None
        try:
            return await in_worker(_unzip_csv, body)
        except (zipfile.BadZipFile, StopIteration, ValueError) as exc:
            raise MarketError("source_unavailable", f"binance file {url.rsplit('/', 1)[-1]} is unreadable: {exc}",
                              source_id="binance") from exc

    async def listing(self, prefix: str) -> list[str]:
        """Names under an S3 prefix (sub-prefixes and files), following pages; retries 503 Slow Down."""
        if prefix in self._listings:
            return self._listings[prefix]
        names, marker = [], None
        while True:
            params = {"delimiter": "/", "prefix": prefix}
            if marker:
                params["marker"] = marker
            for attempt in range(5):
                status, body, _headers = await self.egress.request("binance", LISTING, params=params,
                                                                   accept_status=(503,), timeout=60)
                if status != 503:
                    break
                await asyncio.sleep(1.5 * (attempt + 1))
            else:
                raise MarketError("rate_limited", "binance file listing answers 503 Slow Down", source_id="binance",
                                  status=503, retry_at=now_ns() + 60 * NS)
            text = body.decode("utf-8", "replace")
            found = re.findall(r"<Prefix>([^<]+)</Prefix>", text)[1:] + re.findall(r"<Key>([^<]+)</Key>", text)
            names += [name[len(prefix):].rstrip("/") for name in found if name.startswith(prefix)]
            truncated = "<IsTruncated>true</IsTruncated>" in text
            following = re.search(r"<NextMarker>([^<]+)</NextMarker>", text)
            if not truncated:
                break
            marker = following.group(1) if following else (prefix + names[-1] + ("/" if found[-1].endswith("/")
                                                                                   else ""))
        self._listings[prefix] = names
        return names

    # ── answers ─────────────────────────────────────────────────────────────

    def declare(self, scope: dict, periods: list[tuple]) -> list[dict]:
        """Binance's history rows: spot and futures K lines, funding, open interest and the ratios, with its files."""
        def files(start: str, bases=("last",)) -> dict:
            return {"archives": [archive("month", start, "P3D", bases=bases), archive("day", start, "PT12H",
                                                                                      bases=bases)]}
        spot = {**scope, "marketTypes": ["spot"]}
        swap = {**scope, "marketTypes": ["swap"]}
        metrics = {"archives": [archive("day", "2021-12-01", "PT12H")]}
        return [
            row(spot, "bars", [(tf, depth, rows_, files(SPOT_FILES)) for tf, depth, rows_, _more in periods],
                live="realtime", delisted=True, live_periods=["1m"]),
            row({**scope, "marketTypes": ["swap", "future"]}, "bars",
                [(tf, FUTURES_API, rows_, files(FUTURES_START, tuple(KLINES))) for tf, _depth, rows_, _more in periods],
                live="realtime", delisted=True, live_periods=["1m"]),
            row(swap, "funding_rate", [("8h", "P90D", 100, {"archives": [archive("month", "2020-01-01", "P3D")]})],
                live="realtime", delisted=True),
            row(swap, "open_interest", [("5m", "P30D", 500, metrics)], live="realtime", delisted=True),
            # The ratios have no API history: the day files are all (the newest day waits for its file).
            *[row(swap, kind, [("5m", "P0D", 288, metrics)], delisted=True) for kind in RATIOS]]

    async def history(self, target, kind: str, timeframe: str | None, basis: str, start: int, end: int,
                      file: tuple[str, int] | None) -> History:
        """One answer for a contract, from one of its files or from the API; a reused code answers only for the
        contract's own segment (see the module docstring)."""
        record = target.instrument
        fam = family(record)
        native = record.get("nativeId") or record["symbol"].split(":")[0].replace("/", "")
        onboard = None
        if fam != "spot" and record.get("retiredAt") is None:
            info = ((await self.source.markets("binance")).get(record["symbol"]) or {}).get("info") or {}
            if info.get("onboardDate"):
                onboard = int(info["onboardDate"]) * MS
        segments = await self.segments(record, onboard)
        lower, upper = bounds(segments, record.get("retiredAt"))
        if (upper is not None and start >= upper) or (lower is not None and end <= lower):
            return empty_history("source_has_none")
        if lower is not None and start < lower:
            return History(bars.empty(), lower, none=[(start, lower)])
        if file is not None:
            return await self._file(fam, native, kind, timeframe, basis, file, start, end, lower, upper)
        if record.get("active") is False and record.get("retiredAt") is None and fam != "spot":
            return History(bars.empty(), missing=True)  # a delisted futures contract: only its files have it
        history = await self.source._rest_history("binance", target, kind, timeframe, basis, start, end)
        stop = history.next if history.next is not None else end
        if upper is not None and stop > upper:
            history.columns = bars.window(history.columns, None, upper) if bars.length(history.columns) \
                else history.columns
            history.none.append((max(upper, start), stop))
        for a, b in segments.get("none", []):
            a, b = int(a), int(b)
            if b > start and a < stop:
                history.none.append((max(a, start), min(b, stop)))
        return history

    async def _file(self, fam: str, native: str, kind: str, timeframe: str | None, basis: str,
                    file: tuple[str, int], start: int, end: int, lower: int | None, upper: int | None) -> History:
        unit, first = file
        label = _month_label(first) if unit == "month" else _day_label(first)
        folder = f"{FILES}/{fam}/{'monthly' if unit == 'month' else 'daily'}"
        if kind == "bars":
            rows = await self.rows(f"{folder}/{KLINES[basis]}/{native}/{timeframe}/{native}-{timeframe}-{label}.zip")
        elif kind == "funding_rate":
            rows = await self.rows(f"{folder}/fundingRate/{native}/{native}-fundingRate-{label}.zip")
        else:
            rows = await self.rows(f"{folder}/metrics/{native}/{native}-metrics-{label}.zip")
        if rows is None:
            return History(bars.empty(), missing=True)
        if kind == "funding_rate":
            # calc_time is the funding moment, sometimes a few milliseconds late: whole seconds.
            times = np.asarray([_instant(row[0]) // NS * NS for row in rows], dtype=np.int64)
            columns = bars.sort_unique({"time": times, "intervalHours": np.asarray([float(row[1]) for row in rows]),
                                        "rate": np.asarray([float(row[2]) for row in rows])})
            return History(bars.window(columns, start, end))
        if kind != "bars":
            return History(bars.window(_metric_columns(rows, kind), start, end))
        step = timeframe_nanoseconds(timeframe)
        # A month of minutes is 44,640 rows of text: read in a worker thread, not on the event loop.
        columns = bars.window(await in_worker(kline_columns, rows, step, 7 if fam == "futures/cm" and basis == "last" else 5), start, end)
        none: list[tuple[int, int]] = []
        if fam != "spot" and bars.length(columns):
            flat = filler(columns)
            none += spans_of(columns["time"][flat], step)
            columns = bars.take(columns, ~flat)
        if upper is not None and end > upper:
            none.append((max(upper, start), end))
            columns = bars.window(columns, None, upper)
        columns.pop("trades", None)
        return History(columns if bars.length(columns) else bars.empty(), none=none)

    # ── reused codes ────────────────────────────────────────────────────────

    async def segments(self, record: dict, onboard: int | None) -> dict:
        fam, native = family(record), record["nativeId"]
        key = (fam, native)
        cached = self._segments.get(key)
        if cached is None:
            cached = await in_worker(self.market.identity.segments, "binance", fam, native)
        stale = cached is not None and onboard is not None and (cached.get("onboard") is None
                                                                 or int(cached["onboard"]) < onboard)
        if cached is not None and not stale:
            self._segments[key] = cached
            return cached
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._segments and not stale:
                return self._segments[key]
            if fam == "spot":
                daily = await self._spot_daily(native)
                value = find_cuts(daily, spot=True, onboard=None)
            else:
                daily = await self._futures_daily(fam, native, onboard)
                value = find_cuts(daily, spot=False, onboard=onboard)
            if cached is not None:
                # A cut once made stays: a later scan only adds cuts.
                known = {cut["retiredAt"] for cut in cached.get("cuts", [])}
                value["cuts"] = cached.get("cuts", []) + [c for c in value["cuts"] if c["retiredAt"] not in known]
            retired = []
            current = record if record.get("retiredAt") is None else None
            if current is not None:
                for cut in value["cuts"]:
                    retired.append({**{k: v for k, v in current.items() if k != "instrumentId"},
                                    "retiredAt": cut["retiredAt"], "active": False})
            await in_worker(self.market.identity.save_segments, "binance", fam, native, value,
                            current["instrumentId"] if current else None, retired)
            self._segments[key] = value
            return value

    async def _spot_daily(self, native: str) -> bars.Columns:
        """Every daily bar of a spot pair: the 1,000-day pages are asked at once (one round trip)."""
        page_ms = 1000 * 86_400_000
        starts = range(SPOT_HISTORY_START_MS, now_ns() // MS + 86_400_000, page_ms)
        pages = await asyncio.gather(*(self.egress.json("binance", f"{SPOT_API}/klines",
                                                        params={"symbol": native, "interval": "1d",
                                                                "startTime": since, "endTime": since + page_ms - 1,
                                                                "limit": 1000}, timeout=30)
                                       for since in starts))
        rows = [item for page in pages for item in page]
        return kline_columns([[str(item[0]), *item[1:]] for item in rows], DAY_NS)

    async def _futures_daily(self, fam: str, native: str, onboard: int | None) -> bars.Columns:
        """Daily bars from the month files: around onboardDate when known, else all of them."""
        months = sorted(name.rsplit("-", 2)[-2] + "-" + name.rsplit("-", 1)[-1][:2]
                        for name in await self.listing(f"data/{fam}/monthly/klines/{native}/1d/")
                        if name.endswith(".zip"))
        parts: list[bars.Columns] = []
        if onboard is not None:
            onboard_month = _month_label(onboard)
            for label in reversed([m for m in months if m <= onboard_month]):
                rows = await self.rows(f"{FILES}/{fam}/monthly/klines/{native}/1d/{native}-1d-{label}.zip")
                columns = kline_columns(rows or [], DAY_NS, 7 if fam == "futures/cm" else 5)
                parts.append(columns)
                real = bars.take(columns, ~filler(columns)) if bars.length(columns) else columns
                if bars.length(real) and int(real["time"][0]) < onboard:
                    break  # going back month by month: this month holds the last real trade before onboard
        else:
            for label in months:
                rows = await self.rows(f"{FILES}/{fam}/monthly/klines/{native}/1d/{native}-1d-{label}.zip")
                parts.append(kline_columns(rows or [], DAY_NS, 7 if fam == "futures/cm" else 5))
        merged = {**bars.empty(), "trades": np.empty(0, dtype=np.float64)}
        for part in parts:
            merged = bars.merge(merged, part) if bars.length(merged) else part
        return merged

    async def delisted(self, fam: str, known: set[str]) -> list[str]:
        """Codes with files but no longer in Binance's catalog."""
        return [name for name in await self.listing(f"data/{fam}/monthly/klines/") if name and name not in known]


def _metric_columns(rows: list[list[str]], kind: str) -> dict:
    header = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
              "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio", "count_long_short_ratio",
              "sum_taker_long_short_vol_ratio"]
    columns = {"time": np.asarray([_instant(row[0]) for row in rows], dtype=np.int64)}
    for name in METRICS[kind]:
        index = header.index(name)
        columns[_column(kind, name)] = np.asarray(
            [float(row[index]) if row[index] not in ("", None) else np.nan for row in rows])
    return bars.sort_unique(columns)


def _column(kind: str, name: str) -> str:
    if kind == "open_interest":
        return "value" if name == "sum_open_interest" else "valueQuote"
    return "ratio"
