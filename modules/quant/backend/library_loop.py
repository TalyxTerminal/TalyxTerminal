"""Keeps published library versions current: at each bar boundary of a version's calendar its frozen code makes the new period's values.

Each round takes the versions that are due, in the order of what they read (factors, then the models and allocations that read them, then
what reads those); each update asks the run queue for a place (how many run at once is the machine's memory and cores, not a number here); a version waits for its inputs in the same round. A period's result is one of: on time; late,
when TALYX was not open when it came due or the source gave the bar late (waiting in the queue is not late); missing, when the source has
no bar (checked again after 1, 2, 5 and 10 minutes, then every 10); upstream missing, when an input has no value for that bar; failed,
when the code failed (tried again after 10 minutes, or now on request). Five missing periods in a row stop the version (expired).

An on-time value is dated at its bar's edge (the close, or the open for weights), as the publication's history is; a late one at the
moment it was made, so nothing that read the library then could have used it earlier. Every period's row, the new values, the figures
after publication and the item's summary go in one transaction.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import time

import pandas as pd
from talyx_sdk import ContractError

from . import library_after, library_summary, research_calendar
from .library_produce import (
    FactorProducer,
    RunProducer,
    UpdateFailure,
    day_text,
    producer_for,
)
from .library_summary import REPUBLISH
from .run_queue import RunQueue

POLL = 30.0  # seconds between looks at what is due
DELAY = 5 * 60_000  # a bar's data is asked for this long after the bar closes
GRACE = 5 * 60_000  # a period first seen due later than this was due while TALYX was not open
BACKOFF = (60_000, 120_000, 300_000, 600_000)  # waits for a bar the source has not given yet; then every ten minutes
MISS_AFTER = sum(BACKOFF[:3]) + 60_000  # a bar still missing this long after it was due is recorded as missing (it can still come late)
EXPIRES_AFTER = 5  # missing periods in a row stop a version
RETRY = 10 * 60_000  # wait after a failed update
DAY = 86_400_000
NOTICE = {"fail": "libraryFailures", "miss": "libraryMissing", "up": "libraryMissing", "weak": "libraryWeakening", "drift": "libraryWeakening"}
PROBLEMS = ("fail", "miss", "up")


def now_ms():
    return int(time.time() * 1000)


def period_ms(timeframe):
    return int(timeframe[:-1] or 1) * {"m": 60_000, "h": 3_600_000, "d": DAY, "w": 7 * DAY}[timeframe[-1]]


def dependency_order(versions):
    """Versions ordered so that each comes after the versions it reads that are in the same list."""
    by_id = {version["id"]: version for version in versions}
    placed, order = set(), []

    def place(version, trail):
        if version["id"] in placed or version["id"] in trail:
            return
        for entry in version["meta"].get("inputs") or []:
            if entry["versionId"] in by_id:
                place(by_id[entry["versionId"]], trail | {version["id"]})
        placed.add(version["id"])
        order.append(version)

    for version in sorted(versions, key=lambda item: (len(item["meta"].get("inputs") or []), item["created_at"])):
        place(version, frozenset())
    return order


class LibraryLoop:
    def __init__(self, ctx, records, library, notifications, *, factors=None, clock=None, calendar=None, queue=None, producer=None):
        self.ctx, self.records, self.library, self.notifications = ctx, records, library, notifications
        self.store = library.store
        self.factors = factors
        self.clock = clock or now_ms
        self.home = None  # the loop's own context, kept when it starts (run)
        self.calendar = calendar or self.market_calendar
        # The places updates ask for are the factor engine's queue (one budget for runs, training and production), unless a queue is given.
        self.queue = queue if queue is not None else factors.queue if factors is not None else RunQueue()
        self.pipelines = None  # set by whoever publishes pipeline models: the producer of their versions
        self.allocations = None  # set by the allocation service: the producer of the allocations it published
        if producer is None:
            factor, run = FactorProducer(ctx, factors, library), RunProducer(ctx, library)
            producer = lambda version: producer_for(version, factor, run, self.pipelines, self.allocations)  # noqa: E731
        self.producer = producer
        self.running = {}  # version id -> {task, name, version, queuedAt, startedAt}
        self.attempting = {}  # version id -> the labels being made
        self.seen = {}  # (version id, label) -> when this process first saw the period due
        self.next_at = {}  # version id -> when its next period comes due
        self.cancelled = set()
        self.last_round = None
        self.wake = asyncio.Event()
        self.task = None
        library.loop = self

    # --- lifetime -------------------------------------------------------------------------------------------------------
    def start(self):
        """A module-level task, not tied to a workspace: it keeps working whichever workspaces are open."""
        self.store.recover_updates()
        self.notifications.resume()
        self.task = self.ctx.start(self.run())

    async def close(self):
        tasks = [task for task in (self.task, *(entry["task"] for entry in self.running.values())) if task is not None]
        for task in tasks:
            task.cancel("module_shutdown")
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        # The loop's own context (Quant's, with no caller and no deadline): an update started by someone's Retry runs in it too, not in their request.
        self.home = contextvars.copy_context()
        while True:
            await self.cycle()
            self.wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), POLL)

    async def market_calendar(self, version, start, end):
        info = version["meta"]["info"]
        zone, found = await research_calendar.boundaries(self.ctx, info["calendarId"], info["timeframe"], info["sessionScope"], start, end)
        return [(research_calendar.label_of(opened, info["timeframe"], zone), opened, closed) for opened, closed in found]

    # --- rounds ---------------------------------------------------------------------------------------------------------
    def due_now(self, now):
        chosen = []
        for version in self.store.continuing():
            if version["id"] in self.running or version["retry_after"] is not None and version["retry_after"] > now:
                continue
            if library_summary.republish(version):   # published before a change it cannot be produced under: a retry fails the same way
                continue
            pending = version["pending"]
            if pending is not None and pending.get("nextAt", 0) > now:
                continue
            if pending is None and version["state"] != "failed" and self.next_at.get(version["id"], 0) > now:
                continue
            chosen.append(version)
        return dependency_order(chosen)

    async def cycle(self):
        """One round: every version that is due, inputs first; returns when all of them finished."""
        now = self.clock()
        order = self.due_now(now)
        tasks = {}
        for version in order:
            inputs = [tasks[entry["versionId"]] for entry in version["meta"].get("inputs") or [] if entry["versionId"] in tasks]
            tasks[version["id"]] = self.spawn(version, inputs, now)
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            self.last_round = {"at": now, "took": round((self.clock() - now) / 1000, 1), "versions": len(order),
                               "order": [f"{(version['meta'].get('origin') or {}).get('objectName') or version['id']}" for version in order]}
        with contextlib.suppress(ContractError):
            await self.notifications.resend()

    def spawn(self, version, inputs, now):
        task = asyncio.create_task(self.guarded(version["id"], inputs), context=self.home.copy() if self.home is not None else None)
        self.running[version["id"]] = {"task": task, "queuedAt": now, "startedAt": None, "versionId": version["id"], "version": version["version"],
                                       "itemId": version["item_id"]}
        task.add_done_callback(lambda _done, identity=version["id"]: self.running.pop(identity, None) if self.running.get(identity, {}).get("task") is _done else None)
        return task

    @staticmethod
    def memory_of(version):
        """What an update is expected to hold: 1.2 times the peak the last one reported, else the publication's, else what the engine holds for one data set."""
        from .factor_engine import MEMORY
        meta = version["meta"]
        peak = meta.get("peakMemory") or (meta.get("published") or {}).get("peakMemory")
        return int(peak * 1.2) if peak else MEMORY

    def retry_now(self, version_id):
        """Try a failed, missing or waiting version now, ahead of the round."""
        version = self.store.version(version_id)
        if version["state"] not in ("updating", "failed") or not version["auto"] or version["archived_at"] is not None:
            raise ContractError("invalid_input", "Only a producing version with auto-update on can be retried.")
        if library_summary.republish(version):   # a retry fails the same way: the failure says to publish again
            raise ContractError("conflict", version["failure"]["message"])
        self.store.mark(version_id, retry_after=None, pending=None if version["pending"] is None else {**version["pending"], "nextAt": self.clock()})
        self.next_at.pop(version_id, None)
        if version_id in self.running:
            return {"versionId": version_id, "started": False, "running": True}
        self.spawn(self.store.version(version_id), [], self.clock())
        return {"versionId": version_id, "started": True, "running": True}

    async def cancel(self, version_id):
        """Stop the update running now; the version keeps its values and tries again at the next check."""
        entry = self.running.get(version_id)
        if entry is None:
            raise ContractError("conflict", "No update of this version is running.")
        self.cancelled.add(version_id)
        entry["task"].cancel()
        await asyncio.gather(entry["task"], return_exceptions=True)
        return {"versionId": version_id, "cancelled": True}

    async def guarded(self, version_id, inputs):
        """One update of one version at a time; any failure leaves the version at Failed instead of ending the loop."""
        if inputs:
            await asyncio.gather(*inputs, return_exceptions=True)
        found = self.store.version(version_id)
        async with self.queue.slot(owner="library:" + version_id, label=f"{(found['meta'].get('origin') or {}).get('objectName') or 'Library'} update", interactive=False,
                                   memory=self.memory_of(found)):
            if version_id in self.running:
                self.running[version_id]["startedAt"] = self.clock()
            item_id = found["item_id"]
            before = self.state_of(item_id)
            try:
                await self.advance(version_id)
            except asyncio.CancelledError:
                if version_id in self.cancelled:
                    self.cancelled.discard(version_id)
                    self.store.log_update(version_id, "cancelled", "Cancelled by you before it finished; nothing was added. It is tried again at the next check.")
                    version = self.store.version(version_id)
                    self.store.mark(version_id, pending={**(version["pending"] or {"label": None, "tries": 0, "firstSeen": self.clock()}), "nextAt": self.clock() + RETRY})
                    self.library.publish_change(item_id, version_id)
                    return
                self.store.log_update(version_id, "interrupted", "The module or Host stopped before the update completed.")
                raise
            except UpdateFailure as failure:
                await self.fail(version_id, failure.message, failure.details)
            except ContractError as error:
                if error.code in {"interrupted", "cancelled", "expired", "provider_unloaded"}:
                    self.store.log_update(version_id, "interrupted" if error.code == "provider_unloaded" else error.code, error.message)
                else:
                    await self.fail(version_id, error.message, {"code": error.code, **({"details": error.details} if error.details else {})})
            except Exception as error:  # a background task's last boundary: whatever broke is shown on the item, never lost with the task
                await self.fail(version_id, f"{type(error).__name__}: {error}", {"code": "internal"})
            finally:
                self.attempting.pop(version_id, None)
            self.library.publish_change(item_id, version_id)
            await self.report(item_id, before)

    # --- one update -------------------------------------------------------------------------------------------------------
    async def advance(self, version_id):
        version = self.store.version(version_id)
        meta = version["meta"]
        timeframe = meta["info"]["timeframe"]
        now = self.clock()
        cursor = version["cursor"]
        start = (cursor if cursor is not None else now) - 10 * period_ms(timeframe) - 3 * DAY
        bars = sorted(await self.calendar(version, start, now + 8 * DAY))
        closed = [bar for bar in bars if bar[2] + DELAY <= now]
        upcoming = next((bar for bar in bars if bar[2] + DELAY > now), None)
        self.next_at[version_id] = upcoming[2] + DELAY if upcoming else now + int(POLL * 1000)
        due = [bar for bar in closed if cursor is None or bar[0] > cursor]
        if not due:
            if version["pending"] is not None:
                self.store.mark(version_id, pending=None)
            return
        # A bar counts as seen when its round queued the update: waiting in the queue behind other updates never makes a value late.
        queued = min(now, (self.running.get(version_id) or {}).get("queuedAt") or now)
        for bar in due:
            self.seen.setdefault((version_id, bar[0]), queued)
        periods, blocked = [], self.blocked(meta, due)
        for label, why in blocked.items():
            bar = next(item for item in due if item[0] == label)
            periods.append({"label": label, "due_at": bar[2] + DELAY, "result": "up", "names": 0, "why": why})
        makeable = [bar for bar in due if bar[0] not in blocked]
        if not makeable:
            # Its inputs may still come: look again soon, without making anything.
            self.store.put_periods(version_id, periods, pending={"label": due[-1][0], "firstSeen": self.seen[(version_id, due[-1][0])], "tries": 0,
                                                                  "nextAt": now + BACKOFF[2], "dueAt": due[-1][2] + DELAY, "upstream": True})
            return
        newest = makeable[-1][0]
        pending = version["pending"] if version["pending"] and version["pending"].get("label") == newest else None
        attempt = (pending or {}).get("tries", 0)
        self.attempting[version_id] = [(bar[0], bar[2] + DELAY) for bar in makeable]
        self.store.begin_update(version_id)
        started = self.clock()
        made = await self.producer(version).make(version, through=newest, after=cursor, now=now, attempt=attempt, due=makeable[-1][2])
        if made.peak:
            self.store.merge_meta(version_id, peakMemory=int(made.peak))
        self.name_new_instruments(version, made)
        now = self.clock()
        took = round((now - started) / 1000, 2)
        rows = made.rows[(made.rows["datetime"] <= newest) & ((made.rows["datetime"] > cursor) if cursor is not None else True)]
        have = set(rows["datetime"].astype("int64"))
        produced, missing, waiting = [], [], None
        latest = max(made.present, default=None)
        for bar in makeable:
            label = bar[0]
            if label in have:
                produced.append(bar)
            elif label in made.present:
                missing.append((bar, f"The code made no value for {day_text(label)}"))
            elif latest is not None and latest > label:
                missing.append((bar, f"The source has no bar for {day_text(label)}"))
            else:
                waiting = bar
        fields = {"pending": None, "state": "updating", "failure": None, "retry_after": None}
        if waiting is not None:
            label, due_at = waiting[0], waiting[2] + DELAY
            tries = attempt + 1
            fields["pending"] = {"label": label, "firstSeen": min(self.seen[(version_id, label)], (pending or {}).get("firstSeen", now)), "tries": tries,
                                 "nextAt": now + BACKOFF[min(tries - 1, len(BACKOFF) - 1)], "dueAt": due_at}
            if now - due_at >= MISS_AFTER:
                periods.append({"label": label, "due_at": due_at, "result": "miss", "names": 0, "tries": tries,
                                "why": f"The source has not given the bar for {day_text(label)} yet; checking every {BACKOFF[-1] // 60_000} minutes while TALYX is open"})
        frame, constant = await asyncio.to_thread(self.production_frame, rows, produced, version, made, now, pending, attempt, took, periods, dict(self.seen))
        if constant is not None:
            produced = [bar for bar in produced if bar[0] < constant[0]]
            missing = [(bar, why) for bar, why in missing if bar[0] < constant[0]]
        for bar, why in missing:
            periods.append({"label": bar[0], "due_at": bar[2] + DELAY, "result": "miss", "names": 0, "tries": attempt + 1, "why": why})
        moved = max([bar[0] for bar in produced] + [bar[0] for bar, _ in missing], default=None)
        streak = self.streak(version_id, periods)
        fields["misses"] = streak
        if streak >= EXPIRES_AFTER:
            fields["state"] = "expired"
            newest_miss = max((period for period in periods if period["result"] == "miss"), key=lambda period: period["label"], default=None)
            if newest_miss is not None:
                newest_miss["why"] = f"No new bars from the source for {streak} periods; production stopped"
        if len(frame):
            after, points = await asyncio.to_thread(self.after_figures, version, frame, made.prices, made.risk_free)
            await asyncio.to_thread(self.store.append_values, version_id, frame, cursor=moved, expected_cursor=cursor, periods=periods, after=after, points=points,
                                    fields=fields)
        elif moved is not None:
            self.store.put_periods(version_id, periods, cursor=moved, **fields)
        else:
            self.store.put_periods(version_id, periods, **fields)
        if fields["state"] == "expired":
            self.store.log_update(version_id, "expired", f"No new bars from the source for {streak} periods; production stopped.")
        self.store.finish_update(version_id)
        if constant is not None:
            self.attempting.pop(version_id, None)
            await self.fail(version_id, "Every name has the same value", {"code": "same_value"}, labels=[constant])

    @staticmethod
    def production_frame(rows, produced, version, made, now, pending, attempt, took, periods, seen):
        meta, version_id = version["meta"], version["id"]
        metadata = []
        counts = rows.groupby("datetime", sort=False).agg(names=("instrument", "nunique"), distinct=("value", "nunique")).to_dict("index")
        edge_kind = meta.get("edge", "close")
        constant = None
        for label, opened, closing in produced:
            names, distinct = counts[label]["names"], counts[label]["distinct"]
            if names > 1 and distinct == 1 and meta["publication"]["kind"] != "allocation":
                # Every name has the same value: no ranking can come of it. This period fails and is tried again; what came after it waits, so nothing is made twice.
                constant = (label, closing + DELAY)
                break
            due_at = closing + DELAY
            first = min(seen.get((version_id, label), now), (pending or {}).get("firstSeen", now)) if pending else seen.get((version_id, label), now)
            # Due before the version existed (an allocation published from a run whose data end years back is filled up to now at once): made at
            # publication, known from then on, and not late (sixty periods read "TALYX was not open" and a real late one could not be told apart).
            before = due_at < version["created_at"]
            host_closed = not before and first - due_at > GRACE
            mine = pending is not None and pending.get("label") == label
            waited = mine and pending.get("tries", 0) > 0 and not pending.get("upstream")
            upstream = mine and pending.get("upstream") and now - due_at > GRACE
            late = host_closed or waited or upstream
            edge = closing if edge_kind == "close" else opened
            stopped = any(start <= due_at <= end for start, end in meta.get("stops", []))
            why = ("Stopped when it was due; made when it was produced again" if stopped else "TALYX was not open when it was due" if host_closed
                   else "The source gave the bar late" if waited else "An input gave its value late") if late \
                else "Made at publication; it was due before the version existed" if before else None
            if label in made.solver:   # an allocation period not solved as set says how it ended
                why = "; ".join(part for part in (why, made.solver[label]) if part)
            periods.append({"label": label, "due_at": due_at, "result": "late" if late else "ok", "ready_at": now, "names": names, "distinct_values": distinct,
                            "tries": attempt + 1, "took": took, "why": why})
            metadata.append((label, max(edge, now) if late or before else edge, "catch_up" if late or before else "scheduled"))
        stamps = pd.DataFrame(metadata, columns=["datetime", "availableAt", "mode"])
        frame = rows.merge(stamps, on="datetime", how="inner").assign(recordedAt=now)
        if made.settled is not None and len(made.settled) and len(frame):
            frame = pd.concat([frame, made.settled.assign(availableAt=now, recordedAt=now, mode="scheduled")], ignore_index=True)
        return frame, constant

    def blocked(self, meta, due):
        """For each due bar an input has no value for: why. A stopped input says so, and that a producing version can take its place."""
        inputs = meta.get("inputs") or []
        if not inputs:
            return {}
        found = self.store.versions([entry["versionId"] for entry in inputs])
        # An input counts only with its own value for that very bar: an older value is never carried forward.
        valued = {entry["versionId"]: {label for label, _ in self.store.days(entry["versionId"])} for entry in inputs}
        out = {}
        for label, _opened, _closing in due:
            for entry in inputs:
                other = found.get(entry["versionId"])
                if other is None or other["archived_at"] is not None:
                    out[label] = f"{entry['name']} v{entry['version']} was archived; publish this again on a producing version"
                elif label not in valued[entry["versionId"]]:
                    stopped = other["state"] in ("delisted", "expired")
                    out[label] = (f"{entry['name']} v{entry['version']} has no {day_text(label)} value"
                                  + (f"; it stopped {'on ' + day_text(other['stopped_at']) if other['stopped_at'] else 'updating'}" if stopped else ""))
                else:
                    continue
                break
        return out

    def streak(self, version_id, periods):
        """Missing periods in a row, counting the ones just recorded."""
        merged = {period["label"]: period for period in self.store.periods(version_id, limit=EXPIRES_AFTER + 5)}
        for period in periods:
            if merged.get(period["label"], {}).get("result") not in ("ok", "late"):
                merged[period["label"]] = period
        return library_summary.run_of([merged[label] for label in sorted(merged)], "miss")

    def name_new_instruments(self, version, made):
        """A version's universe is a rule (index members each day), so an update's data can hold a member the published run's data lacked: it joins the
        version's instruments, so its values are named and counted like the rest."""
        info = version["meta"].get("info") or {}
        known = {item["instrumentId"] for item in info.get("instruments", [])}
        valued = set(made.rows["instrument"])
        new = [item for item in made.instruments if item["instrumentId"] not in known and item["instrumentId"] in valued]
        if new:
            instruments = info.get("instruments", []) + new
            self.store.merge_meta(version["id"], info={**info, "instruments": instruments}, instrumentCount=len(instruments))

    def after_figures(self, version, new, prices, risk_free=0.0):
        """The figures after publication over everything this version made, settled periods only (see library_after)."""
        meta = version["meta"]
        field = meta["fields"][0]
        columns = ["datetime", "instrument", "value", "mode"]
        old = self.store.frame(version["id"], field=field, columns=columns)
        values = pd.concat([old, new.loc[new["field"] == field, columns]], ignore_index=True)
        scheduled = values.loc[values["mode"] == "scheduled", "datetime"].unique()
        through = version["created_at"]
        annualization = meta["info"]["annualization"]
        if (meta.get("publication") or {}).get("kind") == "allocation":
            have = self.store.frame(version["id"], field="riskShare", columns=["datetime", "instrument", "value"])
            shares_now = pd.concat([have, new.loc[new["field"] == "riskShare", ["datetime", "instrument", "value"]]], ignore_index=True)
            shares_now = shares_now[shares_now["datetime"].isin(scheduled)]
            latest = shares_now[shares_now["datetime"] == shares_now["datetime"].max()]
            allocation = meta.get("allocation") or {}
            settings = {**(allocation.get("settings") or {}), "pool": [{**row, "ticker": row["instrumentId"]} for row in allocation.get("pool", [])]}
            return library_after.allocation(values, prices, settings=settings, scheduled=scheduled, published_through=through, published=meta["published"], annualization=annualization,
                                            shares=dict(zip(latest["instrument"], latest["value"], strict=True)), every=meta["rebalance"]["every"],
                                            risk_free=risk_free, benchmark=library_after.benchmark_of(meta))
        horizon = meta.get("horizon") or {}
        realised = None
        if "label" in (meta.get("fields") or []) and field != "label":
            # A model is scored against the label it was trained on, which the pipeline writes beside each prediction as it settles.
            have = self.store.frame(version["id"], field="label", columns=["datetime", "instrument", "value"])
            realised = pd.concat([have, new.loc[new["field"] == "label", ["datetime", "instrument", "value"]]], ignore_index=True)
        return library_after.measure(values, prices, scheduled=scheduled, settings=meta.get("settings"), sectors=meta.get("sectors"), holding=int(horizon.get("holding", 1)), delay=int(horizon.get("delay", 1)),
                                     annualization=annualization, realised=realised, ahead=horizon.get("ahead"))

    async def fail(self, version_id, message, details, labels=None):
        version = self.store.version(version_id)
        now = self.clock()
        labels = self.attempting.pop(version_id, []) if labels is None else labels
        if version["state"] == "delisted" or version["archived_at"] is not None:
            # Stopped while this update ran: the failure goes in the log, nothing more (no failed periods, no retry, no notice of a failure).
            self.store.log_update(version_id, "failed", message, failure={"label": labels[-1][0] if labels else None, "location": details.get("location"),
                                                                          "traceback": details.get("traceback"), "output": details.get("output")})
            return
        tries = ((version["failure"] or {}).get("tries") or 0) + 1
        location = details.get("location")
        where = f" ({location['file']} line {location['line']})" if location and location.get("file") else ""
        # A version published before a change it cannot be produced under fails once and is not tried again on its own: only publishing again mends it.
        again = None if details.get("code") == REPUBLISH else now + RETRY
        if again is None:
            self.next_at.pop(version_id, None)
        failure = {"message": message, "at": now, "tries": tries, "retryAt": again, "location": location, "code": details.get("code")}
        periods = [{"label": label, "due_at": due_at, "result": "fail", "names": 0, "tries": tries, "why": message + where} for label, due_at in labels]
        self.store.put_periods(version_id, periods, state="failed", failure=failure, retry_after=again)
        self.store.log_update(version_id, "failed", message, failure={"label": labels[-1][0] if labels else None, "location": location,
                                                                      "traceback": details.get("traceback"), "output": details.get("output")})
        if version["state"] != "failed":
            summary = self.store.summary_of(version["item_id"])
            occupants = [row["workspace_id"] for row in self.store.users(version_id)]
            for workspace in dict.fromkeys(item for item in (version["workspace_id"], *occupants) if item):
                self.records.put("qlibevent", {"id": f"library_update_failed:{version_id}:{now}:{workspace}", "type": "library_update_failed", "workspaceId": workspace,
                                               "itemId": version["item_id"], "versionId": version_id, "name": summary["name"], "version": version["version"], "message": message})

    def state_of(self, item_id):
        with contextlib.suppress(ContractError):
            return self.store.summary_of(item_id)
        return None

    async def report(self, item_id, before):
        """Tell the user when an item starts failing, missing, weakening or drifting, and when it recovers: once per change."""
        after = self.state_of(item_id)
        if after is None or before is None or after["state"] == before["state"]:
            return
        name = f"{after['name']} v{after['shownVersion']}"
        last = (after.get("last20") or [{}])[-1]
        if after["state"] in NOTICE:
            text = {"fail": f"{name} could not be updated: {last.get('why') or 'the code failed'}.", "miss": f"{name} has no value for the newest period: {last.get('why') or ''}",
                    "up": f"{name} is missing an input: {last.get('why') or ''}", "weak": f"{name} is weaker since publication: {self.weak_words(after)}.",
                    "drift": f"{name} drifts from its published risk."}[after["state"]]
            await self.notifications.deliver(NOTICE[after["state"]], f"library:{after['state']}:{after['shownVersionId']}:{last.get('period')}",
                                             f"Quant · {library_summary.LABEL[after['state']]}", text)
        elif before["state"] in PROBLEMS and after["state"] in ("ok", "late"):
            await self.notifications.deliver("libraryRecovered", f"library:recovered:{after['shownVersionId']}:{last.get('period')}", "Quant · Library recovered",
                                             f"{name} is producing again.")

    def weak_words(self, summary):
        version = self.store.version(summary["shownVersionId"])
        published, after = (version["meta"].get("published") or {}).get("value") or 0, (version["after"] or {}).get("value") or 0
        return "it has the opposite sign of what was published" if library_summary.weak_how(published, after) == "opposite sign" else "it is under a quarter of what was published"

    # --- what the list and the home page show -----------------------------------------------------------------------------------
    def next_due(self, version_id):
        version = self.store.version(version_id)
        if version["retry_after"] is not None:
            return {"at": version["retry_after"], "why": "retry"}
        if version["pending"] is not None:
            return {"at": version["pending"]["nextAt"], "why": "waiting", "label": version["pending"]["label"], "tries": version["pending"]["tries"]}
        at = self.next_at.get(version_id)
        return {"at": at, "why": "next period"} if at else None

    def view(self):
        """The production queue: what runs now, what waits its turn, what retries when, and the next round."""
        # Read from a worker thread too (the list): copies, so the loop changing them meanwhile does not matter.
        entries, next_at = list(self.running.values()), dict(self.next_at)
        continuing = self.store.continuing()
        names = {item["itemId"]: item["name"] for item in self.store.summaries()}
        label = lambda version_id, item_id, number: {"versionId": version_id, "itemId": item_id, "name": names.get(item_id), "version": number}  # noqa: E731
        running = [{**label(entry["versionId"], entry["itemId"], entry["version"]), "startedAt": entry["startedAt"]} for entry in entries if entry["startedAt"]]
        queued = [{**label(entry["versionId"], entry["itemId"], entry["version"]), "queuedAt": entry["queuedAt"]} for entry in entries if not entry["startedAt"]]
        retries = [{**label(version["id"], version["item_id"], version["version"]), "at": version["retry_after"], "tries": (version["failure"] or {}).get("tries")}
                   for version in continuing if version["retry_after"] is not None]
        waiting = [{**label(version["id"], version["item_id"], version["version"]), "at": version["pending"]["nextAt"], "tries": version["pending"]["tries"]}
                   for version in continuing if version["pending"] is not None]
        upcoming = [next_at[version["id"]] for version in continuing if version["id"] in next_at]
        nearest = min(upcoming, default=None)
        return {"lastRound": self.last_round, "concurrency": len(getattr(self.queue, "running", {}) or {}), "running": running, "queued": queued, "retries": retries, "waiting": waiting,
                "next": {"at": nearest, "versions": sum(1 for at in upcoming if nearest is not None and at - nearest < 60_000)} if nearest is not None else None,
                "order": [f"{names.get(version['item_id'])} v{version['version']}" for version in dependency_order(continuing)], "producing": len(continuing)}
