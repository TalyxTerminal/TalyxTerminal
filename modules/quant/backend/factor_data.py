"""Immutable research inputs read through Market; Market owns the only mutable price store."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
from talyx_sdk import ContractError

from . import research_data, research_inputs
from .materials import release, run_task
from .object_data import left_out_text, required_settings, resolve_until
from .work_records import digest

DAY = 86_400_000
INDUSTRY_CLASSES = {"equity"}  # the asset class Market keeps an industry and shares for (not funds: an ETF has neither)
FETCHING = 4


def freeze_day(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now, UTC).date().isoformat()


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _bar_range(path):
    labels = pd.read_parquet(path, columns=["label"])["label"]
    return int(labels.min()), int(labels.max()), len(labels.unique())


def ended_names(path, shown=5):
    """The note for names whose prices stop before the data's do (delisted, or the source has none after), or None."""
    bars = pd.read_parquet(path, columns=["label", "column"])
    last = bars.groupby("column")["label"].max()
    names = sorted(last[last < bars["label"].max()].index)
    if not names:
        return None
    listed = ", ".join(names[:shown]) + (f" and {len(names) - shown} more" if len(names) > shown else "")
    return f"Prices of {listed} end before the period does (delisted, or the source has none after): each is out at its last close and earns nothing after"


def _library_frame(rows, columns, item):
    table = pd.DataFrame(rows)
    table = table[table["instrument"].isin(columns)]
    return pd.DataFrame({"name": item["name"], "version": f"v{item['version']}",
                         "column": table["instrument"].map(columns), "label": table["datetime"].astype("int64"),
                         "availableAt": table["availableAt"].astype("int64"), "value": table["value"].astype(float)})


def _write_library(frames, path):
    staging = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        pd.concat(frames, ignore_index=True).to_parquet(staging, index=False)
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


