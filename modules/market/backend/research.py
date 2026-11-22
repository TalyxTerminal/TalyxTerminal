"""Research data: corporate actions and factors, index member history, classification, shares, financials.

Rows are stored per (instrument, kind) as small documents and read with their
``knownAt``: ``asOf`` keeps only what was knowable by then. A refreshed classification
keeps the rows known before (a changed one ends where the new one starts), so what was
known at a time is not lost. Where a source answers a whole market's classification on a
date (Baostock's CSRC industries), one document per date is kept and an instrument's
classification over a window is read from those dates (its first day and each quarter
end): each row then has the time it was known from and, once changed, until. Index members are
stored by the index's own instrument as codes with intervals and mapped to
instruments when read (market, code, date), so a later merge or an earlier code
period maps them without refetching; members that map to nothing stay, counted
as ``unmapped``. Sources answer through ``Source.research``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from talyx_sdk.i18n import t

from .common import DAY_NS, MarketError, now_ns, ns_of_date
from .data import READ_REQUEST_LIMIT
from .routing import in_worker
from .store import DocumentStore

KINDS = ("actions", "factors", "members", "classification", "shares", "financials", "earnings_dates", "market_cap",
         "categories")
STORED = ("members", "classification", "shares", "financials", "earnings_dates", "market_cap", "categories")
COLUMNS = {
    "actions": ("instrumentId", "exDate", "kind", "ratio", "amount", "currency", "recordDate", "payDate", "knownAt",
                "sourceId"),
    "factors": ("instrumentId", "exDate", "splitFactor", "dividendFactor", "knownAt", "basis"),
    "members": ("indexId", "instrumentId", "code", "from", "until", "knownAt", "weight"),
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
        self.documents = {kind: DocumentStore(market.root / "research", kind) for kind in (*STORED, "weights")}
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
            target = self.market.data.target(instrument, environment, source) if source else None
            parts_of = getattr(self.market.registry.entry(source).source, "research_parts", None) if source else None
            parts = parts_of(source, target, kind, start, end) if parts_of else None
            if parts is not None:
                return await self._parts(instrument_id, kind, source, target, parts, doc)
            if doc is not None and not refresh:
                return doc
            if source is None:
                if doc is not None:
                    return doc
                raise MarketError("unsupported", t("No configured source gives {kind} for {symbol}", kind=kind, symbol=instrument['symbol']))
            rows = await self.market.registry.call(source, "research", target, kind)
            if kind == "classification" and doc is not None and doc["source"] == source:
                rows = merged_classification(doc["rows"], rows)
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
            if kind in ("members", "weights"):
                combined = member_periods([*doc["rows"], *rows])
            else:
                seen = {dumps(row) for row in doc["rows"]}
                combined = sorted([*doc["rows"], *(row for row in rows if dumps(row) not in seen)],
                                  key=lambda row: (row.get("date") or row.get("periodEnd") or "", dumps(row)))
            done.add(part)
            # A new document each time: the stored one is shared with readers (DocumentStore).
            doc = {**doc, "rows": combined,
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
            raise MarketError("invalid_input", t("{name} is not a research kind", name=name))
        if name == "classification" and self._market_sources(instrument["venueId"], name):
            days = self._classification_days(instrument, start, end)
            for day in days:
                await self._market_doc(instrument["venueId"], name, day)
            return len(days)
        # Only what is missing, as for bars: a stored document holds what the source knew when it was fetched, so
        # it is asked again only when the request reaches past that (or names no end, i.e. up to now).
        stored = await in_worker(self.stored, instrument["instrumentId"], name)
        refresh = stored is None or end is None or int(stored["fetchedAt"]) < end
        doc = await self.ensure(instrument, name, environment, refresh=refresh, start=start, end=end)
        if name == "members" and self.source_for(instrument, "weights"):
            await self.ensure(instrument, "weights", environment, start=start, end=end)
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
        doc = await self.ensure(record, "members", start=start, end=end)
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
                raise MarketError("invalid_input", t("members needs the index instrument"))
            index = self.market.instrument_record(value["index"])
            begin, stop = (at, at + 1) if at is not None else (start, end)
            doc = await self._doc(index, kind, fill, environment, coverage, begin, stop)
            if doc is not None:
                note = self.market.registry.entry(doc["source"]).source.note(doc["source"], "members") \
                    if doc["source"] in self.market.registry.entries else None
                weights = self.stored(index["instrumentId"], "weights")
                if fill and self.source_for(index, "weights"):
                    weights = await self._doc(index, "weights", fill, environment, coverage, begin, stop)
                first, last = begin or 0, stop or now_ns()
                members = doc["rows"]
                if weights:
                    members = weighted_members(members, weights["rows"], as_of)
                for row in members:
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
                                 "knownAt": str(known), "weight": row.get("weight")})
        elif value.get("market") and not value.get("instruments"):
            rows, unmapped = await self._market_rows(value["market"], kind, at, as_of, fill, coverage)
        else:
            instruments = [self.market.instrument_record(item) for item in value.get("instruments") or []]
            if not instruments:
                raise MarketError("invalid_input", t("{kind} needs instruments", kind=kind))
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

    def _market_sources(self, market: str, kind: str) -> list[str]:
        """The ranked sources that answer a whole market's ``kind`` on a date in one request."""
        probe = {"identityVersion": 2, "venueId": market, "assetClass": "equity", "marketType": "spot"}
        return [sid for sid, _row in self.market.registry.rank(probe, kind, history=False)
                if hasattr(self.market.registry.entry(sid).source, "research_market")]

    async def _market_doc(self, market: str, kind: str, at: int, *, fill: bool = True) -> dict | None:
        """The whole market's rows on the date of ``at`` (one stored document per date, never replaced: it is that date's
        answer); fetched from the first ranked source when missing and ``fill``."""
        key = f"market-{market}-{_day(at)}"
        doc = self.documents[kind].read(key)
        if doc is not None or not fill:
            return doc
        sources = self._market_sources(market, kind)
        if not sources:
            raise MarketError("unsupported", t("No configured source gives {kind} for the whole {market} market", kind=kind, market=market))
        async with self._locks.setdefault((key, kind), asyncio.Lock()):
            doc = self.documents[kind].read(key)
            if doc is None:
                found = await self.market.registry.call(sources[0], "research_market", market, kind, ns_of_date(_day(at)))
                doc = {"source": sources[0], "fetchedAt": now_ns(), "rows": found, "revision": 1}
                await in_worker(self.documents[kind].write, key, doc)
        return doc

    async def _market_rows(self, market: str, kind: str, at: int | None, as_of: int | None, fill: bool,
                           coverage: list) -> tuple[list[dict], int]:
        """A whole market's rows at a date from a source that answers for the market in one request
        (Baostock's CSRC industries); stock codes map to instruments at that date."""
        if kind != "classification" or at is None:
            raise MarketError("invalid_input", t("A market-wide read is for classification at a date (at)"))
        doc, problem = None, None
        try:
            doc = await self._market_doc(market, kind, at, fill=fill)
        except MarketError as exc:
            problem = exc.problem()
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

    def _classification_days(self, instrument: dict, start: int | None, end: int | None) -> list[int]:
        """The dates an instrument's classification over [start, end) is read on: the window's first day and every quarter end
        after it, from the quarter before its listing to its delisting. Without a start, only the end (or now)."""
        last = min(end, now_ns()) if end is not None else now_ns()
        if start is None:
            return [last]
        listed = instrument.get("listedFrom")
        delisted = instrument.get("delistedAt")
        first = int(listed) - 92 * DAY_NS if listed else None
        return [day for day in snapshot_days(start, last)
                if (first is None or day >= first) and (delisted is None or day <= int(delisted))]

    async def _classification_history(self, instrument: dict, fill: bool, coverage: list, as_of: int | None,
                                      start: int | None, end: int | None, at: int | None) -> list[dict]:
        """An instrument's classification over a window, from the whole market's documents on its dates: a row runs from the date
        it was first seen (its knownAt) until the date it was seen changed or gone, so a change shows a quarter late at most, never
        early."""
        market = instrument["venueId"]
        days = [at] if at is not None else self._classification_days(instrument, start, end)
        missing = [day for day in days if self.documents["classification"].read(f"market-{market}-{_day(day)}") is None]
        if fill and len(missing) > READ_REQUEST_LIMIT:
            raise MarketError("fill_too_large", t("{missing} dates of {market} classification are not stored yet; fetch them first (a download job)", missing=len(missing), market=market))
        problem, docs = None, []
        for day in days:
            try:
                docs.append((day, await self._market_doc(market, "classification", day, fill=fill)))
            except MarketError as exc:
                if exc.code == "invalid_input":
                    raise
                problem = exc.problem()
                docs.append((day, None))
        have = [day for day, doc in docs if doc is not None]
        source = next((doc["source"] for _day_, doc in docs if doc is not None), None)
        coverage.append({"instrumentId": instrument["instrumentId"], "start": str(have[0]) if have else None,
                         "end": str(have[-1]) if have else None,
                         "source": self.market.source_ref(source) if source else None, "problem": problem})
        codes = await in_worker(self.market.identity.codes, instrument["instrumentId"])
        snapshots = [(day, code_at(codes, day), doc) for day, doc in docs if doc is not None]
        rows = classification_history([(_snapshot_known(doc), (_snapshot_index(doc).get(code) if code else None))
                                       for _day_, code, doc in snapshots])
        if as_of is not None:
            rows = [row for row in rows if int(row["knownAt"]) <= as_of]
        return [{"instrumentId": instrument["instrumentId"], **row} for row in rows]

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
        if kind == "classification" and (start is not None or end is not None or at is not None) \
                and instrument["identityVersion"] == 2 and self._market_sources(instrument["venueId"], kind):
            return await self._classification_history(instrument, fill, coverage, as_of, start, end, at)
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
            if not self.market.registry.usable(entry):
                continue
            for index in getattr(entry.source, "indices", lambda _sid: [])(source_id):
                if self.source_for(index["index"], "members") != source_id:
                    continue
                indices.append({**index, "source": self.market.source_ref(source_id)})
        return {"kinds": out, "indices": indices}


