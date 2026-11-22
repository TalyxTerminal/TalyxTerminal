"""``write_data``: import price files as their own datasets, and delete local data.

An import reads CSV or Parquet files, one instrument per file or a code column. What
the caller does not say is detected per file: kind from the columns, the code from a
code column or the file name (``AAPL_1d_2024.csv`` → AAPL), the period from the file
name or the most common gap, the zone from offsets in the values or the exchange's.
Codes are matched to Market's listings by market, code and date (former codes and
delisted listings included); an unmatched code becomes its own listing. Imported rows
are kept as the instrument's ``import:<id>`` series, beside and never instead of the
source's: reading the instrument still answers from its sources.

Rows that cannot be read are refused one by one with the reason; the rest are kept.
Delete removes stored rows (by instrument and kind, by import, or everything one source
gave), never the instrument itself; deleted data is downloaded again when needed.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow.parquet as pq
from talyx_sdk.i18n import t

from vocabulary.identity import normalize_timeframe, timeframe_nanoseconds

from . import bars, calendars, codes
from .common import DAY_NS, NS, MarketError, now_ns
from .routing import in_worker
from .store import DocumentStore, SeriesKey

TIME = ("time", "date", "datetime", "timestamp", "day", "trade_date")
PRICES = {"open": ("open", "o"), "high": ("high", "h"), "low": ("low", "l"), "close": ("close", "c", "price"),
          "volume": ("volume", "vol", "v")}
CODE = ("symbol", "ticker", "code")
MAX_REJECTED = 20
MAX_FILE = 512 * 1024 * 1024


def folder_files(folder: Path) -> list[str]:
    if not folder.is_dir():
        raise MarketError("not_found", t("No folder {folder}", folder=folder))
    return sorted(str(path) for path in folder.iterdir() if path.suffix.lower() in (".csv", ".parquet") and path.is_file())


async def read_import_file(path: str) -> bytes:
    def read():
        target = Path(path)
        if target.stat().st_size > MAX_FILE:
            raise MarketError("invalid_file", t("{path} is larger than 512 MB", path=path))
        return target.read_bytes()
    return await in_worker(read)


class ParsedFile:
    """One file's rows after reading: per code, the bar columns; and the refused lines."""

    def __init__(self, path: str):
        self.path = path
        self.kind = "bars"
        self.timeframe: str | None = None
        self.market: str | None = None
        self.by_code: dict[tuple[str, str | None], dict] = {}
        self.rejected: list[dict] = []


def _name(path: str) -> str:
    return Path(path.replace("\\", "/")).name


def _header(columns: list[str], wanted: tuple[str, ...]) -> str | None:
    lowered = {column.strip().lower(): column for column in columns}
    return next((lowered[name] for name in wanted if name in lowered), None)


def _timeframe_of_name(stem: str) -> str | None:
    for token in stem.lower().split("_"):
        match = re.fullmatch(r"(\d+)(m|min|h|hour|d|day|w|week|mo|month|y|year)", token)
        if match:
            unit = {"min": "m", "hour": "h", "day": "d", "week": "w", "month": "mo", "year": "y"}.get(match[2], match[2])
            return f"{int(match.group(1))}{unit}"
        if token in ("daily", "day"):
            return "1d"
        if token in ("minute", "min"):
            return "1m"
        if token in ("hourly", "hour"):
            return "1h"
    return None


def _code_of_name(stem: str, pattern: str | None) -> str | None:
    if pattern:
        match = re.search(pattern, _name(stem))
        return match.group(1) if match else None
    return stem.split("_")[0] or None


def _rows_of(path: str, content: bytes) -> tuple[list[str], list[dict]]:
    if path.lower().endswith(".parquet"):
        table = pq.read_table(io.BytesIO(content))
        return table.column_names, table.to_pylist()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return number if np.isfinite(number) else None


def _instant(value, zone: ZoneInfo, exchange_zone: ZoneInfo) -> tuple[int | None, bool]:
    """(ns, date_only) of one time value: an ISO date or date-time (with or without offset) or epoch s/ms/ns."""
    if value is None or value == "":
        return None, False
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=zone)
        return int(moment.timestamp() * 1_000_000) * 1000, False
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) or re.fullmatch(r"\d{8}", text):
        day = text if "-" in text else f"{text[:4]}-{text[4:6]}-{text[6:]}"
        try:
            moment = datetime.fromisoformat(day).replace(tzinfo=exchange_zone)
        except ValueError:
            return None, False
        return int(moment.timestamp()) * NS, True
    if re.fullmatch(r"\d{9,19}", text):
        number = int(text)
        scale = NS if number < 10**11 else 10**6 if number < 10**14 else 1000 if number < 10**17 else 1
        return number * scale, False
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00").replace("/", "-"))
    except ValueError:
        return None, False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return int(moment.timestamp() * 1_000_000) * 1000, False