class FactorData:
    def __init__(self, ctx, root, lists, library, *, clock=time.time):
        self.ctx, self.root, self.lists, self.library, self.clock = ctx, Path(root), lists, library, clock
        self.root.mkdir(parents=True, exist_ok=True)
        self.locks = {}
        self.in_use = {}
        self.renamed = None
        self.pruned_at = 0

    def selection(self, data):
        required_settings({**data, "symbols": data.get("symbols") or data.get("list")})
        chosen = {key: data[key] for key in ("timeframe", "since", "until") if key in data}
        chosen["adjustment"] = data.get("adjustment", "split_dividend")
        if data.get("index"):
            chosen["index"] = data["index"]
        else:
            symbols = self.lists.members(data["list"]) if data.get("list") else data.get("symbols", [])
            chosen["instruments"] = [item["instrument"] for item in symbols]
        return chosen

    def key(self, data, calendars="one", facts=True):
        """The freeze these settings share; one made without industry and shares (`facts=False`) is its own."""
        # format 2: index members are kept once and named by place in each interval (freezes before it are made again)
        return digest({"selection": self.selection(data), "calendars": calendars, "format": 2, **({} if facts else {"facts": False})})[:24]

    async def calendar_items(self, data):
        if data.get("until") != "latest":
            return None
        if data.get("index"):
            return [data["index"]["instrument"]]
        if data.get("list"):
            return [item["instrument"] for item in self.lists.members(data["list"])]
        return None

    async def resolve(self, data):
        return await resolve_until(self.ctx, data, instruments=await self.calendar_items(data))

    def directory(self, key):
        return self.root / key

    def ready(self, key):
        path = self.directory(key) / "ready.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def fetch_rows(self, key):
        path = self.directory(key) / "fetch.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def today(self):
        return freeze_day(self.clock())

    async def prepare(self, data, *, report=None, retry=False, refresh_after=None, calendars="one", facts=True):
        """The frozen data for these settings. When the same settings (the same range to its last closed bar) were frozen before and
        Market's revision of every series is the one frozen, that freeze is used as it is: nothing is fetched, exported or arranged.
        Otherwise a candidate is frozen and kept only if its revisions or facts differ. `facts=False` (a factor run that reads no industry or
        shares) freezes the prices alone."""
        resolved = await self.resolve(data)
        key = self.key(resolved, calendars, facts)
        async with self.locks.setdefault(key, asyncio.Lock()):
            previous = await asyncio.to_thread(self.ready, key)
            if previous is not None and await self.unchanged(previous):
                return {**previous, "how": "unchanged"}
            staging = self.root / f".preparing-{key}-{uuid4().hex}"
            try:
                candidate = await self._prepare(key, resolved, report or (lambda rows, stage, done=None, progress=None: None), staging, calendars, facts)
                if previous is not None and previous["fingerprint"] == candidate["fingerprint"]:
                    return {**previous, "how": "unchanged"}
                if previous is not None:
                    candidate["frozenAt"] = max(candidate["frozenAt"], previous["frozenAt"] + 1)
                candidate.update(directory=str(self.directory(key)), barsPath=str(self.directory(key) / "bars.parquet"),
                                 extrasPath=str(self.directory(key) / "extras.json"))
                await asyncio.to_thread(_write_json, staging / "ready.json", candidate)
                if previous is not None:
                    await self._retire(key, previous)
                elif self.directory(key).exists():
                    await asyncio.to_thread(shutil.rmtree, self.directory(key))
                await asyncio.to_thread(staging.rename, self.directory(key))
                if time.time() - self.pruned_at > 3600:
                    self.pruned_at = time.time()
                    await asyncio.to_thread(self.prune, 3, self.kept_by())
                return candidate
            finally:
                if staging.exists():
                    await asyncio.to_thread(shutil.rmtree, staging)

    kept_by = staticmethod(lambda: frozenset())

    async def unchanged(self, ready):
        """Whether Market still has every series of a freeze at the revision it was frozen at (one batch of revision reads)."""
        return await research_data.unchanged(self.ctx, ready["info"])

    async def _retire(self, key, ready):
        while self.in_use.get(key):
            await asyncio.sleep(.2)
        aside = f"{key}~{ready['frozenAt']}-{ready['fingerprint'][:8]}"
        await asyncio.to_thread(self.directory(key).rename, self.directory(aside))
        retired = {**ready, "key": aside, "directory": str(self.directory(aside)),
                   "barsPath": str(self.directory(aside) / "bars.parquet"), "extrasPath": str(self.directory(aside) / "extras.json")}
        await asyncio.to_thread(_write_json, self.directory(aside) / "ready.json", retired)
        if self.renamed is not None:
            self.renamed(key, aside, ready["frozenAt"])

    async def _prepare(self, key, resolved, report, directory, calendars="one", facts=True):
        directory.mkdir(parents=True)
        selection = self.selection(resolved)
        data = dict(resolved)
        if "instruments" in selection:
            data["symbols"] = [{"instrument": item} for item in selection["instruments"]]
        rows = []
        def progress(payload):
            for row in rows:
                row["waitingFor"], row["waitingUntil"] = payload["waitingFor"], payload["waitingUntil"]
            report(rows, "prices", payload["done"], payload)
        owner = f"qfactor-data:{key}:{uuid4().hex}"
        prepared = await research_data.prepare(self.ctx, data, owner, directory, progress=progress, calendars=calendars)
        try:
            info = prepared["research"]["info"]
            rows = prepared["summary"]["fetchRows"]
            await asyncio.to_thread(self._write_rows, directory, rows)
            instruments = [item["instrument"] for item in info["instruments"]]
            if facts:
                report(rows, "facts", 0)
                priced = [next(row for row in rows if row["instrumentId"] == item["instrumentId"]) for item in instruments]
                extras = await self._facts(instruments, priced, rows, directory, info["since"], info["until"],
                                           info["adjusted"], info["timeframe"], report)
            else:
                extras = {"industry": None, "shares": None, "describe": await research_inputs.describe(self.ctx, instruments), "kinds": [], "notes": []}
            notes = [*extras["notes"], *fact_notes(rows, extras["kinds"])]
            stopped = await asyncio.to_thread(ended_names, prepared["research"]["barsPath"])
            if stopped:
                notes.append(stopped)
            left = [row for row in rows if row["result"] not in {"fetched", "partial"}]
            if "index" in info:
                from .index_universe import gap_text, gaps
                lacking = gaps(info["index"], len(left))
                if lacking:
                    notes.append(gap_text(lacking))
            elif left:
                notes.append(left_out_text(left, len(rows)))
            extras_value = {"industry": extras["industry"], "shares": extras["shares"], "notes": notes}
            await asyncio.to_thread(_write_json, directory / "extras.json", extras_value)
            first, last, bars = await asyncio.to_thread(_bar_range, prepared["research"]["barsPath"])
            fingerprint = digest({"revisions": info["revisions"], "index": info.get("index"), "facts": extras_value,
                                  "instruments": [item["instrumentId"] for item in info["instruments"]]})
            info["fingerprint"] = fingerprint
            await asyncio.to_thread(_write_json, directory / "info.json", info)
            ready = {"key": key, "directory": str(directory), "barsPath": prepared["research"]["barsPath"], "info": info,
                     "extrasPath": str(directory / "extras.json"), "notes": notes, "describe": extras["describe"],
                     "bars": bars, "dataFrom": first, "dataTo": last, "names": len(instruments), "requested": prepared["summary"]["requested"],
                     "frozenAt": int(self.clock() * 1000), "asOf": self.today(), "how": "full",
                     "fingerprint": fingerprint, "newBars": {"names": len(instruments), "perName": 0, "without": 0}}
            await asyncio.to_thread(self._write_rows, directory, rows)
            report(rows, "ready")
            return ready
        finally:
            await release(self.ctx, prepared["references"], owner)

    @staticmethod
    def _write_rows(directory, rows):
        _write_json(directory / "fetch.json", rows)

    def industries(self, data):
        """Whether these names are of an asset class Market gives an industry for (stocks, not funds or crypto); the settings that need
        an industry (neutralizing by it, grouping inside it, Group neutral) exist only for such a pool, and take nothing out by default
        elsewhere."""
        if not data.get("symbols") and not data.get("list"):
            return True
        instruments = self.selection(data)["instruments"]
        return not instruments or any(item["assetClass"] in INDUSTRY_CLASSES for item in instruments)

    def default_neutralize(self, data):
        return "industry+size" if self.industries(data) else "none"

    async def _facts(self, instruments, priced_rows, rows, directory, since, until, adjusted, timeframe, report):
        """Industry and shares as Market knew them on each date: an industry history per name (`{ticker: [[from, until, name], ...]}`,
        times in ms, None where open) and each name's share counts by announcement."""
        described = await research_inputs.describe(self.ctx, instruments)
        fields = {item["key"]: item for item in described["fields"]}
        industry, shares, notes = {}, {}, []
        eligible = [(instrument, row) for instrument, row in zip(instruments, priced_rows, strict=True)
                    if instrument["assetClass"] in INDUSTRY_CLASSES]
        kinds = [kind for field, kind in (("industry", "classification"), ("shares", "shares")) if fields[field]["basis"] is not None] if eligible else []
        ticker_of = {instrument["instrumentId"]: row for instrument, row in eligible}
        if "classification" in kinds:
            # Market reads A shares' industry on each quarter's date (one request a date for the whole market) and keeps what it read.
            found, problems = await research_inputs.industries(self.ctx, [instrument for instrument, _ in eligible], since, until,
                                                              progress=lambda value: report(rows, "facts", 0, value))
            for identity, problem in problems.items():
                if problem["code"] not in {"unsupported", "no_data", "not_found"}:
                    raise ContractError(problem["code"], problem["message"], problem)
                _fact_note(ticker_of[identity], problem["message"])
            for identity, history in found.items():
                row = ticker_of[identity]
                industry[row["ticker"]] = [list(item) for item in history]
                row["sector"] = history[-1][2]
        if "shares" in kinds:
            # One Market fetch for every name: Market keeps what it has (fetches only facts it lacks up to this end) and
            # limits the requests to its sources itself.
            result = await run_task(self.ctx, "market.fetch", {"symbols": [instrument["instrumentId"] for instrument, _ in eligible],
                "data": [{"kind": "shares"}], "start": "1970-01-01", "end": research_inputs.timestamp(until)},
                progress=lambda value: report(rows, "facts", 0, value))
            for failure in result["failures"]:
                problem = failure["problem"]
                if problem["code"] not in {"unsupported", "no_data", "not_found"}:
                    raise ContractError(problem["code"], problem["message"], problem)
                _fact_note(ticker_of[failure["instrumentId"]], problem["message"])
        gate = asyncio.Semaphore(FETCHING)
        finished = 0
        async def one(instrument, row):
            nonlocal finished
            async with gate:
                found = await research_inputs.read_pages(self.ctx, {"data": "shares", "symbols": [instrument["instrumentId"]],
                    "end": research_inputs.timestamp(until), "asOf": research_inputs.timestamp(until), "fill": "none"})
                self._fact_problem(found, row)
                counts = [(research_inputs.milliseconds(entry["knownAt"]), float(entry["shares"]))
                          for entry in found["rows"] if entry["kind"] == "outstanding"]
                if counts:
                    shares[row["ticker"]] = sorted(set(counts))
                finished += 1
                report(rows, "facts", finished)
        tasks = [asyncio.create_task(one(instrument, row)) for instrument, row in eligible] if "shares" in kinds else []
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return {"industry": industry or None, "shares": shares or None, "describe": described, "kinds": kinds, "notes": notes}

    @staticmethod
    def _fact_problem(found, row):
        problem = research_inputs.source_problem(found["coverage"])
        if problem is None:
            return
        if problem["code"] not in {"unsupported", "no_data", "not_found"}:
            raise ContractError(problem["code"], problem["message"], problem)
        _fact_note(row, problem["message"])

    async def library_values(self, key, info, exclude=None):
        catalog = [item for item in await asyncio.to_thread(self.library.store.catalog)
                   if item["kind"] == "factor" and item["state"] != "delisted" and item["name"] != exclude]
        if not catalog:
            return None
        versions = await asyncio.to_thread(self.library.store.versions, [item["version_id"] for item in catalog])
        state = digest([[item["name"], item["version_id"], versions[item["version_id"]]["cursor"]] for item in catalog])[:16]
        path = self.directory(key) / f"library-known-{state}.parquet"
        if path.is_file():
            return str(path)
        columns = {item["instrumentId"]: item["column"] for item in info["instruments"]}
        frames = []
        for item in catalog:
            async with self.library.reading:
                found = await asyncio.to_thread(self.library.store.read_values, item["version_id"], start=info["since"], end=info["until"])
            if found:
                frame = await asyncio.to_thread(_library_frame, found, columns, item)
                if not frame.empty:
                    frames.append(frame)
        if not frames:
            return None
        await asyncio.to_thread(_write_library, frames, path)
        return str(path)

    def prune(self, keep_days=3, keep=frozenset()):
        cutoff = self.clock() - keep_days * 86400
        for folder in self.root.iterdir():
            if folder.is_dir() and not folder.name.startswith(".preparing-") and folder.name not in keep and not self.in_use.get(folder.name) and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder)


def _fact_note(row, message):
    if message not in row.setdefault("factNotes", []):
        row["factNotes"].append(message)


FACT_WORDS = {"classification": "industry", "shares": "shares outstanding"}


def fact_notes(rows, kinds, shown=5):
    """one note per reason a source gave no industry or shares, naming the names it is about, not one bare source message per name."""
    names = {}
    for row in rows:
        for message in row.get("factNotes", ()):
            names.setdefault(message, []).append(row["ticker"])
    what = " or ".join(FACT_WORDS[kind] for kind in kinds) or "facts"
    notes = []
    for message, tickers in names.items():
        listed = ", ".join(tickers[:shown]) + (f" and {len(tickers) - shown} more" if len(tickers) > shown else "")
        notes.append(f"No {what} for {listed}: {message}")
    return notes