def weighted_members(members, weights, as_of):
    """Split each membership at observed weight changes; keep members even when no weight is available."""
    by_code = {}
    for row in weights:
        if as_of is None or int(row["knownAt"]) <= as_of:
            by_code.setdefault(row["code"], []).append(row)
    for member in members:
        cursor = int(member["from"])
        end = int(member["until"]) if member["until"] is not None else None
        for weight in by_code.get(member["code"], ()):
            start = max(cursor, int(weight["from"]))
            stop = int(weight["until"])
            if end is not None:
                stop = min(end, stop)
            if start >= stop:
                continue
            if cursor < start:
                yield {**member, "from": str(cursor), "until": str(start)}
            yield {**member, "from": str(start), "until": str(stop), "weight": weight["weight"],
                   "knownAt": str(max(int(member["knownAt"]), int(weight["knownAt"])))}
            cursor = stop
        if end is None or cursor < end:
            yield {**member, "from": str(cursor)}


def member_periods(rows):
    """Coalesce adjacent snapshots so an unchanged member has one holding period."""
    periods = []
    for row in sorted(rows, key=lambda r: (r["code"], int(r["from"]))):
        if (periods and periods[-1]["code"] == row["code"]
                and (periods[-1]["until"] is None or int(periods[-1]["until"]) >= int(row["from"]))
                and periods[-1].get("weight") == row.get("weight")):
            ends = periods[-1]["until"], row["until"]
            periods[-1] = {**periods[-1], "until": None if None in ends else max(ends, key=int)}
        else:
            periods.append(row)
    return periods


