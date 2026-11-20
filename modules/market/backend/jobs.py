"""Fetch jobs: resumable downloads into local storage.

One job is one ``fetch`` call's targets × kinds over a range. Items (one instrument
and one kind) run at most eight at a time; each fills only what is missing, so a
resumed job requests only what is left. Rate limits wait for the source's cooldown
and continue; a lost network waits for it to come back. A job belongs to the
workspace that started it. Closing the workspace interrupts its active downloads;
opening it resumes its interrupted downloads. A caller can list jobs and cancel or
resume one by ID in that workspace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from contextlib import suppress
from typing import Any

import numpy as np
import orjson

from vocabulary.identity import normalize_timeframe
from vocabulary.instruments import MarketInstrument, market_data_series, ohlcv_series

from .common import DAY_NS, MarketError, now_ns, ns_of_date, problem_of
from .data import SERIES_CAPABILITIES
from .routing import JOBS, PRIORITY, in_worker

log = logging.getLogger(__name__)

CONCURRENCY = 8
ROW_BYTES = 48          # stored bytes per bar row, measured on daily Parquet parts with their metadata
MAX_FAILURES_LISTED = 20
NETWORK_RETRY_SECONDS = 15.0
SAVE_SECONDS = 1.0


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def instant(value, *, end: bool = False) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and "-" in value:
        return ns_of_date(value) + (DAY_NS if end else 0)
    return int(value)


def _trading_days(calendar: str, start: int, end: int) -> list[tuple[int, int]]:
    """[local midnight, next local midnight) of each trading day whose midnight is in [start, end)."""
    from . import calendars
    days = calendars._schedule(calendar)[0]
    opens = calendars.local_midnight(calendar, days)
    picked = (opens >= start) & (opens < end)
    return [(int(value), int(value) + DAY_NS) for value in np.asarray(opens)[picked]]


def item_id(target: str, kind: str) -> str:
    return hashlib.sha256(f"{target}\0{kind}".encode()).hexdigest()[:16]


class Job:
    """A job's persisted record plus its running state; ``done`` is set while it is not running."""

    def __init__(self, record: dict):
        self.record = record
        self.task: asyncio.Task | None = None
        self.listeners: list = []
        self.done = asyncio.Event()
        self.done.set()
        self.saved_at = 0.0

    @property
    def id(self) -> str:
        return self.record["jobId"]


