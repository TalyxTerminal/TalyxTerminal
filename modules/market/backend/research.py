"""Research data: corporate actions and factors, index member history, classification, shares, financials.

Rows are stored per (instrument, kind) as small documents and read with their
``knownAt``: ``asOf`` keeps only what was knowable by then. Index members are
stored by the index's own instrument as codes with intervals and mapped to
instruments when read (market, code, date), so a later merge or an earlier code
period maps them without refetching; members that map to nothing stay, counted
as ``unmapped``. Sources answer through ``Source.research``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .common import MarketError, now_ns, ns_of_date
from .routing import in_worker
from .store import DocumentStore

KINDS = ("actions", "factors", "members", "classification", "shares", "financials", "earnings_dates", "market_cap",
         "categories")
STORED = ("members", "classification", "shares", "financials", "earnings_dates", "market_cap", "categories")
COLUMNS = {
    "actions": ("instrumentId", "exDate", "kind", "ratio", "amount", "currency", "recordDate", "payDate", "knownAt",
                "sourceId"),
    "factors": ("instrumentId", "exDate", "splitFactor", "dividendFactor", "knownAt", "basis"),
    "members": ("indexId", "instrumentId", "code", "from", "until", "knownAt"),
    "classification": ("instrumentId", "scheme", "level", "code", "name", "from", "until", "knownAt"),
    "shares": ("instrumentId", "date", "shares", "kind", "knownAt"),
    "financials": ("instrumentId", "metric", "periodEnd", "periodKind", "value", "unit", "currency", "knownAt",
                   "revision"),
    "earnings_dates": ("instrumentId", "date", "time", "epsEstimate", "epsActual", "knownAt"),
    "market_cap": ("instrumentId", "date", "value", "currency", "knownAt"),
    "categories": ("instrumentId", "category", "name", "knownAt"),
}


class Research:
    def __init__(self, market):
        self.market = market
        self.documents = {kind: DocumentStore(market.root / "research", kind) for kind in STORED}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    # ── sources and storage ─────────────────────────────────────────────────

    def source_for(self, instrument: dict, kind: str) -> str | None:
        ranked = self.market.registry.rank(instrument, kind, history=False)
        return ranked[0][0] if ranked else None

    def stored(self, instrument_id: str, kind: str) -> dict | None:
        return self.documents[kind].read(instrument_id)

    async def _commit(self, instrument_id: str, kind: str, document: dict) -> None:
        """Keep this instrument's fetch lock until its accepted answer is on disk, even when the job is stopped."""
        writing = asyncio.create_task(in_worker(self.documents[kind].write, instrument_id, document))
        cancelled = False
        while not writing.done():
            try:
                await asyncio.shield(writing)
            except asyncio.CancelledError:
                cancelled = True
        writing.result()
        if cancelled:
            raise asyncio.CancelledError

    async def ensure(self, instrument: dict, kind: str, environment: str = "production", *, refresh: bool = False,
                     start: int | None = None, end: int | None = None) -> dict | None:
        """The stored document, fetched once from the ranked source when there is none. A source that
        answers in parts (Baostock's shares, one quarter a request) is asked only for the parts in
        [start, end) not stored yet, and each part is stored as it arrives, so an interrupted fetch resumes."""
        instrument_id = instrument["instrumentId"]
        lock = self._locks.setdefault((instrument_id, kind), asyncio.Lock())
        async with lock:
            doc = self.stored(instrument_id, kind)
            source = self.source_for(instrument, kind)
            target = self.market.data.target(instrument, environment) if source else None
            parts_of = getattr(self.market.registry.entry(source).source, "research_parts", None) if source else None
            parts = parts_of(source, target, kind, start, end) if parts_of else None
            if parts is not None:
                return await self._parts(instrument_id, kind, source, target, parts, doc)
            if doc is not None and not refresh:
                return doc
            if source is None:
                if doc is not None:
                    return doc
                raise MarketError("unsupported", f"No configured source gives {kind} for {instrument['symbol']}")
            rows = await self.market.registry.call(source, "research", target, kind)
            doc = {"source": source, "fetchedAt": now_ns(), "rows": rows,
                   "revision": (doc["revision"] + 1) if doc else 1}
            await self._commit(instrument_id, kind, doc)
            return doc

    async def _parts(self, instrument_id: str, kind: str, source: str, target, parts: list[str], doc: dict | None
                     ) -> dict:
        if doc is None or doc.get("source") != source:
            doc = {"source": source, "fetchedAt": now_ns(), "rows": [], "revision": 0, "parts": []}
        done = set(doc.get("parts") or [])
        for part in parts:
            if part in done:
                continue
            rows = await self.market.registry.call(source, "research_part", target, kind, part)
            seen = {dumps(row) for row in doc["rows"]}
            done.add(part)
            # A new document each time: the stored one is shared with readers (DocumentStore).
            doc = {**doc, "rows": sorted([*doc["rows"], *(row for row in rows if dumps(row) not in seen)],
                                         key=lambda row: (row.get("date") or row.get("periodEnd") or "", dumps(row))),
                   "parts": sorted(done), "fetchedAt": now_ns(), "revision": doc["revision"] + 1}
            await self._commit(instrument_id, kind, doc)
        return doc

    async def fetch(self, instrument: dict, kind: dict, start, end, environment) -> int:
        """A fetch job item of a research kind; returns the rows now stored."""
        name = kind["kind"]
        if name in ("actions", "factors"):
            if instrument["identityVersion"] != 2:
                return 0
            from vocabulary.instruments import MarketInstrument, ohlcv_series
            series = ohlcv_series(MarketInstrument.from_wire(instrument), "1d",
                                  adjustment="split_dividend" if name == "factors" else "raw")
            plan = await in_worker(self.market.data.plan, instrument, series, start, environment=environment)
            await self.market.data.fill_actions(plan, start or ns_of_date("1970-01-02"), end or now_ns(), None,
                                                environment)
            return len((await in_worker(self.market.actions.read, instrument["instrumentId"]))["rows"])
        if name not in STORED:
            raise MarketError("invalid_input", f"{name} is not a research kind")
        # Only what is missing, as for bars: a stored document holds what the source knew when it was fetched, so
        # it is asked again only when the request reaches past that (or names no end, i.e. up to now).
        stored = await in_worker(self.stored, instrument["instrumentId"], name)
        refresh = stored is None or end is None or int(stored["fetchedAt"]) < end
        doc = await self.ensure(instrument, name, environment, refresh=refresh, start=start, end=end)
        return len(doc["rows"]) if doc else 0

    # ── members ─────────────────────────────────────────────────────────────

    async def index_record(self, index) -> dict:
        if isinstance(index, dict):
            return self.market.instrument_record(index)
        return (await self.market.instruments.resolve({"text": index}))["instrument"]

    @staticmethod
    def member_window(row: dict, start: int, end: int) -> tuple[int, int] | None:
        """The part of a member row inside [start, end), or None when they don't overlap.

        A member's code maps to any listing holding it during this part: a series fetched from ``start``
        begins at the first session's open (05:00 UTC for New York), after the window's first instant."""
        row_until = int(row["until"]) if row["until"] else end
        first, last = max(int(row["from"]), start), min(row_until, end)
        return (first, last) if first < last else None

    async def members_between(self, index, start: int | None, end: int | None, *,
                              candidates: bool = False) -> tuple[list[dict], int]:
        """Instruments that were members at any time in [start, end), and how many members map to none.

        A member's code maps only where the listing's code period is known to cover the date, and a listing
        from an exchange list starts when it was first seen until bars show it earlier. With ``candidates``
        (a fetch of the index), a member that maps to none is fetched under the listing holding its code
        today when that listing's start is still unknown: its bars then set the start, and reads map it
        exactly (a code reused later, as FB, ends up not covering the member's dates and stays unmapped)."""
        record = await self.index_record(index)
        doc = await self.ensure(record, "members")
        start = start if start is not None else 0
        end = end if end is not None else now_ns()
        found: dict[str, dict] = {}
        unmapped = 0
        for row in doc["rows"]:
            window = self.member_window(row, start, end)
            if window is None:
                continue
            instrument_id = self.market.identity.find_code_between(record["venueId"], row["code"], *window)
            if instrument_id is None and candidates:
                holder = self.market.identity.find_code(record["venueId"], row["code"])
                holder_record = self.market.identity.record(holder) if holder else None
                if holder_record and holder_record.get("listedFrom") is None:
                    instrument_id = holder
            if instrument_id is None:
                unmapped += 1
                continue
            found.setdefault(instrument_id, self.market.identity.record(instrument_id))
        return [value for value in found.values() if value], unmapped

    # ── reading ─────────────────────────────────────────────────────────────

    async def read(self, value: dict) -> dict:
        kind = value["kind"]
        fill = value.get("fill", "await") == "await"
        environment = value.get("environment", "production")
        as_of = int(value["asOf"]) if value.get("asOf") else None
        at = int(value["at"]) if value.get("at") else None
        start = int(value["start"]) if value.get("start") else None
        end = int(value["end"]) if value.get("end") else None
        limit = int(value.get("limit", 1000))
        offset = int(value["cursor"]) if value.get("cursor") else 0
        rows: list[dict] = []
        coverage: list[dict] = []
        unmapped, note = 0, None
        if kind == "members":
            if not value.get("index"):
                raise MarketError("invalid_input", "members needs the index instrument")
            index = self.market.instrument_record(value["index"])
            doc = await self._doc(index, kind, fill, environment, coverage)
            if doc is not None:
                note = self.market.registry.entry(doc["source"]).source.note(doc["source"], "members") \
                    if doc["source"] in self.market.registry.entries else None
                # A point read is the window [at, at + 1): members on that instant, mapped on it.
                first, last = (at, at + 1) if at is not None else (start or 0, end or now_ns())
                for row in doc["rows"]:
                    known = int(row.get("knownAt") or row["from"])
                    if as_of is not None and known > as_of:
                        continue
                    window = self.member_window(row, first, last)
                    if window is None:
                        continue
                    instrument_id = self.market.identity.find_code_between(index["venueId"], row["code"], *window)
                    unmapped += instrument_id is None
                    rows.append({"indexId": index["instrumentId"], "instrumentId": instrument_id, "code": row["code"],
                                 "from": str(int(row["from"])), "until": str(int(row["until"])) if row["until"] else None,
                                 "knownAt": str(known)})
        elif value.get("market") and not value.get("instruments"):
            rows, unmapped = await self._market_rows(value["market"], kind, at, as_of, fill, coverage)
        else:
            instruments = [self.market.instrument_record(item) for item in value.get("instruments") or []]
            if not instruments:
                raise MarketError("invalid_input", f"{kind} needs instruments")
            for instrument in instruments:
                window = value.get("instrumentWindows", {}).get(instrument["instrumentId"], {})
                rows.extend(await self._rows(instrument, kind, value, fill, environment, coverage,
                                             window.get("asOf", as_of), window.get("start", start),
                                             window.get("end", end), window.get("at", at)))
        if note is None:
            # A source's words about its rows (CoinGecko's required credit, a member list's bias).
            for item in coverage:
                source_id = (item.get("source") or {}).get("sourceId")
                if source_id in self.market.registry.entries:
                    note = self.market.registry.entry(source_id).source.note(source_id, kind)
                    if note:
                        break
        page = rows[offset:offset + limit]
        columns = {name: [row.get(name) for row in page] for name in COLUMNS[kind]}
        return {"kind": kind, "columns": columns, "total": len(rows), "coverage": coverage, "unmapped": unmapped, "note": note,
                "nextCursor": str(offset + limit) if offset + limit < len(rows) else None}

    async def _market_rows(self, market: str, kind: str, at: int | None, as_of: int | None, fill: bool,
                           coverage: list) -> tuple[list[dict], int]:
        """A whole market's rows at a date from a source that answers for the market in one request
        (Baostock's CSRC industries); stock codes map to instruments at that date."""
        if kind != "classification" or at is None:
            raise MarketError("invalid_input", "A market-wide read is for classification at a date (at)")
        probe = {"identityVersion": 2, "venueId": market, "assetClass": "equity", "marketType": "spot"}
        sources = [sid for sid, _row in self.market.registry.rank(probe, kind, history=False)
                   if hasattr(self.market.registry.entry(sid).source, "research_market")]
        key = f"market-{market}-{_day(at)}"
        doc = self.documents[kind].read(key)
        problem = None
        if doc is None and fill and sources:
            try:
                found = await self.market.registry.call(sources[0], "research_market", market, kind, at)
                doc = {"source": sources[0], "fetchedAt": now_ns(), "rows": found, "revision": 1}
                await in_worker(self.documents[kind].write, key, doc)
            except MarketError as exc:
                problem = exc.problem()
        elif doc is None and fill:
            problem = MarketError("unsupported", f"No configured source gives {kind} for the whole {market} market"
                                  ).problem()
        coverage.append({"instrumentId": f"market:{market}", "start": str(at) if doc else None,
                         "end": str(at) if doc else None,
                         "source": self.market.source_ref(doc["source"]) if doc else None, "problem": problem})
        rows, unmapped = [], 0
        for row in (doc or {}).get("rows", []):
            if as_of is not None and int(row["knownAt"]) > as_of:
                continue
            instrument_id = self.market.identity.find_code(market, row["stock"], at)
            unmapped += instrument_id is None
            rows.append({"instrumentId": instrument_id, **{k: v for k, v in row.items() if k != "stock"}})
        return rows, unmapped

    async def _doc(self, instrument, kind, fill, environment, coverage, start=None, end=None) -> dict | None:
        problem = None
        doc = self.stored(instrument["instrumentId"], kind)
        source = self.source_for(instrument, kind) if fill else None
        in_parts = source is not None and hasattr(self.market.registry.entry(source).source, "research_parts")
        if (doc is None or in_parts) and fill:
            try:
                doc = await self.ensure(instrument, kind, environment, start=start, end=end)
            except MarketError as exc:
                if exc.code in ("invalid_input",):
                    raise
                problem = exc.problem()
        source = self.market.source_ref(doc["source"]) if doc else None
        times = []
        if doc:
            for row in doc["rows"]:
                for name in ("knownAt", "from", "date", "periodEnd", "exDate"):
                    if row.get(name):
                        times.append(int(row[name]))
                        break
        coverage.append({"instrumentId": instrument["instrumentId"], "start": str(min(times)) if times else None,
                         "end": str(max(times)) if times else None, "source": source, "problem": problem})
        return doc

    async def _rows(self, instrument, kind, value, fill, environment, coverage, as_of, start, end, at) -> list[dict]:
        instrument_id = instrument["instrumentId"]
        if kind in ("actions", "factors"):
            if fill and instrument["identityVersion"] == 2:
                await self.fetch(instrument, {"kind": kind}, start, end, environment)
            doc = self.market.actions.read(instrument_id)
            coverage.append({"instrumentId": instrument_id,
                             "start": str(doc["spans"][0][0]) if doc["spans"] else None,
                             "end": str(doc["spans"][-1][1]) if doc["spans"] else None,
                             "source": self.market.source_ref(doc["source"]) if doc["source"] else None,
                             "problem": None})
            out = []
            for row in doc["rows"]:
                ex = int(row["exDate"])
                if (start is not None and ex < start) or (end is not None and ex >= end):
                    continue
                known = int(row.get("knownAt") or ex)
                if as_of is not None and known > as_of:
                    continue
                if kind == "actions" and row["kind"] != "factor":
                    out.append({"instrumentId": instrument_id, "exDate": str(ex), "kind": row["kind"],
                                "ratio": row.get("ratio"), "amount": row.get("amount"),
                                "currency": row.get("currency"), "recordDate": row.get("recordDate"),
                                "payDate": row.get("payDate"), "knownAt": str(known),
                                "sourceId": row.get("sourceId")})
            if kind == "factors":
                daily = self._daily_key(instrument)
                times, ratios, dividends = await in_worker(self.market.actions.factors, instrument_id, daily, instrument["calendarId"],
                                                           start - 1 if start is not None else None, end)
                given = {int(row["exDate"]) for row in doc["rows"] if row["kind"] == "factor"}
                for ex, ratio, dividend in zip(times.tolist(), ratios.tolist(), dividends.tolist(), strict=True):
                    if (start is not None and ex < start) or (end is not None and ex >= end):
                        continue
                    if as_of is not None and ex > as_of:
                        continue
                    out.append({"instrumentId": instrument_id, "exDate": str(ex), "splitFactor": _text(ratio),
                                "dividendFactor": _text(dividend), "knownAt": str(ex),
                                "basis": "source" if ex in given else "computed"})
            return out
        doc = await self._doc(instrument, kind, fill, environment, coverage, start, end)
        if doc is None:
            return []
        rows = [dict(row) for row in doc["rows"]]
        if as_of is not None:
            rows = [row for row in rows if int(row["knownAt"]) <= as_of]
        if kind == "financials":
            metrics = set(value.get("metrics") or [])
            kinds = set(value.get("periodKinds") or [])
            rows = [row for row in rows if (not metrics or row["metric"] in metrics)
                    and (not kinds or row["periodKind"] in kinds)]
            latest: dict[tuple, dict] = {}
            for row in sorted(rows, key=lambda r: int(r["knownAt"])):
                latest[(row["metric"], row["periodEnd"], row["periodKind"])] = row
            rows = sorted(latest.values(), key=lambda r: (r["metric"], int(r["periodEnd"])))
            if start is not None:
                rows = [row for row in rows if int(row["periodEnd"]) >= start]
            if end is not None:
                rows = [row for row in rows if int(row["periodEnd"]) < end]
        elif kind == "classification":
            if at is not None:
                rows = [row for row in rows if (row["from"] is None or int(row["from"]) <= at)
                        and (row["until"] is None or at < int(row["until"]))]
        elif kind in ("shares", "market_cap", "earnings_dates"):
            if start is not None:
                rows = [row for row in rows if int(row["date"]) >= start]
            if end is not None:
                rows = [row for row in rows if int(row["date"]) < end]
        return [{"instrumentId": instrument_id, **row} for row in rows]

    def _daily_key(self, instrument: dict):
        from vocabulary.instruments import MarketInstrument, ohlcv_series
        series = ohlcv_series(MarketInstrument.from_wire(instrument), "1d")
        return self.market.data.plan(instrument, series, None, local_only=True).key

    # ── describe ────────────────────────────────────────────────────────────

    async def describe(self, value: dict) -> dict:
        instruments = [self.market.instrument_record(item) for item in value.get("instruments") or []]
        kinds = value.get("kinds") or list(KINDS)
        out = []
        for kind in kinds:
            sources: dict[str | None, list[int]] = {}
            count = 0
            for instrument in instruments:
                if kind in ("actions", "factors"):
                    doc = self.market.actions.read(instrument["instrumentId"])
                    times = [int(r["exDate"]) for r in doc["rows"]]
                    source = doc["source"]
                else:
                    doc = self.stored(instrument["instrumentId"], kind) if kind in STORED else None
                    times = []
                    source = doc["source"] if doc else self.source_for(instrument, kind)
                    if doc:
                        times = [int(r.get("knownAt") or 0) for r in doc["rows"]]
                if times:
                    count += 1
                sources.setdefault(source, []).extend(times)
            source = next((s for s in sources if s), None)
            times = sources.get(source, [])
            out.append({"kind": kind, "source": self.market.source_ref(source) if source else None,
                        "instruments": count, "start": str(min(times)) if times else None,
                        "end": str(max(times)) if times else None})
        indices = []
        for source_id, entry in self.market.registry.entries.items():
            for index in getattr(entry.source, "indices", lambda _sid: [])(source_id):
                indices.append({**index, "source": self.market.source_ref(source_id)})
        return {"kinds": out, "indices": indices}


def _day(moment: int) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(moment // 10**9, UTC).strftime("%Y-%m-%d")


def _text(value: float) -> str:
    return format(round(value, 12), ".12g")


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))
