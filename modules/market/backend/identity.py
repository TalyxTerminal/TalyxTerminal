"""The identity table: instruments, code history, external ids and search.

Listings (identityVersion 2) get a random id the first time Market sees them and
keep it through renames, code changes, exchange moves and source changes. Codes
are (market, code, start, end) periods pointing at an id; a date before a code's
start does not match (codes are reused), so the start only moves earlier when a
source returns an earlier bar. Venue contracts (identityVersion 1, crypto) hash
their fields; a reused venue symbol is a separate contract with ``retiredAt``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from vocabulary.instruments import MarketInstrument

from . import codes as codes_module
from .common import DAY_NS, MarketError, now_ns
from .display import presented, typed_codes

FAR = 2**62
SCHEMA = """
create table if not exists instruments(
  id text primary key, kind text not null, market text not null, code text not null, name text,
  asset_class text, active integer not null default 1, merged_into text, record text not null, created integer);
create index if not exists instruments_market on instruments(market, asset_class);
create index if not exists instruments_name on instruments(name collate nocase);
create table if not exists codes(
  market text not null, code text not null collate nocase, start integer not null, end integer, id text not null,
  name text);
create index if not exists codes_code on codes(code collate nocase, market);
create index if not exists codes_id on codes(id);
create table if not exists source_keys(source text not null, key text not null, start integer not null, id text not null,
  end integer, primary key(source, key, start));
create table if not exists conflicts(id text not null, code text not null, start integer, end integer, other text not null,
  claimed_by text not null, reason text not null);
create table if not exists catalogs(venue text not null, family text not null, state text not null, problem text,
  loaded_at integer, revision text, primary key(venue, family));
create table if not exists segments(venue text not null, family text not null, native text not null,
  value text not null, primary key(venue, family, native));