class WriteData:
    def __init__(self, market):
        self.market = market
        self.imports = DocumentStore(market.root, "imports")

    # ── entry ───────────────────────────────────────────────────────────────

    async def run(self, value: dict, *, read, approve=None) -> dict:
        """``read(path) -> bytes`` reads one named file using the caller’s workspace for relative paths."""
        operation = value["operation"]
        if approve is not None:
            await approve(operation)
        if operation["op"] == "import":
            return await self.import_files(operation["paths"], operation.get("spec") or {}, read)
        return await self.delete(operation)

    # ── import ──────────────────────────────────────────────────────────────

    async def scan_folder(self, folder: Path, spec: dict) -> dict:
        files = await self.scan(await in_worker(folder_files, folder), spec, read_import_file)
        return {"folder": str(folder), "files": files}

    async def import_folder(self, folder: Path, spec: dict) -> dict:
        paths = await in_worker(folder_files, folder)
        if not paths:
            raise MarketError("invalid_file", t("No CSV or Parquet files in {folder}", folder=folder))
        return await self.import_files(paths, {"name": folder.name, **spec}, read_import_file)

    async def scan(self, paths: list[str], spec: dict, read) -> list[dict]:
        """What an import of these files would do, per file, without writing anything."""
        out = []
        for path in paths:
            parsed = await self._parse(path, spec, read)
            if isinstance(parsed, dict):
                out.append(parsed)
                continue
            for (code, instrument_id), columns in parsed.by_code.items():
                out.append({"file": _name(path), "kind": parsed.kind, "timeframe": parsed.timeframe,
                            "market": parsed.market, "code": code, "rows": bars.length(columns),
                            "rejected": len(parsed.rejected), "instrumentId": instrument_id})
        return out

    async def import_files(self, paths: list[str], spec: dict, read) -> dict:
        import_id = _import_id(spec.get("name"))
        result = {"importId": import_id, "rows": 0, "instruments": 0, "matched": 0, "unmatched": [], "rejectedRows": [],
                  "deletedBytes": 0, "rejectedCount": 0, "unmatchedCount": 0}
        files, seen = [], set()
        for path in paths:
            parsed = await self._parse(path, spec, read)
            if isinstance(parsed, dict):
                result["rejectedCount"] += 1
                if len(result["rejectedRows"]) < MAX_REJECTED:
                    result["rejectedRows"].append({"line": 0, "reason": f"{parsed['file']}: {parsed['problem']}"[:200]})
                files.append(parsed)
                continue
            for item in parsed.rejected:
                result["rejectedCount"] += 1
                if len(result["rejectedRows"]) < MAX_REJECTED:
                    result["rejectedRows"].append({"line": item["line"],
                                                   "reason": f"{_name(path)}: {item['reason']}"[:200]})
            for (code, instrument_id), columns in parsed.by_code.items():
                first = int(columns["time"][0])
                if instrument_id is None:
                    instrument_id = await in_worker(self._new_listing, parsed.market, code, first, import_id)
                    result["unmatchedCount"] += 1
                    if len(result["unmatched"]) < 50:
                        result["unmatched"].append(code[:32])
                else:
                    result["matched"] += 1
                record = self.market.identity.record(instrument_id)
                key = SeriesKey(instrument_id, "ohlcv", "last", "continuous" if record["identityVersion"] == 1 else "regular",
                                parsed.timeframe, import_id)
                await in_worker(self.market.store.write, key, columns,
                                fetched=[(int(columns["time"][0]), int(columns["close_time"][-1]))],
                                source=f"import:{import_id}")
                seen.add(instrument_id)
                result["rows"] += bars.length(columns)
                self.market.instrument_changed(instrument_id)
                files.append({"file": _name(path), "code": code, "instrumentId": instrument_id,
                              "timeframe": parsed.timeframe, "rows": bars.length(columns),
                              "rejected": len(parsed.rejected)})
        result["instruments"] = len(seen)
        if not seen:
            raise MarketError("invalid_file", t("No file held rows Market could read"),
                              details={"rejectedRows": result["rejectedRows"]})
        await in_worker(self.imports.write, import_id, {
            "importId": import_id, "name": spec.get("name") or _default_name(paths), "spec": spec, "files": files,
            "createdAt": str(now_ns()), "rows": result["rows"]})
        return result

    async def _parse(self, path: str, spec: dict, read) -> ParsedFile | dict:
        name = _name(path)
        if not name.lower().endswith((".csv", ".parquet")):
            return {"file": name, "problem": "only CSV and Parquet files are imported"}
        try:
            content = await read(path)
        except MarketError as exc:
            return {"file": name, "problem": exc.message}
        except OSError as exc:
            return {"file": name, "problem": f"cannot be read ({exc.strerror or exc})"}
        if len(content) > MAX_FILE:
            return {"file": name, "problem": "larger than 512 MB"}
        try:
            columns, rows = await in_worker(_rows_of, path, content)
        except (ValueError, UnicodeDecodeError, OSError) as exc:
            return {"file": name, "problem": f"is not readable CSV or Parquet ({exc})"[:200]}
        return await in_worker(self._parse_rows, path, columns, rows, spec)

    def _parse_rows(self, path: str, columns: list[str], rows: list[dict], spec: dict) -> ParsedFile | dict:
        name, stem = _name(path), Path(_name(path)).stem
        given = spec.get("columns") or {}
        mapped = {field: given.get(field) or _header(columns, names) for field, names in PRICES.items()}
        time_column = given.get("time") or _header(columns, TIME)
        if spec.get("kind") not in (None, "bars"):
            return {"file": name, "problem": f"imports of {spec['kind']} are not supported yet; give price bars"}
        if time_column is None or not all(mapped[field] for field in ("open", "high", "low", "close")):
            return {"file": name, "problem": "needs a time or date column and open, high, low and close"}
        code_column = spec.get("codeColumn") or _header(columns, CODE)
        file_code = spec.get("symbol") or _code_of_name(stem, spec.get("fileNameCode"))
        if code_column is None and not file_code:
            return {"file": name, "problem": "has no code column and its name gives no code"}
        parsed = ParsedFile(path)
        typed_market, _code = codes.parse_typed(file_code or "")
        prefix, sep, symbol = (file_code or "").partition(":")
        contract_venue = prefix if sep and "/" in symbol and "/" not in prefix else None
        venue = spec.get("market") or contract_venue or typed_market or "US"
        parsed.market = venue.upper() if venue.upper() in codes.MARKET_CALENDARS else venue.lower()
        calendar = codes.MARKET_CALENDARS.get(parsed.market, "24_7")
        exchange_zone = ZoneInfo(calendars.timezone(calendar))
        zone = ZoneInfo(spec["timezone"]) if spec.get("timezone") else exchange_zone
        grouped: dict[str, list] = {}
        date_only = True
        for index, row in enumerate(rows):
            line = index + 2
            code = (str(row.get(code_column) or "").strip() if code_column else "") or file_code
            if not code:
                parsed.rejected.append({"line": line, "reason": t("missing instrument code")})
                continue
            moment, is_date = _instant(row.get(time_column), zone, exchange_zone)
            if moment is None:
                parsed.rejected.append({"line": line, "reason": t("time {time_column} is not a date or time", time_column=repr(row.get(time_column)))})
                continue
            values = {field: _number(row.get(column)) if column else None for field, column in mapped.items()}
            missing = [field for field in ("open", "high", "low", "close") if values[field] is None]
            if missing:
                parsed.rejected.append({"line": line, "reason": t("{missing} is not a number", missing=', '.join(missing))})
                continue
            o, h, low, c = values["open"], values["high"], values["low"], values["close"]
            if h < max(o, c, low) or low > min(o, c, h):
                parsed.rejected.append({"line": line, "reason": t("high is below or low is above the other prices")})
                continue
            date_only = date_only and is_date
            grouped.setdefault(code.upper(), []).append((moment, o, h, low, c, values["volume"], line))
        if not grouped:
            return {"file": name, "problem": "no readable rows", "rejected": parsed.rejected}
        parsed.timeframe = normalize_timeframe(spec.get("timeframe") or _timeframe_of_name(stem) or
                                               _common_gap(grouped, date_only))
        for code, items in grouped.items():
            prefix, sep, symbol = code.partition(":")
            if sep and "/" in symbol and "/" not in prefix:
                if prefix.lower() != parsed.market.lower():
                    return {"file": name, "problem": f"{code} does not belong to {parsed.market}"}
                code = symbol
            if "/" in code and parsed.market.upper() in codes.MARKET_CALENDARS:
                return {"file": name, "problem": f"Specify the exchange of the existing contract {code}"}
            holders = self.market.identity.code_holders(parsed.market, code)
            times = np.array([row[0] for row in items], dtype=np.int64)
            step = timeframe_nanoseconds(parsed.timeframe)
            if step is None or step >= DAY_NS:
                opens = calendars.period_starts(calendar, parsed.timeframe, times)
                closes = calendars.period_ends(calendar, parsed.timeframe, opens)
            else:
                opens, closes = times, times + step
            groups = {}
            seen = set()
            for i, (moment, *_, line) in enumerate(items):
                matches = {id_ for lo, hi, id_ in holders if lo <= moment and (hi is None or moment < hi)}
                if len(matches) > 1 or not matches and (holders or parsed.market not in ("US", "CN", "HK")):
                    parsed.rejected.append({"line": line, "reason": t("No unique {market} instrument for {code} on this date", market=parsed.market, code=code)})
                    continue
                instrument_id = next(iter(matches), None)
                key = (instrument_id, opens[i])
                if key in seen:
                    parsed.rejected.append({"line": line, "reason": t("a second row for the same time")})
                    continue
                seen.add(key)
                groups.setdefault(instrument_id, []).append(i)
            for instrument_id, indices in groups.items():
                values = {field: np.array([items[i][j] for i in indices], dtype=np.float64)
                          for j, field in enumerate(("open", "high", "low", "close", "volume"), 1)}
                parsed.by_code[code, instrument_id] = bars.sort_unique(
                    {"time": opens[indices], "close_time": closes[indices], **values})
        if not parsed.by_code:
            return {"file": name, "problem": parsed.rejected[0]["reason"], "rejected": parsed.rejected}
        return parsed

    def _new_listing(self, market: str, code: str, first: int, import_id: str) -> str:
        return self.market.identity.upsert_listings(
            market, [{"code": code, "codeFrom": str(first), "assetClass": "equity", "name": None}],
            source=f"import:{import_id}")[0]

    # ── delete ──────────────────────────────────────────────────────────────

    async def delete(self, operation: dict) -> dict:
        store = self.market.store
        environment = operation.get("environment", "production")
        keys: list[SeriesKey] = []
        actions: list[str] = []
        if operation.get("importId"):
            if environment != "production":
                raise MarketError("invalid_input", t("Imported datasets do not belong to exchange test environments"))
            keys = [key for key in store.keys() if key.import_id == operation["importId"]]
            if not keys and self.imports.read(operation["importId"]) is None:
                raise MarketError("not_found", t("No import {importId}", importId=operation['importId']))
        elif operation.get("sourceId"):
            source = operation["sourceId"]
            keys = [key for key in store.keys(environment=environment) if (store.pointer(key) or {}).get("source") == source]
            actions = [i for i in self.market.actions.documents.ids()
                       if environment == "production" and self.market.actions.read(i).get("source") == source]
        else:
            if not operation.get("symbols"):
                raise MarketError("invalid_input", t("Name symbols with kinds, an importId or a sourceId"))
            wanted = operation.get("kinds") or [{"kind": "bars"}]
            for symbol in operation["symbols"]:
                record = (await self.market.instruments.resolve({"text": symbol, "environment": environment}))["instrument"]
                for key in store.keys(record["instrumentId"], environment=environment):
                    if key.import_id:
                        continue
                    if any(_kind_matches(item, key) for item in wanted):
                        keys.append(key)
                if environment == "production" and any(item["kind"] == "actions" for item in wanted):
                    actions.append(record["instrumentId"])
        deleted = 0
        for key in keys:
            pointer = store.pointer(key) or {}
            deleted += int(pointer.get("bytes") or 0)
            await in_worker(store.delete, key)
            self.market.sessions.series_replaced(key)
            self.market.instrument_changed(key.instrument_id)
        for instrument_id in actions:
            await in_worker(self.market.actions.documents.delete, instrument_id)
        if operation.get("importId"):
            await in_worker(self.imports.delete, operation["importId"])
        return {"importId": operation.get("importId"), "rows": 0, "instruments": len({k.instrument_id for k in keys}),
                "matched": 0, "unmatched": [], "rejectedRows": [], "deletedBytes": deleted, "rejectedCount": 0,
                "unmatchedCount": 0}


def _kind_matches(item: dict, key: SeriesKey) -> bool:
    kind = item["kind"]
    if kind == "bars":
        return key.capability == "ohlcv" and (not item.get("timeframe") or item["timeframe"] == key.timeframe)
    return key.capability == kind


def _common_gap(grouped: dict, date_only: bool) -> str:
    if date_only:
        return "1d"
    gaps: Counter = Counter()
    for items in grouped.values():
        times = np.asarray([item[0] for item in items], dtype=np.int64)
        gaps.update(np.diff(np.sort(times)).tolist())
    if not gaps:
        return "1d"
    gap = gaps.most_common(1)[0][0]
    if gap >= DAY_NS:
        return "1d"
    minutes = max(1, round(gap / (60 * NS)))
    return f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"


def _import_id(name: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:24]
    return (slug + "-" if slug else "") + uuid.uuid4().hex[:8]


def _default_name(paths: list[str]) -> str:
    folders = {str(Path(path.replace("\\", "/")).parent) for path in paths}
    return folders.pop() if len(folders) == 1 and folders != {"."} else _name(paths[0])