def snapshot_days(start: int, end: int) -> list[int]:
    """UTC midnights: the day of ``start`` and every quarter end (Mar 31, Jun 30, Sep 30, Dec 31) after it, up to ``end``."""
    from datetime import UTC, date, datetime, timedelta
    first = datetime.fromtimestamp(start // 10**9, UTC).date()
    last = datetime.fromtimestamp(max(start, end) // 10**9, UTC).date()
    days = [first]
    year, quarter = first.year, (first.month - 1) // 3 + 1
    while True:
        following = date(year + (quarter == 4), quarter % 4 * 3 + 1, 1)
        close = following - timedelta(days=1)
        if close > last:
            break
        if close > first:
            days.append(close)
        year, quarter = following.year, (following.month - 1) // 3 + 1
    return [ns_of_date(day) for day in days]


def code_at(codes: list[dict], moment: int) -> str | None:
    """The code an instrument held at ``moment`` (its ``codes`` periods), or None."""
    for item in codes:
        if (item["from"] is None or int(item["from"]) <= moment) and (item["until"] is None or moment < int(item["until"])):
            return item["code"]
    return None


def _snapshot_index(doc: dict) -> dict[str, dict]:
    """A whole-market document's rows by stock code, built once per document (stored documents are never changed)."""
    index = _INDEXES.get(id(doc))
    if index is None or index[0] is not doc:
        index = (doc, {row["stock"]: row for row in doc["rows"]})
        _INDEXES[id(doc)] = index
        while len(_INDEXES) > 400:
            _INDEXES.pop(next(iter(_INDEXES)))
    return index[1]


_INDEXES: dict[int, tuple[dict, dict]] = {}


def _snapshot_known(doc: dict) -> str | None:
    """When a whole-market document's answer was known: its rows' (one) update date."""
    return min((row["knownAt"] for row in doc["rows"]), key=int, default=None)


def classification_history(snapshots: list[tuple[str | None, dict | None]]) -> list[dict]:
    """Rows over time from dated answers, oldest first: (when the answer was known, this instrument's row in it or None). A row
    starts when it was first seen and ends when an answer shows another one or none."""
    out: list[dict] = []
    current = None
    for known, row in snapshots:
        if row is None:
            if current is not None and known is not None:
                current["until"] = known
            current = None
            continue
        value = {key: row[key] for key in ("scheme", "level", "code", "name")}
        if current is not None and all(current[key] == value[key] for key in value):
            continue
        if current is not None:
            current["until"] = row["knownAt"]
        current = {**value, "from": row["knownAt"], "until": None, "knownAt": row["knownAt"]}
        out.append(current)
    return out


def merged_classification(old: list[dict], new: list[dict]) -> list[dict]:
    """A source's new answer added to what it said before: an unchanged row stays as it was; a changed one ends where the new
    one starts (its own start, else when it was known), so a read as of an earlier time still finds the earlier row."""
    out = [dict(row) for row in old]
    for row in new:
        open_rows = [item for item in out if (item["scheme"], item["level"]) == (row["scheme"], row["level"])
                     and item["until"] is None]
        latest = max(open_rows, key=lambda item: int(item["knownAt"]), default=None)
        if latest is not None and (latest["code"], latest["name"]) == (row["code"], row["name"]):
            continue
        begins = row["from"] if row["from"] is not None else (row["knownAt"] if latest is not None else None)
        if latest is not None:
            latest["until"] = begins
        out.append({**row, "from": begins})
    return out


def _day(moment: int) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(moment // 10**9, UTC).strftime("%Y-%m-%d")


def _text(value: float) -> str:
    return format(round(value, 12), ".12g")


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))
