"""Batch reads use one selection of exact identities and membership intervals for rows and files."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from itertools import batched
from uuid import uuid4

import orjson
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from talyx_sdk.i18n import t

from vocabulary.instruments import MarketInstrument, market_data_series, ohlcv_series

from . import calendars
from .common import MarketError, now_ns
from .data import READ_REQUEST_LIMIT
from .public_identity import PublicIdentity, fits, instant
from .research import member_periods
from .routing import in_worker


def page_cursor(request):
    """A read_data cursor: the instrument to go on from and, inside a long history, its next row ("3" or "3:250")."""
    position, _, row = (request.get("cursor") or "0").partition(":")
    if not all(part.isascii() and part.isdecimal() for part in (position, row or "0")):
        raise MarketError("invalid_input", t("Invalid page cursor"))
    return int(position), int(row or 0)


def iso(value):
    return (
        pd.Timestamp(int(value), unit="ns", tz="UTC").isoformat().replace("+00:00", "Z")
        if value is not None
        else None
    )


def local_day(value, calendar):
    if value is None:
        return None
    return (
        pd.Timestamp(int(value), unit="ns", tz="UTC")
        .tz_convert(calendars.timezone(calendar))
        .date()
        .isoformat()
    )


def columns(names, units=None):
    return [{"name": name, "unit": (units or {}).get(name)} for name in names]


def make_series(record, request):
    model = MarketInstrument.from_wire(record)
    if request.get("data", "bars") != "bars":
        invalid = {"adjustment", "anchor", "sessionScope", "priceBasis"}.intersection(request)
        if invalid:
            raise MarketError("invalid_input", t("{invalid} applies only to bars", invalid=', '.join(sorted(invalid))))
        return market_data_series(model, request["data"], timeframe=request.get("timeframe"))
    adjustment = request.get("adjustment", "split") if model.adjustmentEligible else None
    if not model.adjustmentEligible and request.get("adjustment") not in (None, "raw"):
        raise MarketError("invalid_input", t("{symbol} does not support adjustment", symbol=record['symbol']))
    return ohlcv_series(
        model,
        request.get("timeframe") or "1d",
        adjustment=adjustment,
        adjustment_anchor=request.get("anchor"),
        price_basis=request.get("priceBasis", "last"),
        session_scope=request.get("sessionScope"),
    )


class PublicData(PublicIdentity):
    async def selections(self, request, *, missing=None):
        """(record, inclusive start ns, exclusive end ns); repeat a record if it re-entered the index."""
        index = request.get("index")
        if index is None:
            result = []
            symbols = list(dict.fromkeys(request["symbols"]))
            identifiers = await in_worker(
                lambda: {s: self.by_id(s) for s in symbols if s.startswith(("vi1_", "vi2_"))}
            )
            for symbol in symbols:
                record = identifiers.get(symbol) or await self.record(
                    symbol, environment=request.get("environment", "production")
                )
                calendar = record["calendarId"]
                result.append(
                    (
                        record,
                        instant(request.get("start"), calendar),
                        instant(request.get("end"), calendar, inclusive_end=True),
                    )
                )
            return result
        idx = await self.record(index["symbol"])
        calendar = idx["calendarId"]
        start = instant(request.get("start"), calendar)
        end = instant(request.get("end"), calendar, inclusive_end=True)
        at = instant(index.get("at"), calendar)
        doc = await self.market.research.ensure(idx, "members", start=at if at is not None else start,
                                                end=at + 1 if at is not None else end)

        def history():
            result = []
            seen = set()
            first, last = (at, at + 1) if at is not None else (start or 0, end or now_ns())
            members = doc["rows"]
            if at is None:
                members = member_periods(members)
            for row in members:
                window = self.market.research.member_window(row, first, last)
                if window is None:
                    continue
                code = row["code"]
                identity = self.identity.find_code_between(idx["venueId"], code, *window)
                if identity is None:
                    if missing is None:
                        raise MarketError(
                            "not_found", t("No instrument held {code} in its membership interval", code=code)
                        )
                    if code not in seen:
                        missing.append(
                            {
                                "symbol": code,
                                "reason": t("No matching listing in the membership interval"),
                            }
                        )
                        seen.add(code)
                    continue
                if at is not None and identity in seen:
                    continue
                seen.add(identity)
                result.append(
                    (self.identity.record(identity), *(window if at is None else (start, end)))
                )
            return result

        return await in_worker(history)

    def prepare(self, selections, request):
        data = self.market.data
        limit = request.get("limit") or 24
        estimate = 0
        prepared = []
        for record, start, end in selections:
            series = make_series(record, request)
            lo, hi, latest = data.window(record, series, start, end, None, limit)
            if hi <= lo:
                raise MarketError("invalid_input", t("end precedes start"))
            plan = data.plan(record, series, lo, environment=request.get("environment", "production"))
            gaps = data.missing(plan, lo, hi) if plan.source_id else []
            if gaps:
                layers = data.layers(plan, now_ns())
                estimate += layers.requests(layers.units(gaps)) if layers else len(gaps)
            actions_missing = False
            if series.adjustment in ("split", "split_dividend") and plan.source_id:
                _, action_gaps = data.action_gaps(plan, lo, hi)
                estimate += len(action_gaps)
                actions_missing = bool(action_gaps)
            if estimate > READ_REQUEST_LIMIT:
                raise MarketError(
                    "fill_too_large",
                    t("This read needs more than {READ_REQUEST_LIMIT} source requests; run market.fetch first", READ_REQUEST_LIMIT=READ_REQUEST_LIMIT),
                )
            prepared.append((record, series, plan, gaps, lo, hi, latest, actions_missing))
        return prepared

    async def read(self, request):
        file = request.get("format") == "file"
        if file and "cursor" in request:
            raise MarketError("invalid_input", t("A file contains the full selection; omit cursor"))
        missing = []
        selections = await self.selections(request, missing=missing)
        prepared = await in_worker(self.prepare, selections, request)
        environment = request.get("environment", "production")

        async def fill_one(entry):
            _, _, plan, gaps, lo, hi, _, actions_missing = entry
            if gaps:
                fill = await self.market.data.fill(
                    plan, lo, hi, limit=READ_REQUEST_LIMIT
                )
                if fill.problem:
                    problem = fill.problem
                    raise MarketError(
                        problem["code"],
                        problem["message"],
                        source_id=problem.get("sourceId"),
                        status=problem.get("status"),
                        details=problem,
                    )
            if actions_missing or plan.series.adjustment == "split_dividend":
                await self.market.data.fill_actions(plan, lo, hi, READ_REQUEST_LIMIT, environment)

        for group in range(0, len(prepared), 16):
            await asyncio.gather(*(fill_one(entry) for entry in prepared[group : group + 16]))
        if not file:
            return await in_worker(self.table, prepared, request, missing)
        handoff = self.market.handoff
        handoff.folder.mkdir(parents=True, exist_ok=True)
        path = handoff.folder / f".{uuid4().hex}.parquet"
        writing = asyncio.create_task(in_worker(self.table, prepared, request, missing, path))
        try:
            try:
                details = await asyncio.shield(writing)
            except asyncio.CancelledError:
                # The worker owns the open Parquet writer until it has finished.
                await writing
                raise
            return {
                **details,
                "file": await handoff.publish(path, "market-data", "market-data.parquet"),
            }
        finally:
            path.unlink(missing_ok=True)

    def instrument_table(self, windows, limit, file, membership_periods=False):
        record, series = windows[0][:2]
        symbol = self.spelling(record)
        zone = calendars.timezone(record["calendarId"])
        daily = series.timeframe and series.timeframe.endswith(("d", "w", "W", "M", "mo", "y"))
        tables, gaps = [], []
        for period, (_, _, plan, _, lo, hi, latest, _) in enumerate(windows, start=1):
            data = self.market.data
            raw = data.local_rows(plan, lo, hi)
            for gap in data.gaps(plan, lo, min(hi, now_ns())):
                gaps.append({**gap, "start": iso(gap["start"]), "end": iso(int(gap["end"]) - 1)})
            if not raw or not len(raw["time"]):
                continue
            arrays = {
                key: pa.array(value[-limit:] if latest else value)
                for key, value in raw.items()
                if key != "close_time"
            }
            times = arrays["time"].cast(pa.timestamp("ns", tz=zone if daily else "UTC"))
            if daily:
                times = pc.cast(pc.local_timestamp(times), pa.date32())
            arrays["time"] = times
            if membership_periods:
                arrays["membershipPeriod"] = pa.repeat(period, len(times))
            tables.append(
                pa.table(
                    {
                        "symbol": pa.repeat(symbol, len(times)),
                        "instrumentId": pa.repeat(record["instrumentId"], len(times)),
                        **arrays,
                    }
                )
            )
        table = pa.concat_tables(tables) if tables else None
        if table is not None and not file:
            table = table.drop(["instrumentId"])
        first = last = None
        if table is not None:
            times = table["time"]
            first, last = (times[i].as_py().isoformat() for i in (0, -1))
            if not file:
                text = (
                    times.cast(pa.string())
                    if daily
                    else pc.strftime(times, format="%Y-%m-%dT%H:%M:%SZ")
                )
                table = table.set_column(table.schema.get_field_index("time"), "time", text)
        return table, {
            "symbol": symbol,
            "instrumentId": record["instrumentId"],
            "name": record.get("name"),
            "source": plan.source_id,
            "timeframe": series.timeframe,
            "adjustment": series.adjustment,
            "anchor": series.adjustmentAnchor
            or ("latest" if series.adjustment in ("split", "split_dividend") else None),
            "priceBasis": series.priceBasis,
            "sessionScope": series.sessionScope,
            "timezone": zone,
            "units": {
                "price": record.get("currency") or record.get("quote"),
                "volume": record.get("base") or "shares",
            },
            "first": first,
            "last": last,
            "gaps": gaps,
        }

    def table(self, prepared, request, missing, path=None):
        limit, (start, skip) = request.get("limit"), page_cursor(request)
        groups = defaultdict(list)
        for window in prepared:
            groups[window[0]["instrumentId"]].append(window)
        names = ["symbol", "instrumentId", "time"]
        if path is None:
            names.remove("instrumentId")
        if request.get("data", "bars") == "bars":
            names += ["open", "high", "low", "close", "volume"]
        membership_periods = path is not None and "index" in request and not request["index"].get("at")
        if membership_periods:
            names.append("membershipPeriod")
        units = {key: "quote currency" for key in ("open", "high", "low", "close")}
        units["volume"] = "per-symbol volume unit"
        page = {
            "columns": columns(names, units),
            "rows": [],
            "meta": [],
            "nextCursor": None,
            "total": 0,
            "missing": missing,
        }

        def read_group(windows):
            return self.instrument_table(windows, limit or 24, path is not None, membership_periods)

        with ExitStack() as resources:
            writer = None
            workers = (
                resources.enter_context(ThreadPoolExecutor(max_workers=8))
                if len(groups) > 1
                else None
            )
            position = 0
            for batch in batched(groups.values(), 8):
                for table, meta in (
                    workers.map(read_group, batch) if workers else map(read_group, batch)
                ):
                    size = len(table) if table is not None else 0
                    page["total"] += size
                    if table is not None:
                        page["columns"] = columns(table.column_names, units)
                    if path is not None:
                        if table is not None:
                            if writer is None:
                                writer = resources.enter_context(
                                    pq.ParquetWriter(path, table.schema)
                                )
                            writer.write_table(table)
                        page["meta"].append(meta)
                    elif position >= start and page["nextCursor"] is None:
                        rows = page["rows"]
                        first = skip if position == start else 0
                        new = list(zip(*(c.to_pylist() for c in table.columns), strict=True))[first:] if size else []
                        room = len(new) if limit is None else min(len(new), limit - len(rows))
                        # The most of this instrument's rows that fit, measured with the longest cursor the page could carry.
                        cursor = f"{position}:{first + room}"
                        def fitting(count):
                            return fits({**page, "rows": rows + new[:count], "meta": page["meta"] + [meta], "nextCursor": cursor})

                        low, high = (0, room) if fitting(0) else (-1, -1)  # -1: not even the instrument's meta fits
                        while low < high:
                            middle = (low + high + 1) // 2
                            low, high = (middle, high) if fitting(middle) else (low, middle - 1)
                        whole = low == len(new)
                        if not whole and (low <= 0 and not page["meta"] or "index" in request):
                            raise MarketError(
                                "too_large",
                                t("This selection does not fit a page; request format=file or a shorter window"),
                            )
                        if low > 0 or whole:
                            rows.extend(new[:low])
                            page["meta"].append(meta)
                        if not whole:
                            # A long history goes on over pages: the cursor names the instrument and its next row.
                            row = first + max(low, 0)
                            page["nextCursor"] = f"{position}:{row}" if row else str(position)
                    position += 1
            if path is None:
                return page
            if writer is None:
                schema = pa.schema([(name, pa.null()) for name in names])
                writer = resources.enter_context(pq.ParquetWriter(path, schema))
            writer.add_key_value_metadata({"meta": orjson.dumps(page["meta"])})
        return {"columns": page["columns"], "total": page["total"], "missing": missing}