class Jobs:
    def __init__(self, market):
        self.market = market
        self.root = market.root / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        # A started job's task is registered as its workspace's background work (entry.py).
        self.on_started = lambda job: None
        self.open_workspaces: set[str] | None = None   # None: the host does not report workspaces (tests)
        for path in self.root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record["state"] in ("running", "waiting", "queued"):
                record["state"] = "interrupted"
                record["interruption"] = "host_stopped"
            self.jobs[record["jobId"]] = Job(record)

    # ── persistence ─────────────────────────────────────────────────────────

    def save_progress(self, job: Job) -> None:
        """After an item: the whole record (thousands of items, 800 KB) is written at most once a second, on the event
        loop that also carries live data (orjson: a few milliseconds; ``json`` took 50-90). A restart re-checks what is
        stored, so a second of progress is not lost work."""
        moment = time.monotonic()
        if moment - job.saved_at >= SAVE_SECONDS:
            job.saved_at = moment
            self.save(job)

    def save(self, job: Job) -> None:
        path = self.root / f"{job.id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(orjson.dumps(job.record))
        os.replace(temporary, path)

    def list(self, workspace_id: str | None) -> list[dict]:
        return [job.record for job in sorted(self.jobs.values(), key=lambda j: j.record["createdAt"], reverse=True)
                if job.record["workspaceId"] == workspace_id]

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise MarketError("not_found", f"No fetch job {job_id}")
        return job

    # ── the fetch action ────────────────────────────────────────────────────

    def key(self, value: dict) -> str:
        wanted = {name: value.get(name) for name in ("targets", "kinds", "start", "end")}
        wanted["environment"] = value.get("environment", "production")
        return hashlib.sha256(canonical(wanted).encode()).hexdigest()

    async def fetch(self, value: dict, *, workspace_id: str | None, progress=None) -> dict:
        """Start, join or resume the job for this request and wait for it."""
        key = self.key(value)
        job = next((j for j in self.jobs.values() if j.record["key"] == key and j.record["workspaceId"] == workspace_id
                    and j.record["state"] not in ("completed", "cancelled", "failed")), None)
        if job is None:
            job = await self.create(value, key, workspace_id=workspace_id)
        if progress is not None:
            job.listeners.append(progress)
        try:
            if job.task is None or job.task.done():
                self.start(job)
            await asyncio.shield(job.done.wait())
        finally:
            if progress is not None and progress in job.listeners:
                job.listeners.remove(progress)
        return self.result(job)

    async def create(self, value: dict, key: str, *, workspace_id: str | None) -> Job:
        job = await self.prepare(value, key, workspace_id=workspace_id)
        # Planning every item reads its stored pointer: a thousand files, in a worker, not on the event loop.
        await in_worker(self.check_space, job)
        self.jobs[job.id] = job
        self.save(job)
        return job

    async def prepare(self, value: dict, key: str, *, workspace_id: str | None) -> Job:
        start = instant(value.get("start"))
        end = instant(value.get("end"), end=True)
        environment = value.get("environment", "production")
        instruments, unmapped, failures = await self.expand(value["targets"], start, end, environment)
        items = []
        exact = {}
        for target in value["targets"]:
            if "kinds" in target:
                kinds = exact.setdefault(target["instrument"]["instrumentId"], [])
                kinds.extend(kind for kind in target["kinds"] if kind not in kinds)
        for label, instrument in instruments:
            for kind in exact.get(instrument["instrumentId"], value["kinds"]):
                name = kind["kind"] + (f":{normalize_timeframe(kind['timeframe'])}" if kind.get("timeframe") else "")
                items.append({"id": item_id(instrument["instrumentId"], name), "target": label,
                              "instrument": instrument, "kind": kind, "name": name, "state": "pending",
                              "rows": 0, "bytes": 0, "problem": None})
        record = {"jobId": "job_" + uuid.uuid4().hex, "key": key, "request": value, "createdAt": now_ns(),
                  "workspaceId": workspace_id, "state": "queued",
                  "start": start, "end": end, "environment": environment, "items": items,
                  "unmappedMembers": unmapped, "expansionFailures": failures, "waitingFor": None,
                  "waitingUntil": None, "interruption": None}
        return Job(record)

    async def expand(self, targets: list[dict], start, end, environment) -> tuple[list, int, list]:
        """Targets become (label, instrument record) pairs; an index brings its members in the range."""
        found: dict[str, tuple[str, dict]] = {}
        unmapped, failures = 0, []
        instruments = self.market.instruments
        for target in targets:
            try:
                if "instrument" in target:
                    # Identity reads in a worker: "update all" hands in a thousand instruments, 1.9 s on the loop.
                    record = await in_worker(self.market.instrument_record, target["instrument"])
                    found.setdefault(record["instrumentId"], (record["symbol"], record))
                elif "symbol" in target:
                    value = {"text": target["symbol"]}
                    if environment != "production":
                        value["environment"] = environment
                    record = (await instruments.resolve(value))["instrument"]
                    found.setdefault(record["instrumentId"], (target["symbol"], record))
                else:
                    members, missing = await self.market.research.members_between(
                        target["index"], instant(target.get("membersFrom")) or start,
                        instant(target.get("membersUntil")) or end, candidates=True)
                    unmapped += missing
                    for record in members:
                        found.setdefault(record["instrumentId"], (record["symbol"], record))
            except MarketError as exc:
                label = target.get("symbol") or str(target.get("index") or target.get("instrument", {}).get("symbol"))
                failures.append({"target": label[:64], "instrumentId": None, "kind": "identity",
                                 "problem": exc.problem()})
        return list(found.values()), unmapped, failures

    # ── space ───────────────────────────────────────────────────────────────

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.market.root).free

    def estimate_bytes(self, job: Job) -> int:
        total = 0
        data = self.market.data
        for item in job.record["items"]:
            plan = self.plan(job, item)
            if plan is None or plan.source_id is None:
                continue
            start, end = self.window(job, plan)
            gaps = data.missing(plan, start, end)
            total += sum(data.bars_between(plan.instrument, plan.base or "1d", a, b, plan.series.sessionScope)
                         for a, b in gaps) * ROW_BYTES
        return total

    def space(self, job: Job) -> dict:
        return {"estimatedBytes": self.estimate_bytes(job), "freeBytes": self.free_bytes()}

    def check_space(self, job: Job) -> None:
        space = self.space(job)
        estimated, free = space["estimatedBytes"], space["freeBytes"]
        if estimated > free:
            raise MarketError("insufficient_space",
                              f"This download needs about {estimated:,} bytes but only {free:,} are free",
                              details=space)

    # ── running ─────────────────────────────────────────────────────────────

    def start(self, job: Job) -> None:
        job.record.update(state="queued", interruption=None, waitingFor=None, waitingUntil=None)
        job.done.clear()
        job.task = self.market.track_task(asyncio.ensure_future(self.run(job)))
        self.on_started(job)

    def plan(self, job: Job, item: dict):
        """The stored series an item fills: bars, or a crypto capability (funding, open interest, ...); None for
        research kinds, which are documents."""
        kind = item["kind"]
        model = MarketInstrument.from_wire(item["instrument"])
        if kind["kind"] == "bars":
            series = ohlcv_series(model, kind.get("timeframe") or "1d", price_basis=kind.get("priceBasis") or "last")
        elif kind["kind"] in SERIES_CAPABILITIES:
            series = market_data_series(model, kind["kind"], timeframe=kind.get("timeframe"))
        else:
            return None
        return self.market.data.plan(item["instrument"], series, job.record["start"], environment=job.record["environment"])

    def window(self, job: Job, plan) -> tuple[int, int]:
        """The job's range for one instrument. Dates are the instrument's own trading dates: a Shanghai
        bar of 2024-06-18 starts at Beijing midnight, eight hours before that UTC day."""
        start = job.record["start"]
        if start is None:
            from .routing import period_start
            start = period_start(plan.period, plan.series.priceBasis, now_ns()) if plan.period \
                else ns_of_date("1970-01-02")
        end = job.record["end"] if job.record["end"] is not None else now_ns() + DAY_NS
        request, calendar = job.record.get("request") or {}, plan.instrument.get("calendarId")
        if calendar:
            from datetime import date, timedelta

            from .calendars import local_midnight
            for name in ("start", "end"):
                text = request.get(name)
                if isinstance(text, str) and "-" in text:
                    day = date.fromisoformat(text[:10]) + timedelta(days=1 if name == "end" else 0)
                    value = int(local_midnight(calendar, np.asarray([np.datetime64(day, "D")]))[0])
                    start, end = (value, end) if name == "start" else (start, value)
        return start, end

    async def run(self, job: Job) -> None:
        record = job.record
        record.update(state="running", interruption=None)
        self.save(job)
        self.notify(job)
        token = PRIORITY.set(JOBS)
        gate = asyncio.Semaphore(CONCURRENCY)
        try:
            pending = [item for item in record["items"] if item["state"] == "pending"]
            pending = await self.run_bulk(job, pending)

            async def one(item):
                async with gate:
                    await self.run_item(job, item)

            await asyncio.gather(*(one(item) for item in pending))
            record.update(state="completed", waitingFor=None, waitingUntil=None)
        except asyncio.CancelledError:
            if record["state"] != "cancelled":
                record["state"] = "interrupted"
            raise
        finally:
            PRIORITY.reset(token)
            self.save(job)
            self.notify(job)
            job.done.set()   # not running: completed, cancelled or interrupted

    async def run_bulk(self, job: Job, pending: list[dict]) -> list[dict]:
        """Daily bars of many instruments from a source that answers a whole trading day at once (Tushare
        by trade_date): one request per missing day instead of one per instrument. Returns the items left."""
        groups: dict[tuple[str, str], list[tuple[dict, Any]]] = {}
        plans = await in_worker(lambda: {id(item): self.plan(job, item) for item in pending
                                         if item["kind"]["kind"] == "bars"
                                         and item["instrument"]["identityVersion"] == 2})
        for item in pending:
            if id(item) not in plans:
                continue
            plan = plans[id(item)]
            if plan is None or plan.source_id is None or plan.source_id not in self.market.registry.entries:
                continue
            entry = self.market.registry.entry(plan.source_id)
            if "bulk" not in entry.descriptor["supports"] or plan.key.timeframe != "1d":
                continue
            groups.setdefault((plan.source_id, item["instrument"]["venueId"]), []).append((item, plan))
        handled: set[int] = set()
        data = self.market.data
        for (source_id, market), members in groups.items():
            start, end = self.window(job, members[0][1])
            end = min(end, now_ns())
            calendar = members[0][0]["instrument"]["calendarId"]
            days = _trading_days(calendar, start, end)
            if len(members) < 2 or len(days) > len(members):
                continue  # one request per instrument is fewer
            targets = {id(item): data.target(item["instrument"], job.record["environment"]) for item, _p in members}
            for day_start, day_end in days:
                wanted = [(item, plan) for item, plan in members if data.missing(plan, day_start, day_end)]
                if not wanted:
                    continue
                codes = [targets[id(item)].code_at(day_start) for item, _p in wanted]
                while True:
                    try:
                        history = await self.market.registry.call(source_id, "bulk", "bars", "1d",
                                                                  {"markets": [market]}, codes, day_start, day_end)
                        break
                    except MarketError as exc:
                        if await self.wait_out(job, exc):
                            continue
                        for item, _plan in wanted:
                            item.update(state="failed", problem=exc.problem())
                            handled.add(id(item))
                        wanted = []
                        break
                columns = history.columns if wanted else {}
                for (item, plan), code in zip(wanted, codes, strict=False):
                    if id(item) in handled:
                        continue
                    from . import bars
                    from .sources.base import History
                    mask = columns["code"] == code
                    rows = {name: values[mask] for name, values in columns.items() if name != "code"}
                    one = History(rows) if bars.length(rows) else History(bars.empty(), None, "source_has_none")
                    await data.commit(plan, one, day_start, day_end)
            for item, plan in members:
                if id(item) in handled:
                    continue
                pointer = data.store.pointer(plan.key)
                rows = int(pointer["rows"]) if pointer and pointer.get("rows") is not None else 0
                item.update(state="done", rows=rows, bytes=rows * ROW_BYTES)
                handled.add(id(item))
            self.save(job)
            self.notify(job)
        return [item for item in pending if id(item) not in handled]

    async def run_item(self, job: Job, item: dict) -> None:
        while True:
            try:
                rows = await self.fill_item(job, item)
            except MarketError as exc:
                if await self.wait_out(job, exc):
                    continue
                item.update(state="failed", problem=exc.problem())
                break
            except OSError as exc:
                # A file Windows kept refusing (see store._patiently): this item fails with the system's
                # words, the job goes on with the others.
                item.update(state="failed", problem=problem_of(exc))
                break
            item.update(state="done", rows=rows, bytes=rows * ROW_BYTES)
            break
        self.save_progress(job)
        self.notify(job)

    async def fill_item(self, job: Job, item: dict) -> int:
        kind = item["kind"]["kind"]
        data = self.market.data
        if kind == "bars" or kind in SERIES_CAPABILITIES:
            def planned():  # stored pointers and the calendar's local midnights: in a worker
                plan = self.plan(job, item)
                return plan, self.window(job, plan) if plan.source_id is not None else None
            plan, window = await in_worker(planned)
            if plan.source_id is None:
                raise MarketError("unsupported", f"No source gives {item['name']} for {item['instrument']['symbol']}")
            start, end = window
            fill = await data.fill(plan, start, end, limit=None,
                                   retry_unavailable=True)
            if fill.problem is not None:
                raise MarketError(fill.problem["code"], fill.problem["message"], source_id=fill.problem.get("sourceId"),
                                  status=fill.problem.get("status"),
                                  retry_at=int(fill.problem["retryAt"]) if fill.problem.get("retryAt") else None)
            if kind == "bars" and item["instrument"]["identityVersion"] == 2:
                await data.fill_actions(plan, start, end, None, job.record["environment"])
            pointer = await in_worker(data.store.pointer, plan.key)
            return int(pointer["rows"]) if pointer and pointer.get("rows") is not None else 0
        return await self.market.research.fetch(item["instrument"], item["kind"], job.record["start"],
                                                job.record["end"], job.record["environment"])

    async def wait_out(self, job: Job, exc: MarketError) -> bool:
        """Wait out a rate limit or a lost network; True when the item should be tried again."""
        record = job.record
        if exc.code == "rate_limited":
            until = exc.retry_at or now_ns() + 60 * 10**9
            entry = self.market.registry.entries.get(exc.source_id) if exc.source_id else None
            if entry is not None:
                until = max(until, entry.gate.cool_until)
            record.update(state="waiting", waitingFor=exc.source_id, waitingUntil=until)
            self.save(job)
            self.notify(job, f"Waiting for {exc.source_id} until {until}")
            await asyncio.sleep(max(0.0, (until - now_ns()) / 1e9))
            record.update(state="running", waitingFor=None, waitingUntil=None)
            self.notify(job)
            return True
        if exc.code == "network_unreachable":
            record.update(state="waiting", waitingFor="network", waitingUntil=None)
            self.save(job)
            self.notify(job, "Waiting for the network")
            while not await self.market.egress.reachability.network_up(self.market.egress, ""):
                await asyncio.sleep(NETWORK_RETRY_SECONDS)
            record.update(state="running", waitingFor=None)
            self.notify(job)
            return True
        return False

    def notify(self, job: Job, message: str | None = None) -> None:
        record = job.record
        items = record["items"]
        event = {"done": sum(1 for i in items if i["state"] != "pending"), "total": len(items),
                 "waitingFor": record["waitingFor"],
                 "waitingUntil": str(record["waitingUntil"]) if record["waitingUntil"] else None,
                 "message": message or record["state"]}
        for listener in list(job.listeners):
            with suppress(Exception):
                listener(event)

    # ── workspaces and the panel ────────────────────────────────────────────

    async def interrupt(self, job: Job, reason: str) -> None:
        if job.task is not None and not job.task.done():
            job.record["interruption"] = reason
            job.task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await job.task
        if job.record["state"] not in ("completed", "cancelled", "failed"):
            job.record.update(state="interrupted", interruption=reason)
            self.save(job)
            self.notify(job)

    async def workspace_closed(self, workspace_id: str) -> None:
        for job in list(self.jobs.values()):
            if job.record["workspaceId"] == workspace_id and job.record["state"] in ("running", "waiting", "queued"):
                await self.interrupt(job, "workspace_closed")

    def workspace_opened(self, workspace_id: str) -> None:
        """Resume unfinished jobs owned by the reopened workspace."""
        for job in self.jobs.values():
            if job.record["workspaceId"] == workspace_id and job.record["state"] == "interrupted":
                self.start(job)

    async def control(self, op: str, job_id: str, workspace_id: str | None) -> dict:
        job = self.get(job_id)
        if job.record["workspaceId"] != workspace_id:
            raise MarketError("not_found", f"No fetch job {job_id} in this workspace")
        if op == "cancel":
            await self.cancel(job_id)
        else:
            self.resume(job_id)
        return {"state": job.record["state"]}

    @staticmethod
    def summary(record: dict) -> dict:
        items = record["items"]
        return {"jobId": record["jobId"], "state": record["state"], "createdAt": str(record["createdAt"]),
                "request": _request_summary(record["request"]), "done": sum(1 for i in items if i["state"] == "done"),
                "total": len(items), "rows": sum(i["rows"] for i in items),
                "bytes": sum(i["bytes"] for i in items), "failed": sum(1 for i in items if i["state"] == "failed"),
                "waitingFor": record["waitingFor"], "waitingUntil": str(record["waitingUntil"]) if record["waitingUntil"] else None,
                "interruption": record.get("interruption")}

    def resume(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.record["state"] != "interrupted":
            raise MarketError("conflict", "Only interrupted downloads can be resumed")
        self.start(job)

    async def cancel(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.record["state"] in ("completed", "failed"):
            raise MarketError("conflict", f"Download already {job.record['state']}")
        if job.task is not None and not job.task.done():
            job.record["state"] = "cancelled"
            job.task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await job.task
        job.record["state"] = "cancelled"
        job.done.set()
        self.save(job)
        self.notify(job)

    def result(self, job: Job) -> dict:
        record = job.record
        items = record["items"]
        failed = [i for i in items if i["state"] == "failed"]
        failures = [*record["expansionFailures"],
                    *({"target": i["target"][:64], "instrumentId": i["instrument"]["instrumentId"], "kind": i["name"],
                       "problem": i["problem"]} for i in failed)]
        counts: dict[str, int] = {}
        for failure in failures:
            counts[failure["problem"]["code"]] = counts.get(failure["problem"]["code"], 0) + 1
        instruments = {i["instrument"]["instrumentId"] for i in items}
        completed = {i["instrument"]["instrumentId"] for i in items} - \
            {i["instrument"]["instrumentId"] for i in items if i["state"] != "done"}
        return {"jobId": record["jobId"], "instruments": len(instruments), "completed": len(completed),
                "rows": sum(i["rows"] for i in items), "bytes": sum(i["bytes"] for i in items),
                "failures": failures[:MAX_FAILURES_LISTED], "failureCount": len(failures), "failureCounts": counts,
                "unmappedMembers": record["unmappedMembers"],
                "state": record["state"]}

    async def close(self) -> None:
        for job in self.jobs.values():
            if job.task is not None and not job.task.done():
                job.task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await job.task


def _request_summary(request: dict) -> dict:
    targets = request.get("targets", [])
    return {**{key: value for key, value in request.items() if key != "targets"}, "targets": targets[:1],
            "targetCount": len(targets)}