create virtual table if not exists names_prefix using fts5(id unindexed, name, prefix='1 2 3');
create virtual table if not exists names_tri using fts5(id unindexed, name, tokenize='trigram');
"""


def new_listing_id() -> str:
    return "vi2_" + uuid.uuid4().hex


class IdentityStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._local = threading.local()
        self._write = threading.RLock()
        with self.db() as db:
            db.executescript(SCHEMA)
        self.revision = now_ns()

    def db(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            connection.execute("pragma journal_mode=wal")
            connection.execute("pragma synchronous=normal")
            self._local.connection = connection
        return connection

    # ── records ─────────────────────────────────────────────────────────────

    def record(self, instrument_id: str) -> dict | None:
        row = self.db().execute("select record, merged_into from instruments where id=?", (instrument_id,)).fetchone()
        if row is None:
            return None
        if row[1]:
            return self.record(row[1])
        return presented(json.loads(row[0]))

    def survivor(self, instrument_id: str) -> str | None:
        row = self.db().execute("select merged_into from instruments where id=?", (instrument_id,)).fetchone()
        if row is None:
            return None
        return self.survivor(row[0]) if row[0] else instrument_id

    def instrument(self, instrument_id: str) -> MarketInstrument:
        record = self.record(instrument_id)
        if record is None:
            raise MarketError("not_found", f"Market does not know instrument {instrument_id}")
        return MarketInstrument.from_wire(record)

    def codes(self, instrument_id: str) -> list[dict]:
        rows = self.db().execute("select code, name, start, end from codes where id=? order by start",
                                 (instrument_id,)).fetchall()
        return [{"code": code, "name": name, "from": str(start) if start > -FAR else None,
                 "until": None if end is None else str(end)} for code, name, start, end in rows]

    def external_ids(self, instrument_id: str) -> dict[str, str]:
        rows = self.db().execute("select source, key from source_keys where id=?", (instrument_id,)).fetchall()
        return {source: key for source, key in rows}

    def conflicts(self, instrument_id: str) -> list[dict]:
        rows = self.db().execute("select code, start, end, other, claimed_by, reason from conflicts where id=?",
                                 (instrument_id,)).fetchall()
        return [{"code": c, "from": None if s is None else str(s), "until": None if e is None else str(e),
                 "otherInstrumentId": o, "claimedBy": by, "reason": r} for c, s, e, o, by, r in rows][:50]

    # ── writes ──────────────────────────────────────────────────────────────

    def _put(self, db, record: dict, *, kind: str, market: str, code: str) -> None:
        text = json.dumps(record)
        name = record.get("name")
        old = db.execute("select record, name from instruments where id=?", (record["instrumentId"],)).fetchone()
        if old is not None and old[0] == text:
            return  # unchanged (catalog refreshes rewrite nothing)
        db.execute(
            "insert into instruments(id, kind, market, code, name, asset_class, active, record, created) "
            "values(?,?,?,?,?,?,?,?,?) on conflict(id) do update set code=excluded.code, name=excluded.name, "
            "asset_class=excluded.asset_class, active=excluded.active, record=excluded.record",
            (record["instrumentId"], kind, market, code, name, record["assetClass"],
             0 if record.get("active") is False else 1, text, now_ns()))
        if old is not None and old[1] == name:
            return
        if old is not None and old[1]:
            # The name tables are not indexed by id: only an actual rename pays for the scan.
            db.execute("delete from names_prefix where id=?", (record["instrumentId"],))
            db.execute("delete from names_tri where id=?", (record["instrumentId"],))
        if name:
            db.execute("insert into names_prefix(id, name) values(?,?)", (record["instrumentId"], name))
            db.execute("insert into names_tri(id, name) values(?,?)", (record["instrumentId"], name))

    def find_code(self, market: str, code: str, at: int | None = None) -> str | None:
        at = now_ns() if at is None else at
        rows = self.db().execute(
            "select id from codes where market=? and code=? collate nocase and start<=? and (end is null or end>?) "
            "order by start desc", (market, code, at, at)).fetchall()
        return self.survivor(rows[0][0]) if rows else None

    def code_holders(self, market: str, code: str) -> list[tuple[int, int | None, str]]:
        """All holders and the half-open intervals for matching a file of dated prices."""
        db = self.db()
        rows = db.execute("select c.start, c.end, c.id, i.created from codes c join instruments i on c.id=i.id "
                          "where c.market=? and c.code=? collate nocase order by c.start", (market, code)).fetchall()
        if len(rows) == 1:
            start, end, id_, first_seen = rows[0]
            if start == first_seen and end is None and not self.record(id_).get("listedFrom") and len(self.codes(id_)) == 1:
                # A current directory entry with no effective date starts at discovery in the code index.
                return [(-FAR, None, self.survivor(id_))]
        return [(lo, hi, self.survivor(id_)) for lo, hi, id_, _ in rows]

    def find_code_between(self, market: str, code: str, start: int, end: int) -> str | None:
        """The listing that held ``code`` at some time in [start, end), the latest to take it up first."""
        rows = self.db().execute(
            "select id from codes where market=? and code=? collate nocase and start<? and (end is null or end>?) "
            "order by start desc", (market, code, end, start)).fetchall()
        return self.survivor(rows[0][0]) if rows else None

    def find_source_key(self, source: str, key: str) -> str | None:
        row = self.db().execute("select id from source_keys where source=? and key=?", (source, key)).fetchone()
        return self.survivor(row[0]) if row else None

    def upsert_listings(self, market: str, rows: list[dict], *, source: str, naming: str = "replace") -> list[str]:
        """Load listing candidates (a catalog page or search results) into the table in one transaction."""
        ids, now = [], now_ns()
        calendar = codes_module.MARKET_CALENDARS.get(market, "24_7")
        with self._write:
            db = self.db()
            periods: dict[str, list[list]] = {}
            for rowid, code, start, end, instrument_id in db.execute(
                    "select rowid, code, start, end, id from codes where market=?", (market,)):
                periods.setdefault(code.upper(), []).append([rowid, start, end, instrument_id])
            keys = {key: instrument_id for key, instrument_id in
                    db.execute("select key, id from source_keys where source=?", (source,))}
            merged = {i: m for i, m in db.execute("select id, merged_into from instruments where market=? and "
                                                  "merged_into is not null", (market,))}
            records = {i: r for i, r in db.execute("select id, record from instruments where market=?", (market,))}

            def survivor(instrument_id):
                while instrument_id in merged:
                    instrument_id = merged[instrument_id]
                return instrument_id

            def at(code, moment):
                matches = [p for p in periods.get(code.upper(), []) if p[1] <= moment and (p[2] is None or p[2] > moment)]
                return survivor(max(matches, key=lambda p: p[1])[3]) if matches else None

            puts, code_updates, code_inserts, key_inserts = [], [], [], []
            names, renamed = {}, []
            for row in rows:
                code = row["code"]
                until = int(row["codeUntil"]) if row.get("codeUntil") else None
                start = int(row["codeFrom"]) if row.get("codeFrom") else None
                found = survivor(keys[row["sourceKey"]]) if row.get("sourceKey") in keys else None
                if found is None:
                    found = at(code, (until - 1) if until else now)
                    if found is None and start is not None:
                        found = at(code, start)
                instrument_id = found or new_listing_id()
                previous = json.loads(records[instrument_id]) if found and instrument_id in records else None
                if naming == "fill" and previous and previous.get("name"):
                    # A search answer (Yahoo's English name) does not replace the exchange list's name
                    # (the local listing name stays searchable); it only names a listing that has none.
                    row = {**row, "name": previous["name"]}
                other_current = (previous is not None and previous["symbol"] != code and
                                 at(previous["symbol"], now) == instrument_id)
                record = self._listing_record(instrument_id, market, calendar, row, previous, other_current)
                records[instrument_id] = json.dumps(record)
                puts.append((instrument_id, "listing", market, record["symbol"], record.get("name"), record["assetClass"],
                             0 if record.get("active") is False else 1, records[instrument_id], now))
                if record.get("name") and (previous is None or previous.get("name") != record["name"]):
                    # The FTS id is unindexed. A fresh directory has no old names to scan and delete.
                    if previous and previous.get("name") and instrument_id not in names:
                        renamed.append((instrument_id,))
                    names[instrument_id] = record["name"]
                existing = [p for p in periods.get(code.upper(), []) if p[3] == instrument_id]
                if existing:
                    period = existing[0]
                    new_start = min(period[1], start) if start is not None else period[1]
                    new_end = until if until is not None else (period[2] if period[2] is not None and period[2] <= now else None)
                    if (new_start, new_end) != (period[1], period[2]):
                        code_updates.append((new_start, new_end, row.get("name"), period[0]))
                        period[1], period[2] = new_start, new_end
                else:
                    value = [None, start if start is not None else now, until, instrument_id]
                    periods.setdefault(code.upper(), []).append(value)
                    code_inserts.append((market, code, value[1], until, instrument_id, row.get("name")))
                if row.get("sourceKey"):
                    keys[row["sourceKey"]] = instrument_id
                    key_inserts.append((source, row["sourceKey"], start or -FAR, instrument_id, until))
                for name, key in (row.get("externalIds") or {}).items():
                    key_inserts.append((name, key, -FAR, instrument_id, None))
                ids.append(instrument_id)
            db.execute("begin")
            try:
                db.executemany(
                    "insert into instruments(id, kind, market, code, name, asset_class, active, record, created) "
                    "values(?,?,?,?,?,?,?,?,?) on conflict(id) do update set code=excluded.code, name=excluded.name, "
                    "asset_class=excluded.asset_class, active=excluded.active, record=excluded.record", puts)
                db.executemany("delete from names_prefix where id=?", renamed)
                db.executemany("delete from names_tri where id=?", renamed)
                db.executemany("insert into names_prefix(id, name) values(?,?)", names.items())
                db.executemany("insert into names_tri(id, name) values(?,?)", names.items())
                db.executemany("update codes set start=?, end=?, name=coalesce(?, name) where rowid=?", code_updates)
                db.executemany("insert into codes(market, code, start, end, id, name) values(?,?,?,?,?,?)", code_inserts)
                db.executemany("insert or ignore into source_keys(source, key, start, id, end) values(?,?,?,?,?)",
                               key_inserts)
                db.execute("commit")
            except BaseException:
                db.execute("rollback")
                raise
        self.revision = now_ns()
        return ids

    def _listing_record(self, instrument_id: str, market: str, calendar: str, row: dict, previous: dict | None,
                        other_current: bool) -> dict:
        asset = row.get("assetClass") or (previous or {}).get("assetClass") or "equity"
        active = row.get("delistedAt") is None and row.get("codeUntil") is None
        if not active and other_current:
            # A former code of a listing that still trades keeps the current code.
            active, current = True, previous["symbol"]
        else:
            current = row["code"]
        record = {
            "instrumentId": instrument_id, "identityVersion": 2, "venueId": market, "symbol": current,
            "displaySymbol": current, "marketType": "spot", "assetClass": asset, "base": None, "quote": None,
            "settle": None, "contractOrientation": "none", "contractSize": None, "expiry": None, "strike": None,
            "optionSide": None, "underlyingSymbol": None, "retiredAt": None, "calendarId": row.get("calendarId") or calendar,
            "adjustmentEligible": asset in ("equity", "fund"),
            "name": row.get("name") or (previous or {}).get("name"),
            "currency": row.get("currency") or codes_module.MARKET_CURRENCIES.get(market),
            "securityType": row.get("securityType"), "mic": row.get("mic"),
            "listedFrom": (previous or {}).get("listedFrom") or row.get("listedFrom"),
            "delistedAt": None if active else row.get("delistedAt"), "active": active,
        }
        return {key: value for key, value in record.items() if value is not None or key in _REQUIRED_NULLS}

    def _add_code(self, db, instrument_id: str, market: str, code: str, start: int | None, until: int | None,
                  name: str | None, now: int) -> None:
        rows = db.execute("select rowid, start, end from codes where id=? and market=? and code=? collate nocase",
                          (instrument_id, market, code)).fetchall()
        if rows:
            rowid, old_start, old_end = rows[0]
            new_start = min(old_start, start) if start is not None else old_start
            new_end = until if until is not None else (old_end if old_end is not None and old_end <= now else None)
            db.execute("update codes set start=?, end=?, name=coalesce(?, name) where rowid=?",
                       (new_start, new_end, name, rowid))
            return
        db.execute("insert into codes(market, code, start, end, id, name) values(?,?,?,?,?,?)",
                   (market, code, start if start is not None else now, until, instrument_id, name))

    def upsert_contracts(self, venue: str, records: list[dict]) -> list[str]:
        """Venue contracts (crypto): id from the contract fields; code = venue symbol."""
        ids = []
        with self._write:
            db = self.db()
            db.execute("begin")
            try:
                for raw in records:
                    instrument = MarketInstrument.from_wire({**raw, "identityVersion": 1, "instrumentId": None})
                    record = instrument.wire_dict()
                    self._put(db, record, kind="contract", market=venue, code=record["symbol"])
                    exists = db.execute("select 1 from codes where id=?", (record["instrumentId"],)).fetchone()
                    if not exists:
                        # retiredAt is the day of the contract's last real trade: its code holds through that day.
                        until = int(record["retiredAt"]) + DAY_NS if record.get("retiredAt") else None
                        db.execute("insert into codes(market, code, start, end, id, name) values(?,?,?,?,?,?)",
                                   (venue, record["symbol"], -FAR, until, record["instrumentId"], record.get("name")))
                    ids.append(record["instrumentId"])
                db.execute("commit")
            except BaseException:
                db.execute("rollback")
                raise
        self.revision = now_ns()
        return ids

    def segments(self, venue: str, family: str, native: str) -> dict | None:
        """Where a reused exchange code changed hands (frozen once found; see sources/binance_files.py)."""
        row = self.db().execute("select value from segments where venue=? and family=? and native=?",
                                (venue, family, native)).fetchone()
        return json.loads(row[0]) if row else None

    def save_segments(self, venue: str, family: str, native: str, value: dict, current_id: str | None,
                      retired: list[dict]) -> None:
        """Freeze the segments: retired contracts get their own ids; the current code starts at the last cut."""
        with self._write:
            db = self.db()
            db.execute("insert or replace into segments(venue, family, native, value) values(?,?,?,?)",
                       (venue, family, native, json.dumps(value)))
        if retired:
            self.upsert_contracts(venue, retired)
        if current_id and value.get("cuts"):
            with self._write:
                self.db().execute("update codes set start=? where id=? and end is null",
                                  (int(value["cuts"][-1]["cut"]), current_id))
            self.revision = now_ns()

    def segments_of(self, venue: str, family: str) -> dict[str, dict]:
        rows = self.db().execute("select native, value from segments where venue=? and family=?",
                                 (venue, family)).fetchall()
        return {native: json.loads(value) for native, value in rows}

    def save_code_change(self, market: str, old: str, value: dict) -> None:
        """A checked A-share code change (or a delisted code checked and found to be none)."""
        with self._write:
            self.db().execute("insert or replace into segments(venue, family, native, value) values(?,?,?,?)",
                              (market, "code_change", old, json.dumps(value)))

    def apply_rename(self, market: str, old: str, new: str, at: int, source: str) -> dict:
        """A source says the listing trading as ``new`` from ``at`` traded as ``old`` before (FB → META).

        The listing that holds ``new`` now gets ``old`` until ``at`` and ``new`` from ``at``. A separate
        inactive listing whose ``old`` period ends at the rename is the same company and is merged into it
        (returned as ``merged``); a listing still trading as ``old`` before ``at`` contradicts the source:
        the rename is recorded as a conflict and not applied. Apply renames newest first so a later
        rename's old code is in place when an earlier one names it as new.
        """
        key = f"{source}:{old}>{new}@{at}"
        with self._write:
            db = self.db()
            if db.execute("select 1 from segments where venue=? and family='rename' and native=?",
                          (market, key)).fetchone():
                return {"state": "known"}
            rows = db.execute("select rowid, id, start, end from codes where market=? and code=? collate nocase",
                              (market, new)).fetchall()
            holders = [r for r in rows if r[3] is None or r[3] > at]
            if not holders:
                return {"state": "unknown"}
            rowid, instrument_id, start, end = max(holders, key=lambda r: r[2])
            instrument_id = self.survivor(instrument_id) or instrument_id
            others = [r for r in rows if r[0] != rowid and (r[3] is None or r[3] > at) and r[2] < (end or FAR)
                      and r[2] < at]
            if others:
                self.add_conflict(instrument_id, new, at, end, others[0][1], source,
                                  f"{source}: {old} became {new} on this date, but another listing used {new} then")
                return {"state": "conflict"}
            # The listing's history before the rename (a source's bars under the new code) belongs to the old code.
            old_start = start if -FAR < start < at else -FAR
            db.execute("update codes set start=? where rowid=?", (at, rowid))
            used = db.execute("select rowid, id, start, end from codes where market=? and code=? collate nocase "
                              "and start < ?", (market, old, at)).fetchall()
            merged = None
            for other_rowid, other_id, other_start, other_end in used:
                other_id = self.survivor(other_id) or other_id
                if other_id == instrument_id:
                    old_start = other_start if old_start == -FAR else min(old_start, other_start)
                    db.execute("delete from codes where rowid=?", (other_rowid,))
                    continue
                if other_end is not None and other_end <= at - 30 * DAY_NS:
                    old_start = max(old_start, other_end)  # an earlier holder of the code: ours starts after it
                    continue
                record = self.record(other_id)
                if other_end is not None and abs(other_end - at) <= 7 * DAY_NS and record and \
                        record.get("active") is False:
                    merged = other_id  # the same company, known separately under its old code
                    old_start = other_start if old_start == -FAR else min(old_start, other_start)
                    db.execute("delete from codes where rowid=?", (other_rowid,))
                    continue
                db.execute("update codes set start=? where rowid=?", (start, rowid))
                self.add_conflict(instrument_id, old, None, at, other_id, source,
                                  f"{source}: {new} traded as {old} until this date, but another listing used {old}")
                return {"state": "conflict"}
            name = db.execute("select name from codes where rowid=?", (rowid,)).fetchone()[0]
            db.execute("insert into codes(market, code, start, end, id, name) values(?,?,?,?,?,?)",
                       (market, old, old_start, at, instrument_id, name))
            db.execute("insert or replace into segments(venue, family, native, value) values(?,?,?,?)",
                       (market, "rename", key, json.dumps({"instrumentId": instrument_id, "merged": merged})))
        self.revision = now_ns()
        return {"state": "applied", "instrumentId": instrument_id, "merged": merged}

    def adopt(self, record: dict) -> None:
        """Take another machine's listing record with its id."""
        with self._write, self.db() as db:
            db.execute("begin")
            self._put(db, record, kind="listing", market=record["venueId"], code=record["symbol"])
            start = int(record["listedFrom"]) if record.get("listedFrom") else now_ns()
            self._add_code(db, record["instrumentId"], record["venueId"], record["symbol"], start,
                           int(record["delistedAt"]) if record.get("delistedAt") else None, record.get("name"), now_ns())

    def alias(self, alias_id: str, survivor_id: str, record: dict) -> None:
        with self._write, self.db() as db:
            db.execute("begin")
            self._put(db, {**record, "instrumentId": alias_id}, kind="listing", market=record["venueId"],
                      code=record["symbol"])
            db.execute("update instruments set merged_into=? where id=?", (survivor_id, alias_id))

    def observe_first_bar(self, instrument_id: str, first_time: int) -> bool:
        """A source returned a bar at ``first_time``: listedFrom and the first code period
        start no later than it. Returns True when anything moved."""
        record = self.record(instrument_id)
        if record is None or record["identityVersion"] != 2:
            return False
        changed = False
        with self._write:
            db = self.db()
            if record.get("listedFrom") is None or int(record["listedFrom"]) > first_time:
                record["listedFrom"] = str(first_time)
                self._put(db, record, kind="listing", market=record["venueId"], code=record["symbol"])
                changed = True
            row = db.execute("select rowid, start from codes where id=? order by start limit 1",
                             (instrument_id,)).fetchone()
            if row and row[1] > first_time:
                db.execute("update codes set start=? where rowid=?", (first_time, row[0]))
                changed = True
        return changed

    def set_code_periods(self, instrument_id: str, market: str, periods: list[tuple[str, int, int | None]]) -> None:
        """Replace the code history of an instrument (A-share code change, rename evidence)."""
        with self._write:
            db = self.db()
            db.execute("delete from codes where id=?", (instrument_id,))
            for code, start, end in periods:
                db.execute("insert into codes(market, code, start, end, id) values(?,?,?,?,?)",
                           (market, code, start, end, instrument_id))
            current = next((code for code, _s, end in periods if end is None), None)
            record = self.record(instrument_id)
            if current and record:
                record["symbol"] = record["displaySymbol"] = current
                record["active"] = True
                record.pop("delistedAt", None)
                self._put(db, record, kind="listing", market=market, code=current)

    def merge(self, survivor_id: str, merged_id: str) -> None:
        """``merged_id`` was the same listing all along: its codes and keys move to the survivor."""
        if survivor_id == merged_id:
            return
        with self._write:
            db = self.db()
            db.execute("update codes set id=? where id=?", (survivor_id, merged_id))
            db.execute("update source_keys set id=? where id=?", (survivor_id, merged_id))
            db.execute("update instruments set merged_into=? where id=?", (survivor_id, merged_id))
            db.execute("update instruments set merged_into=? where merged_into=?", (survivor_id, merged_id))
            db.execute("delete from names_prefix where id=?", (merged_id,))
            db.execute("delete from names_tri where id=?", (merged_id,))
            merged = json.loads(db.execute("select record from instruments where id=?", (merged_id,)).fetchone()[0])
            survivor = self.record(survivor_id)
            if merged.get("listedFrom") and (not survivor.get("listedFrom")
                                             or int(merged["listedFrom"]) < int(survivor["listedFrom"])):
                survivor["listedFrom"] = merged["listedFrom"]
                self._put(db, survivor, kind="listing", market=survivor["venueId"], code=survivor["symbol"])
        self.revision = now_ns()

    def add_conflict(self, instrument_id: str, code: str, start: int | None, end: int | None, other: str,
                     claimed_by: str, reason: str) -> None:
        with self._write:
            self.db().execute("insert into conflicts values(?,?,?,?,?,?,?)",
                              (instrument_id, code, start, end, other, claimed_by, reason[:300]))

    # ── catalogs ────────────────────────────────────────────────────────────

    def catalog_state(self, venue: str, family: str, state: str, problem: dict | None = None,
                      revision: str | None = None) -> None:
        with self._write:
            self.db().execute(
                "insert into catalogs values(?,?,?,?,?,?) on conflict(venue, family) do update set state=excluded.state, "
                "problem=excluded.problem, loaded_at=coalesce(excluded.loaded_at, loaded_at), "
                "revision=coalesce(excluded.revision, revision)",
                (venue, family, state, json.dumps(problem) if problem else None,
                 now_ns() if state == "ready" else None, revision))

    def catalogs(self) -> list[dict]:
        rows = self.db().execute("select venue, family, state, problem, loaded_at, revision from catalogs").fetchall()
        return [{"venue": v, "family": f, "state": s, "problem": json.loads(p) if p else None, "loadedAt": at,
                 "revision": r} for v, f, s, p, at, r in rows]

    def catalog_page(self, market: str, *, kinds: tuple[str, ...], asset_classes: list[str] | None,
                     market_types: list[str] | None, include_inactive: bool, after: str | None, limit: int
                     ) -> list[dict]:
        clauses, params = ["market=?", "merged_into is null"], [market]
        clauses.append(f"kind in ({','.join('?' * len(kinds))})")
        params += list(kinds)
        if not include_inactive:
            clauses.append("active=1")
        if asset_classes:
            clauses.append(f"asset_class in ({','.join('?' * len(asset_classes))})")
            params += asset_classes
        if after:
            clauses.append("id>?")
            params.append(after)
        rows = self.db().execute(f"select record from instruments where {' and '.join(clauses)} order by id limit ?",
                                 (*params, limit * 4 if market_types else limit)).fetchall()
        records = [presented(json.loads(row[0])) for row in rows]
        if market_types:
            records = [r for r in records if r["marketType"] in market_types][:limit]
        return records

    # ── search ──────────────────────────────────────────────────────────────

    def search(self, query: str, *, markets=None, venues=None, asset_classes=None, market_types=None,
               include_inactive=False, limit=50, offset=0, discovered=()) -> list[dict]:
        """Code (current and former) > contracts on that base > code prefix > name word prefix > name substring.

        Contracts of one base: spot and perpetuals, then futures, then options; USDT, USD, USDC before other
        quotes; larger venues first, each with its spot then its perpetual. Typing BTC shows BTC/USDT and
        BTC/USDT:USDT on Binance before BTC/AED.
        """
        text = query.strip()
        if not text:
            return []
        db = self.db()
        found: dict[str, tuple] = {}
        upper = text.upper()
        variants = {upper}
        crypto = upper.replace("-", "/")
        variants.add(crypto)
        try:
            typed = codes_module.parse_typed(text)
        except MarketError:
            typed = (None, upper)
        if typed[0] is not None:
            variants.add(typed[1].upper())  # 600519 → 600519.SH, 700.HK → 00700.HK
        variants |= typed_codes(text)  # what the chart shows: BTCUSDT, BTCUSDT.P, BTCUSDTZ2026
        for variant in variants:
            # Shortest codes first: a base's pairs (BTC/USDT) before its thousands of dated options.
            rows = db.execute(
                "select id, code, start, end, market from codes where code>=? collate nocase and code<? collate nocase "
                "order by length(code), code limit 200", (variant, variant + "\U0010ffff")).fetchall()
            for instrument_id, code, _start, end, _market in rows:
                exact = code.upper() == variant
                rank = 0 if exact else 1
                former = end is not None and end <= now_ns()
                key = self.survivor(instrument_id) or instrument_id
                entry = (rank, len(code), "former_code" if former else "code", code if former else None,
                         end if former else None)
                if key not in found or entry[:2] < found[key][:2]:
                    found[key] = entry
        # Domestic directories write the CSI 300 and CSI 500 names without a space before the number.
        compact = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[0-9])", "", text)
        # An exact name must still be found when hundreds of ETFs contain the same words and FTS caps its hits.
        for (instrument_id,) in db.execute("select id from instruments where name collate nocase in (?,?) "
                                           "and merged_into is null", (text, compact)):
            found.setdefault(instrument_id, (1.5, 0, "name", None, None))
        phrases = ['"' + term.replace('"', '""') + '"' for term in dict.fromkeys((text, compact))]
        try:
            prefixes = " OR ".join(phrase + "*" for phrase in phrases)
            rows = db.execute("select id from names_prefix where names_prefix match ? limit 200", (prefixes,)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for (instrument_id,) in rows:
            found.setdefault(instrument_id, (2, 0, "name", None, None))
        # A source can recognize a translated name that is absent from the local name index.
        for position, instrument_id in enumerate(discovered):
            current = self.survivor(instrument_id) or instrument_id
            found.setdefault(current, (2.5, position, "name", None, None))
        if len(text) >= 3:
            rows = db.execute("select id from names_tri where names_tri match ? limit 200", (" OR ".join(phrases),)).fetchall()
        else:
            rows = db.execute("select id from instruments where name like ? and merged_into is null limit 200",
                              (f"%{text}%",)).fetchall()
        for (instrument_id,) in rows:
            found.setdefault(instrument_id, (3, 0, "name", None, None))
        results = []
        for instrument_id, (rank, size, matched, former, until) in found.items():
            record = self.record(instrument_id)
            if record is None:
                continue
            if markets and (record["identityVersion"] != 2 or record["venueId"] not in markets):
                continue
            if venues and (record["identityVersion"] != 1 or record["venueId"] not in venues):
                continue
            if asset_classes and record["assetClass"] not in asset_classes:
                continue
            if market_types and record["marketType"] not in market_types:
                continue
            inactive = record.get("active") is False or record.get("retiredAt") is not None
            if inactive and not include_inactive:
                continue
            order: tuple = ()
            if record["identityVersion"] == 1:
                if matched == "code":
                    matched = "contract"
                product = _PRODUCTS.get(record["marketType"], len(_PRODUCTS))
                order = (max(product, 1) - 1, _QUOTES.get(record.get("quote"), len(_QUOTES)),
                         _VENUES.get(record["venueId"], len(_VENUES)), product)
                if rank == 1 and (record.get("base") or "").upper() in variants:
                    rank = 0.5
            results.append(((rank, order, size, record["symbol"]), {
                "instrument": record, "matched": matched,
                "formerCode": {"code": former, "until": str(until)} if former else None, "rank": rank}))
        results.sort(key=lambda item: item[0])
        return [item for _key, item in results[offset:offset + limit]]


_PRODUCTS = {"spot": 0, "swap": 1, "future": 2, "option": 3}
_QUOTES = {"USDT": 0, "USD": 1, "USDC": 2}
# Larger venues first, roughly by spot volume.
_VENUES = {venue: index for index, venue in enumerate((
    "binance", "okx", "bybit", "coinbase", "bitget", "gate", "kucoin", "htx", "mexc", "kraken", "cryptocom",
    "bitfinex", "hyperliquid", "deribit"))}

_REQUIRED_NULLS = {"base", "quote", "settle", "contractSize", "expiry", "strike", "optionSide", "underlyingSymbol",
                   "retiredAt"}


def merged_note(code: str, until: int | None) -> dict[str, Any]:
    return {"code": code, "until": None if until is None else str(until)}
