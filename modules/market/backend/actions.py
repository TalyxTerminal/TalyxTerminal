"""Corporate actions per instrument and the adjustment factors read from them.

Only raw prices are stored. A cash dividend's factor is 1 - amount / the raw
close of the trading day before the ex-date (CRSP); splits and stock dividends
are ratios; sources that publish factors (Baostock, Tushare) are used as given.
Actions are known for the spans that were fetched; ``revision`` moves with them.
"""

from __future__ import annotations

import threading
from bisect import bisect_left, bisect_right

import numpy as np

from . import calendars
from .common import DAY_NS, MarketError
from .store import DocumentStore, SeriesKey, SeriesStore, Spans


class Actions:
    def __init__(self, documents: DocumentStore, store: SeriesStore):
        self.documents, self.store = documents, store
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        # instrument -> (actions revision, daily pointer revision, factors): a live bar's adjustment reads no file.
        self._factors: dict[tuple[str, SeriesKey | None], tuple[tuple, tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}

    def read(self, instrument_id: str) -> dict:
        return self.documents.read(instrument_id) or {"revision": 0, "spans": [], "rows": [], "source": None}

    def write(self, instrument_id: str, rows: list[dict], *, start: int, end: int, source: str, revision: int) -> bool:
        """Merge the actions of one fetched span; returns True when anything changed."""
        with self._guard:
            lock = self._locks.setdefault(instrument_id, threading.Lock())
        with lock:
            old = self.read(instrument_id)
            existing = {(r["exDate"], r["kind"]): r for r in old["rows"]}
            changed = False
            for row in rows:
                key = (row["exDate"], row["kind"])
                value = {**row, "sourceId": source}
                if existing.get(key) != value:
                    existing[key] = value
                    changed = True
            # A new document: the stored one is shared with readers (DocumentStore).
            doc = {**old, "rows": sorted(existing.values(), key=lambda r: (int(r["exDate"]), r["kind"])),
                   "spans": Spans.add(old["spans"], start, end, "have"), "source": source,
                   "revision": max(old["revision"] + 1, revision) if changed else old["revision"]}
            self.documents.write(instrument_id, doc)
            return changed

    def missing(self, instrument_id: str, start: int, end: int) -> list[tuple[int, int]]:
        return Spans.missing(self.read(instrument_id)["spans"], start, end)

    def factors(self, instrument_id: str, daily: SeriesKey | None, calendar: str = "24_7",
                after: int | None = None, before: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(ex-date times, split ratios, dividend factors) for adjusting this instrument; worked out again only when
        its actions or its daily closes changed."""
        doc = self.read(instrument_id)
        rows = self.events(doc["rows"], after, before)
        if not rows:
            return np.empty(0, np.int64), np.empty(0), np.empty(0)
        pointer = self.store.pointer(daily) if daily is not None else None
        # Keep one curve per series. Many different bar windows use the same range of events.
        version = (doc["revision"], pointer["revision"] if pointer else -1, rows[0]["exDate"], rows[-1]["exDate"])
        key = (instrument_id, daily)
        cached = self._factors.get(key)
        if cached is not None and cached[0] == version:
            return cached[1]
        factors = self._work_out(rows, self.store.read(daily) if pointer else {},
                                 self.reference_days(rows, calendar) if daily is not None else {},
                                 pointer["spans"] if pointer else [])
        self._factors[key] = (version, factors)
        return factors

    @staticmethod
    def events(rows, after, before):
        """Only ex-dates that can change a requested price or fall in a research window."""
        def ex(row):
            return int(row["exDate"])
        lo = bisect_right(rows, after, key=ex) if after is not None else 0
        hi = bisect_left(rows, before, key=ex) if before is not None else len(rows)
        return rows[lo:hi]

    @staticmethod
    def reference_days(rows, calendar):
        given = {int(row["exDate"]) for row in rows if row["kind"] == "factor"}
        days = {}
        for row in rows:
            ex = int(row["exDate"])
            if row["kind"] != "cash_dividend" or ex in given or not float(row["amount"] or 0):
                continue
            if calendars.canonical(calendar) == calendars.CONTINUOUS:
                days[ex] = ex - DAY_NS
            else:
                sessions = calendars._session_midnights(calendar)
                pos = int(sessions.searchsorted(ex)) - 1
                if pos < 0:
                    raise MarketError("no_data", f"No trading calendar before ex-date {ex}")
                days[ex] = int(sessions[pos])
        return days

    @staticmethod
    def _work_out(rows: list[dict], closes: dict, references: dict, spans: list) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        times, ratios, dividends = [], [], []
        # A source's own factor for an ex-date stands for that date's dividend and split rows.
        given = {int(row["exDate"]) for row in rows if row["kind"] == "factor"}
        for row in rows:
            ex = int(row["exDate"])
            if ex in given and row["kind"] != "factor":
                continue
            if row["kind"] in ("split", "stock_dividend"):
                times.append(ex)
                ratios.append(float(row["ratio"]))
                dividends.append(1.0)
            elif row["kind"] == "factor":
                times.append(ex)
                ratios.append(float(row.get("ratio") or 1.0))
                dividends.append(float(row["dividendFactor"]))
            elif ex in references and row["kind"] == "cash_dividend":
                previous = reference_close(closes, references[ex], spans)
                if previous is None:
                    raise MarketError("no_data", f"Missing daily reference close for cash dividend on {ex}")
                times.append(ex)
                ratios.append(1.0)
                dividends.append(1.0 - float(row["amount"]) / previous)
        return np.asarray(times, dtype=np.int64), np.asarray(ratios), np.asarray(dividends)


def reference_close(closes: dict, day: int, spans: list) -> float | None:
    if not closes:
        return None
    index = int(np.searchsorted(closes["time"], day, side="right")) - 1
    if index < 0 or not closes["close"][index] > 0:
        return None
    held = int(closes["time"][index])
    # A suspension can leave an earlier last trade. It is usable only if the source has
    # answered the intervening window, including the expected preceding session.
    if held != day and Spans.missing(spans, held + 1, day + 1):
        return None
    return float(closes["close"][index])
