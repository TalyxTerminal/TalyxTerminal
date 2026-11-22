"""Wake work registered under its workspace. The queue and recurrence cursors commit together."""

import asyncio
import json
import logging
import time

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from ..store import TERMINAL, encode
from .records import MENTION, decode_rows, group_handoff, identity, recipients, stamp
from .schedules import due_run

log = logging.getLogger(__name__)
PRIORITY = {"you": 0, "event": 1, "mention": 2, "schedule": 3, "catchup": 4, "resumed": 4}


class BotWorker:
    def __init__(self, bots):
        self.bots, self.agent, self.db, self.store = bots, bots.agent, bots.db, bots.store
        self.tasks, self.signals, self.subs = {}, {}, {}
        self.open = set()
        self.listeners = []

    async def start(self):
        ctx = self.agent.ctx
        changes = await ctx.subscribe("platform.workspace.changes")
        modules = await ctx.subscribe("platform.modules.changes")
        self.open.update(await ctx.platform.open_workspaces())
        await self.catalog()
        for ws in self.open:
            self.ensure(ws)
        self.listeners = [
            asyncio.create_task(self.workspaces(changes)),
            asyncio.create_task(self.modules(modules)),
        ]

    async def catalog(self):
        defs = await self.agent.ctx.call("platform.modules.definitions", {})
        self.bots.sources = {
            f"{item['id']}.{name}": {
                "name": name,
                "description": spec["description"],
                "params": {**spec["params"], "$defs": item["contract"]["$defs"]},
            }
            for item in defs
            if item["enabled"] and item["state"] == "ready"
            for name, spec in item["contract"]["streams"].items()
            if spec["trigger"]
        }
        for ws in self.open:
            self.signal(ws)

    async def modules(self, stream):
        async for _ in stream:
            await self.catalog()

    async def workspaces(self, stream):
        async for change in stream:
            ws = change["workspaceId"]
            if change["kind"] == "opened":
                self.open.add(ws)
                self.ensure(ws)
            elif change["kind"] == "closed":
                self.open.discard(ws)
                self.bots.visible.pop(ws, None)
                if ws in self.tasks:
                    task = self.tasks[ws]
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                pending = self.db.execute(
                    "SELECT DISTINCT c.id FROM conversations c JOIN turns t ON t.conversation=c.id "
                    "WHERE c.workspace=? AND t.state IN ('queued','running','waiting_approval','waiting_business','stopping')",
                    (ws,),
                ).fetchall()
                for (conversation,) in pending:
                    await self.agent.stop(conversation, ws)
                with self.store.transaction():
                    self.db.execute(
                        "UPDATE bot_wakes SET result='stopped' WHERE workspace=? AND turn_id IS NULL AND result IS NULL",
                        (ws,),
                    )

    def signal(self, ws):
        if ws in self.signals:
            self.signals[ws].set()

    def ensure(self, ws):
        if ws not in self.open:
            return
        if ws in self.tasks and not self.tasks[ws].done():
            self.signal(ws)
            return
        if self.needed(ws):
            self.signals[ws] = asyncio.Event()
            activity = self.agent.ctx.activity(ws, t("Agent routines and bots"), origin="ai")
            self.tasks[ws] = activity.add_task(asyncio.create_task(self.run(ws)))

    def needed(self, ws):
        """A bot that wakes by itself, a chat routine, or a run (also a paused bot's answer to the user) to finish."""
        return self.db.execute(
            "SELECT 1 FROM bot_entries WHERE workspace=? AND kind='bot' AND "
            "json_extract(value,'$.paused')=0 UNION ALL SELECT 1 FROM bot_routines "
            "WHERE workspace=? AND bot IS NULL AND json_extract(value,'$.active')=1 UNION ALL "
            "SELECT 1 FROM bot_wakes WHERE workspace=? AND result IS NULL AND turn_id IS NOT NULL LIMIT 1",
            (ws, ws, ws),
        ).fetchone() is not None

    async def run(self, ws):
        clock = time.time()
        deadline = clock
        first = True
        try:
            while True:
                self.signals[ws].clear()
                now = time.time()
                routines = decode_rows(
                    self.db.execute("SELECT value FROM bot_routines WHERE workspace=?", (ws,))
                )
                entries = {b["id"]: b for b in self.bots.entries(ws) if "duty" in b}
                await self.subscriptions(ws, routines)
                self.tick(ws, now, first or now > deadline + 2, routines, entries)
                await self.finish(ws)
                await self.dispatch(ws)
                first = False
                if not self.needed(ws):
                    return
                timings = [r["nextDue"] for r in routines if r["nextDue"] is not None]
                expiry = self.db.execute(
                    "SELECT MIN(json_extract(value,'$.expiresAt')) FROM approvals WHERE state='waiting' "
                    "AND conversation IN (SELECT id FROM conversations WHERE workspace=?)",
                    (ws,),
                ).fetchone()[0]
                if expiry is not None:
                    timings.append(expiry)
                deadline = min([now + 60, *timings])
                try:
                    await asyncio.wait_for(
                        self.signals[ws].wait(), max(0.01, deadline - time.time())
                    )
                except TimeoutError:
                    continue
        finally:
            children = [
                task for (workspace, _), (_, task) in list(self.subs.items()) if workspace == ws
            ]
            for task in children:
                task.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            for key in list(self.subs):
                if key[0] == ws:
                    del self.subs[key]
            self.tasks.pop(ws, None)
            self.signals.pop(ws, None)

    def queue(self, ws, bot, line, conversation, cause, text, now, *, skipped=False, key=None):
        routine = cause.get("routineId")
        if routine and not skipped:
            row = self.db.execute(
                "SELECT id,cause,instruction FROM bot_wakes WHERE workspace=? AND conversation=? AND "
                "turn_id IS NULL AND result IS NULL AND json_extract(cause,'$.routineId')=? "
                "AND json_extract(cause,'$.kind')=? ORDER BY queued LIMIT 1",
                (ws, conversation, routine, cause["kind"]),
            ).fetchone()
            if row:
                previous = json.loads(row["cause"])
                previous["count"] = previous.get("count", 1) + 1
                if "events" in cause:
                    previous["events"].extend(cause["events"])
                self.db.execute(
                    "UPDATE bot_wakes SET cause=? WHERE id=?", (encode(previous), row["id"])
                )
                return row["id"]
        key = key or identity()
        self.db.execute(
            "INSERT INTO bot_wakes VALUES(?,?,?,?,?,?,?,?,NULL,?,NULL)",
            (
                key,
                ws,
                bot,
                line,
                conversation,
                now,
                encode(cause),
                text,
                "skipped" if skipped else None,
            ),
        )
        return key

    def tick(self, ws, now, returning, routines, entries):
        with self.store.transaction():
            for routine in routines:
                if routine["kind"] != "schedule":
                    continue
                bot = routine["botId"]
                cause, next_time = due_run(
                    routine,
                    now,
                    returning,
                    not routine["active"] or bool(bot and entries[bot]["paused"]),
                )
                if not cause:
                    continue
                skipped = cause["kind"] == "skipped"
                if skipped:
                    cause["kind"] = "schedule"
                self.queue(
                    ws,
                    bot,
                    bot or routine["conversationId"],
                    routine["conversationId"],
                    cause,
                    routine["instruction"],
                    now,
                    skipped=skipped,
                )
                routine["nextDue"] = next_time
                self.bots.write_routine(ws, routine)
            cards = decode_rows(
                self.db.execute(
                    "SELECT a.value FROM approvals a JOIN conversations c ON a.conversation=c.id "
                    "WHERE c.workspace=? AND a.state='waiting' AND json_extract(a.value,'$.expiresAt')<=?",
                    (ws, now),
                )
            )
            for card in cards:
                self.store.decide(
                    card["id"],
                    ws,
                    card["version"],
                    "deny",
                    "Approval expired. The action was not performed.",
                    expired=True,
                )
                self.agent.schedule(card["conversationId"], resume=True)

    async def subscriptions(self, ws, routines):
        wanted = {
            r["id"]: r
            for r in routines
            if r["kind"] == "event" and r["source"] in self.bots.sources
        }
        for key, (signature, task) in list(self.subs.items()):
            if key[0] != ws:
                continue
            routine = wanted.get(key[1])
            if (
                task.done()
                or not routine
                or signature
                != (
                    routine["source"],
                    encode(routine["params"]),
                    routine.get("subscriptionVersion", 0),
                )
            ):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self.subs[key]
        for key, routine in wanted.items():
            if (ws, key) not in self.subs:
                task = asyncio.create_task(self.events(ws, routine))
                self.subs[ws, key] = (
                    (
                        routine["source"],
                        encode(routine["params"]),
                        routine.get("subscriptionVersion", 0),
                    ),
                    task,
                )

    async def events(self, ws, routine):
        stream = await self.agent.ctx.subscribe(
            routine["source"], routine["params"], workspace=ws, cursor=routine["cursor"]
        )
        try:
            async for event in stream:
                with self.store.transaction():
                    current = self.bots.routine(ws, routine["id"])
                    cause = {
                        "kind": "event",
                        "event": self.bots.sources[current["source"]]["name"],
                        "routineId": current["id"],
                        "routine": current["name"],
                        "events": [event],
                    }
                    self.queue(
                        ws,
                        current["botId"],
                        current["botId"],
                        current["conversationId"],
                        cause,
                        current["instruction"],
                        time.time(),
                        skipped=not current["active"]
                        or self.bots.entry(ws, current["botId"])["paused"],
                    )
                    self.bots.write_routine(ws, {**current, "cursor": stream.cursor})
                self.signal(ws)
        finally:
            await stream.close()

    async def dispatch(self, ws):
        rows = self.db.execute(
            "SELECT * FROM bot_wakes WHERE workspace=? AND turn_id IS NULL AND result IS NULL "
            "ORDER BY queued,rowid",
            (ws,),
        ).fetchall()
        busy = {
            r[0]
            for r in self.db.execute(
                "SELECT DISTINCT w.bot FROM bot_wakes w JOIN turns t ON w.turn_id=t.id "
                "WHERE w.workspace=? AND t.state IN ('queued','running','stopping')",
                (ws,),
            )
        }
        for row in sorted(rows, key=lambda r: PRIORITY[json.loads(r["cause"])["kind"]]):
            bot = row["bot"]
            if bot and self.agent.usage.group_status(bot)["paused"]:
                with self.store.transaction():
                    self.db.execute(
                        "UPDATE bot_wakes SET result='skipped' WHERE id=?", (row["id"],)
                    )
                continue
            if bot and (bot in busy or self.bots.entry(ws, bot)["paused"]):
                continue
            if self.store.active(row["conversation"]):
                continue
            cause = json.loads(row["cause"])
            text = row["instruction"]
            if cause["kind"] == "event":
                text += "\nObserved event data:\n" + encode(cause["events"])
            elif cause["kind"] == "mention":
                text = self.bots.group_transcript(ws, row["line"], bot)
            elif cause["kind"] == "catchup":
                text += f"\nCatch-up for {stamp(cause['due'])}. Current time {stamp(time.time())}."
            try:
                turn = await self.agent.wake(
                    row["conversation"], ws, text, cause=cause, request_id=row["id"]
                )
            except ContractError as error:
                with self.store.transaction():
                    self.db.execute("UPDATE bot_wakes SET result='failed' WHERE id=?", (row["id"],))
                if bot:
                    definition = self.bots.entry(ws, bot)
                    notice = self.failure(ws, definition, error.message)
                    with self.store.transaction():
                        self.bots.put(ws, "bot", definition)
                    await self.bots.notify(ws, bot, row["line"], "needsYou", row["id"], notice, run=row["id"])
                self.bots.changed(ws)
                continue
            with self.store.transaction():
                self.db.execute(
                    "UPDATE bot_wakes SET turn_id=? WHERE id=?", (turn["id"], row["id"])
                )
            busy.add(bot)
            self.bots.changed(ws)

    def failure(self, ws, definition, detail):
        definition["failures"] += 1
        limit = self.store.defaults(ws, kind="bot")["botFailureLimit"]
        if limit and definition["failures"] >= limit:  # 0 is "Never"
            definition["paused"] = True
            return "Bot paused after consecutive failures. " + detail
        return detail

    def group_handoffs(self, ws, line, definition, answer, mentions, parent):
        bot = definition["id"]
        notice = None
        group = self.bots.entry(ws, line, "group")
        for target in recipients(group["members"], mentions, None, user=False):
            if target == bot:
                continue
            allowed, notify = group_handoff(
                group, self.store.defaults(ws, kind="bot")["botRoundLimit"]
            )
            if notify:
                notice = (
                    "needsYou",
                    "Bot-to-bot round limit reached. Send a message to continue.",
                )
            if allowed:
                subject = self.bots.entry(ws, target)
                self.queue(
                    ws,
                    target,
                    line,
                    self.bots.line(ws, subject, line),
                    {
                        "kind": "mention",
                        "bot": bot,
                        "name": definition["name"],
                        "parent": parent,
                    },
                    answer,
                    time.time(),
                    skipped=subject["paused"],
                )
        self.bots.put(ws, "group", group)

        return notice

    async def finish(self, ws):
        rows = self.db.execute(
            "SELECT w.*,t.state,t.value FROM bot_wakes w JOIN turns t ON w.turn_id=t.id "
            "WHERE w.workspace=? AND w.result IS NULL ORDER BY w.queued",
            (ws,),
        ).fetchall()
        for row in rows:
            bot, line = row["bot"], row["line"]
            turn = json.loads(row["value"])
            if turn["state"] == "waiting_approval" and bot:
                for card in decode_rows(
                    self.db.execute(
                        "SELECT value FROM approvals WHERE turn_id=? AND state='waiting'",
                        (turn["id"],),
                    )
                ):
                    if not card.get("notified"):
                        with self.store.transaction():
                            card["notified"] = True
                            self.db.execute(
                                "UPDATE approvals SET value=? WHERE id=?",
                                (encode(card), card["id"]),
                            )
                        await self.bots.notify(
                            ws,
                            bot,
                            line,
                            "needsYou",
                            card["id"],
                            "Approval requested for " + card["action"],
                            run=row["id"],
                        )
            if turn["state"] not in TERMINAL:
                continue
            assistant = self.db.execute(
                "SELECT json_extract(value,'$.text') FROM cards WHERE turn_id=? AND "
                "json_extract(value,'$.role')='assistant' ORDER BY rowid DESC LIMIT 1",
                (turn["id"],),
            ).fetchone()
            answer = (assistant[0] or "").strip() if assistant else ""
            outcome = {
                "completed": "done" if answer and answer != "NO_REPLY" else "quiet",
                "failed": "failed",
                # A spending limit stops the bot until next month or a higher limit; it is not a failing run.
                "budget_exceeded": "skipped",
                "cancelled": "stopped",
                "interrupted": "stopped",
            }[turn["state"]]
            notice = None
            with self.store.transaction():
                self.db.execute("UPDATE bot_wakes SET result=? WHERE id=?", (outcome, row["id"]))
                if not bot:
                    continue
                definition = self.bots.entry(ws, bot)
                error = turn["error"] or {}
                if turn["state"] == "budget_exceeded" and (error.get("details") or {}).get("groupId") == bot:
                    # The overall limit notifies once by itself; a bot's own limit pauses it, so this comes once a month.
                    notice = ("needsYou", error.get("message", "Monthly budget reached"))
                elif outcome == "failed":
                    detail = (turn["error"] or {}).get("message", "Run failed")
                    notice = ("needsYou", self.failure(ws, definition, detail))
                elif outcome in {"done", "quiet"}:
                    definition["failures"] = 0
                self.bots.put(ws, "bot", definition)
                if outcome == "done":

                    mentions = MENTION.findall(answer)
                    self.bots.message(ws, line, bot, answer, mentions=mentions, turn=turn["id"])
                    notice = ("results", answer)
                    if bot != line:
                        notice = (
                            self.group_handoffs(ws, line, definition, answer, mentions, row["id"])
                            or notice
                        )
                if turn["state"] == "interrupted" and json.loads(row["cause"])["kind"] != "resumed":
                    calls = self.store.calls(turn["id"])
                    detail = encode(
                        [
                            {"action": c["action"], "state": c["state"], "callId": c["callId"]}
                            for c in calls
                        ]
                    )
                    self.queue(
                        ws,
                        bot,
                        line,
                        row["conversation"],
                        {"kind": "resumed", "stopped": stamp(row["queued"])},
                        row["instruction"]
                        + "\nThe preceding run stopped mid-work. Inspect these recorded operations before continuing: "
                        + detail,
                        time.time(),
                    )
            self.bots.changed(ws)
            if notice:
                await self.bots.notify(ws, bot, line, notice[0], row["id"], notice[1], run=row["id"])
