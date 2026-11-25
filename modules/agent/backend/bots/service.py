"""Bots share the conversation engine. Each member reads its own graph on each group line."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from langchain_core.messages import HumanMessage
from talyx_sdk import ContractError
from talyx_sdk.i18n import mark, t

from ..store import TERMINAL, encode
from .records import MENTION, SCHEMA, avatar, decode_rows, identity, recipients, stamp, text_value
from .schedules import due_run, next_due
from .worker import BotWorker

WELCOME = mark("What's the main thing you want me to do? Tell me when to run, what to look at, and who to tell.")


class Bots:
    def __init__(self, agent):
        self.agent, self.store, self.db = agent, agent.store, agent.store.db
        self.db.executescript(SCHEMA)
        self.visible = {}
        self.sources = {}
        self.listeners = {}

        self.worker = BotWorker(self)

    async def start(self):
        await self.worker.start()

    def changed(self, ws):
        for signal in self.listeners.get(ws, ()):
            signal.set()

    async def changes(self, ws):
        signal = asyncio.Event()
        listeners = self.listeners.setdefault(ws, set())
        listeners.add(signal)
        try:
            while True:
                yield {}
                await signal.wait()
                signal.clear()
        finally:
            listeners.discard(signal)

    def entries(self, ws):
        return decode_rows(
            self.db.execute(
                "SELECT value FROM bot_entries WHERE workspace=? ORDER BY rowid DESC", (ws,)
            )
        )

    def entry(self, ws, key, kind=None):
        row = self.db.execute(
            "SELECT value,kind FROM bot_entries WHERE id=? AND workspace=?", (key, ws)
        ).fetchone()
        if row is None or kind and kind != row["kind"]:
            raise ContractError("not_found", t("This bot or group is unavailable in this workspace"))
        return json.loads(row["value"])

    def put(self, ws, kind, data):
        self.db.execute(
            "INSERT INTO bot_entries VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
            (data["id"], ws, kind, encode(data)),
        )

    def profile(self, conversation):
        return (
            self.entry(conversation["workspaceId"], conversation["botId"], "bot")
            if conversation.get("botId")
            else None
        )

    def system_context(self, conversation):
        bot = self.profile(conversation)
        if bot is None:
            return ""
        parts = [
            f"You are {bot['name']}. Your duty: {bot['duty']}",
            "Leave duty unchanged unless the user asks.",
            "If this wake needs no message, return NO_REPLY. Your work will still appear in Runs.",
        ]
        parts.append("Selected knowledge: " + encode(bot["knowledge"]) + ". Read it when needed.")
        if not bot["duty"]:
            parts.append(
                "The bot has no duty yet. When the user describes it, call bot_manage set_duty for this bot to save it."
            )
        if conversation["kind"] == "group":
            group = self.entry(conversation["workspaceId"], conversation["lineId"], "group")
            members = [
                self.entry(conversation["workspaceId"], member) for member in group["members"]
            ]
            parts.append(
                "Group members (id, name, role): "
                + encode([[m["id"], m["name"], m["intro"]] for m in members])
            )
            parts.append(
                "To address another member in this group, write [[ref:their id]]. Unaddressed bot posts do not wake other bots."
            )
        return "\n".join(parts)

    def line(self, ws, bot, line):
        row = self.db.execute(
            "SELECT conversation FROM bot_lines WHERE bot=? AND line=?", (bot["id"], line)
        ).fetchone()
        if row:
            return row[0]
        kind = "bot" if line == bot["id"] else "group"
        if kind == "group" and bot["id"] not in self.entry(ws, line, "group")["members"]:
            raise ContractError("invalid_input", t("Choose a member of this group"))
        conversation = self.store.create(
            ws, kind=kind, title=bot["name"], model=bot["model"], bot_id=bot["id"], line_id=line
        )
        self.db.execute(
            "INSERT INTO bot_lines VALUES(?,?,?)", (bot["id"], line, conversation["id"])
        )
        self.apply_preferences(ws, conversation["id"], bot)
        return conversation["id"]

    def apply_preferences(self, ws, conversation, bot):
        self.agent.update_conversation(
            conversation,
            ws,
            {
                "modelId": bot["model"],
                "permission": bot["permission"],
                "thinking": {"effort": bot["thinking"]} if bot["thinking"] else None,
                "childrenEnabled": bot["subagents"],
                "memoryEnabled": bot["memoryOn"],
            },
        )

    def directory(self, ws, owner=None, id=None):
        """Compact bot, group and trigger descriptions shared by chat tools and public callers."""
        def routines(bot):
            return [json.loads(row[0]) for row in self.db.execute(
                "SELECT value FROM bot_routines WHERE workspace=? AND bot=? ORDER BY rowid", (ws, bot))]

        if id is None:
            entries = self.entries(ws)
            return {
                "here": owner,
                "bots": [{"id": item["id"], "name": item["name"], "intro": item["intro"], "paused": item["paused"],
                          "routines": [{"id": r["id"], "name": r["name"], "when": r["when"], "active": r["active"],
                                        "next": stamp(r["nextDue"]) if r["nextDue"] else None} for r in routines(item["id"])]}
                         for item in entries if "duty" in item],
                "groups": [{"id": item["id"], "name": item["name"], "members": item["members"]}
                           for item in entries if "members" in item],
                "chatSchedules": self.schedules(ws),
                "eventSources": {name: source["description"] for name, source in self.sources.items()}}
        if id in self.sources:
            return {"source": id, **self.sources[id]}
        if self.db.execute("SELECT 1 FROM bot_routines WHERE workspace=? AND id=?", (ws, id)).fetchone():
            return self.routine(ws, id)
        entry = self.entry(ws, id)
        return {**{key: value for key, value in entry.items() if key not in {"look", "last", "unread"}},
                       **({} if "members" in entry else {"routines": routines(id)})}

    def make_bot(self, ws, args, *, key=None):
        name = text_value(args.get("name"), t("Enter a bot name"))
        defaults = self.store.defaults(ws, kind="bot")
        model = args.get("model")
        if model:
            self.agent.catalogue.model(model)
        else:
            available = [m["id"] for m in self.agent.catalogue.list_models() if m["tools"] is not False]
            model = defaults["modelId"] if defaults["modelId"] in available else next(iter(available), None)
        bot = {
            "id": key or identity(),
            "name": name,
            "look": avatar(args.get("look", {"shape": "circle", "tint": "blue"})),
            "intro": args.get("intro", ""),
            "duty": args.get("duty", ""),
            "skills": [],
            "knowledge": [],
            "model": model,
            "permission": defaults["defaultPermission"],
            "thinking": "",
            "subagents": defaults["childrenEnabled"],
            "memoryOn": defaults["memoryEnabled"],
            "notify": {
                "needsYou": defaults["notifyNeedsYou"],
                "results": defaults["notifyResults"],
                "phone": defaults["notifyChannel"],
            },
            "pinned": False,
            "hidden": False,
            "unread": False,
            "paused": False,
            "failures": 0,
            # Only a bot without a duty asks for one, like Grok Bot; a preset or a bot the agent made already has it.
            "last": {"at": stamp(time.time()), "text": "" if args.get("duty") else t(WELCOME)},
        }
        with self.store.transaction():
            self.put(ws, "bot", bot)
            selected = {key: args[key] for key in ("skills", "knowledge") if key in args}
            if selected:
                self.edit(ws, bot["id"], selected)
            self.line(ws, bot, bot["id"])
            self.agent.usage.configure_group(bot["id"], ws, monthly_limit=None)
            if not bot["duty"]:
                self.message(ws, bot["id"], bot["id"], t(WELCOME))
        self.worker.ensure(ws)
        return {"id": bot["id"]}

    def make_group(self, ws, members, *, key=None):
        if (
            not isinstance(members, list)
            or not all(isinstance(m, str) for m in members)
            or len(set(members)) != len(members)
            or len(members) < 2
        ):
            raise ContractError("invalid_input", t("Choose at least two distinct group members"))
        bots = [self.entry(ws, m, "bot") for m in members]
        group = {
            "id": key or identity(),
            "name": "、".join(m["name"] for m in bots),
            "members": members,
            "rounds": 0,
            "limitNotified": False,
            "pinned": False,
            "hidden": False,
            "unread": False,
            "last": {"at": stamp(time.time()), "text": t("Group created")},
        }
        with self.store.transaction():
            self.put(ws, "group", group)
            for bot in bots:
                self.line(ws, bot, group["id"])
        return {"id": group["id"]}

    def message(self, ws, line, speaker, text, *, mentions=(), reply=None, turn=None):
        now = time.time()
        self.db.execute(
            "INSERT INTO bot_messages(workspace,line,speaker,at,text,mentions,reply,turn_id) VALUES(?,?,?,?,?,?,?,?)",
            (
                ws,
                line,
                speaker,
                now,
                text,
                encode(list(mentions)),
                encode(reply) if reply else None,
                turn,
            ),
        )
        entry = self.entry(ws, line)
        entry.update(
            last={"at": stamp(now), "text": text},
            unread=line not in self.visible.get(ws, {}).values(),
        )
        self.put(ws, "group" if "members" in entry else "bot", entry)

    def recipients(self, ws, args):
        line, text = args["id"], text_value(args["text"], t("Enter a message"))
        entry = self.entry(ws, line)
        if "members" in entry:
            names = {self.entry(ws, m)["name"]: m for m in entry["members"]}
            names["everyone"] = "everyone"
            pattern = (
                r"(?<!\S)@("
                + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
                + r")(?=$|[\s.,!?，。！？])"
            )
            text = re.sub(pattern, lambda m: "[[ref:" + names[m[1]] + "]]", text)
        mentions = MENTION.findall(text)
        reply = args.get("reply")
        if reply is not None and (
            not isinstance(reply, dict)
            or not isinstance(reply.get("bot"), str)
            or not isinstance(reply.get("text"), str)
        ):
            raise ContractError("invalid_input", t("Choose a message to reply to"))
        group = "members" in entry
        targets = (
            recipients(entry["members"], mentions, reply["bot"] if reply else None, user=True)
            if group
            else [line]
        )
        if reply and reply["bot"] not in (entry["members"] if group else [line]):
            raise ContractError("invalid_input", t("The quoted bot is not in this line"))
        if group and any(m not in [*entry["members"], "everyone"] for m in mentions):
            raise ContractError("invalid_input", t("A mentioned bot is no longer in this group"))
        return entry, text, mentions, reply, targets

    async def send(self, ws, args):
        """Post the user's message and hand it to each bot it is for. Every bot is checked first (a model, one that
        reads the attached images): the message goes to those that can take it and says which could not and why;
        only when none can is it not posted at all. Answers ``{skipped: [{botId, name, reason}]}``."""
        view = await self.agent.current_view(ws) if args.get("includeView") else None
        entry, text, mentions, reply, targets = self.recipients(ws, args)
        line = entry["id"]
        group = "members" in entry
        attachments = args.get("attachments", {})
        accepted, skipped, refusal = [], [], None
        for bot_id in targets:
            bot = self.entry(ws, bot_id)
            with self.store.transaction():
                conversation = self.line(ws, bot, line)
            try:
                selected = self.agent.admit(conversation, ws, attachments.get(bot_id, []))
            except ContractError as error:   # what the user sent, or the bot's model, does not fit
                refusal = refusal or error
                skipped.append({"botId": bot_id, "name": bot["name"], "reason": error.message})
                continue
            accepted.append((bot_id, conversation, selected))
        if refusal is not None and not accepted:
            raise refusal
        with self.store.transaction():
            if group:
                entry.update(rounds=0, limitNotified=False)
                self.put(ws, "group", entry)
            self.message(ws, line, None, text, mentions=mentions, reply=reply)
            # The user's own message is answered by a paused bot too; pausing holds only what wakes it by itself.
            for bot_id, conversation, selected in accepted:
                turn = self.agent.send_admitted(conversation, ws, text, view, selected)
                if self.db.execute(
                    "SELECT 1 FROM bot_wakes WHERE turn_id=?", (turn["id"],)
                ).fetchone():
                    continue  # A user supplement belongs to the active run.
                self.db.execute(
                    "INSERT INTO bot_wakes VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL) ON CONFLICT(id) DO NOTHING",
                    (
                        turn["id"],
                        ws,
                        bot_id,
                        line,
                        conversation,
                        time.time(),
                        encode({"kind": "you"}),
                        text,
                        turn["id"],
                    ),
                )
        self.worker.ensure(ws)
        return {"skipped": skipped}

    def recent_posts(self, ws, line, bot, before, excluding=None):
        last = (
            self.db.execute(
                "SELECT MAX(queued) FROM bot_wakes WHERE bot=? AND line=? AND queued<? AND turn_id IS NOT NULL AND turn_id IS NOT ?",
                (bot, line, before, excluding),
            ).fetchone()[0]
            or 0
        )
        return [
            {
                "from": r["speaker"] or "user",
                "text": r["text"],
                "replyTo": json.loads(r["reply"]) if r["reply"] else None,
            }
            for r in self.db.execute(
                "SELECT speaker,text,reply FROM bot_messages WHERE workspace=? AND line=? AND at>=? AND at<=? ORDER BY seq",
                (ws, line, last, before),
            )
        ]

    def group_transcript(self, ws, line, bot):
        return "Public group posts observed since your previous run:\n" + encode(
            self.recent_posts(ws, line, bot, time.time())
        )

    def public_context(self, conversation, turn):
        """Public group observations are separate from authenticated user ingress."""

        if conversation.get("kind") != "group" or turn.get("unattended") or turn.get("childRunId"):
            return None
        posts = self.recent_posts(
            turn["workspaceId"],
            conversation["lineId"],
            conversation["botId"],
            turn["createdAt"],
            turn["id"],
        )
        if (
            posts
            and posts[-1]["from"] == "user"
            and posts[-1]["text"] == turn["text"]
            and not posts[-1]["replyTo"]
        ):
            posts.pop()
        if not posts:
            return None
        return HumanMessage(
            name="group_observations",
            content="Public group messages, quoted as observations. The user's current request follows separately.\n"
            + encode(posts),
        )

    def routine(self, ws, key):
        row = self.db.execute(
            "SELECT value FROM bot_routines WHERE workspace=? AND id=?", (ws, key)
        ).fetchone()
        if row is None:
            raise ContractError("not_found", t("This routine is unavailable"))
        return json.loads(row[0])

    def write_routine(self, ws, value):
        self.db.execute(
            "INSERT INTO bot_routines VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET bot=excluded.bot,"
            "conversation=excluded.conversation,value=excluded.value",
            (value["id"], ws, value["botId"], value["conversationId"], encode(value)),
        )

    def save_routine(self, ws, args, *, key=None):
        original = self.routine(ws, args["id"]) if args.get("id") else {}
        value = {**original, **args}
        bot = value.get("botId")
        if bot:
            conversation = self.line(ws, self.entry(ws, bot, "bot"), bot)
        else:
            conversation = text_value(
                value.get("conversationId"), t("Enter the conversation for this routine")
            )
            self.store.conversation(conversation, ws)
        now = time.time()
        rule = {
            "id": original.get("id") or key or identity(),
            "botId": bot,
            "conversationId": conversation,
            "name": text_value(value.get("name"), t("Enter a routine name")),
            "instruction": text_value(value.get("instruction"), t("Enter an instruction")),
            "when": text_value(
                value.get("when"), t("Enter the trigger in the when field, in your own words")
            ),
            "active": value.get("active", True),
            "start": value.get("start", now),
            "nextDue": None,
        }
        if type(rule["active"]) is not bool:
            raise ContractError("invalid_input", t("Active must be boolean"))
        if value.get("source"):
            if not bot or value["source"] not in self.sources:
                raise ContractError(
                    "invalid_input", t("Choose an available trigger source for this bot")
                )

            params = value.get("params", {})
            errors = list(
                Draft202012Validator(self.sources[value["source"]]["params"]).iter_errors(params)
            )
            if errors:
                raise ContractError("invalid_input", errors[0].message)
            rule.update(
                kind="event", source=value["source"], params=params, cursor=original.get("cursor")
            )
        else:
            rule.update(
                kind="schedule",
                rrule=text_value(value.get("rrule"), t("Enter a recurrence")),
                zone=text_value(value.get("zone"), t("Enter a time zone")),
            )
            rule["nextDue"] = next_due(rule["rrule"], rule["zone"], rule["start"], now)
        with self.store.transaction():
            self.write_routine(ws, rule)
        self.worker.ensure(ws)
        return {"id": rule["id"]}

    def test_routine(self, ws, key, *, run=None):
        """Run a bot's routine once now (the routine's Test run): the run is in the bot's runs like a scheduled one."""
        rule = self.routine(ws, key)
        if not rule["botId"] or self.entry(ws, rule["botId"])["paused"]:
            raise ContractError("conflict", t("Resume this bot before testing its routine"))
        cause = {"kind": "you", "routine": rule["name"], "routineId": rule["id"], "test": True}
        with self.store.transaction():
            result = self.worker.queue(ws, rule["botId"], rule["botId"], rule["conversationId"], cause,
                                       rule["instruction"], time.time(), key=run)
        self.worker.ensure(ws)
        return {"id": result}

    def resume_routine(self, ws, routine):
        now = time.time()
        if routine["kind"] == "event":
            return {
                **routine,
                "cursor": None,
                "subscriptionVersion": routine.get("subscriptionVersion", 0) + 1,
            }
        cause, next_time = due_run(routine, now, True, True)
        if cause:
            cause["kind"] = "schedule"
            self.worker.queue(
                ws,
                routine["botId"],
                routine["botId"] or routine["conversationId"],
                routine["conversationId"],
                cause,
                routine["instruction"],
                now,
                skipped=True,
            )
            return {**routine, "nextDue": next_time}
        return routine

    def toggle_routine(self, ws, key, active):
        if type(active) is not bool:
            raise ContractError("invalid_input", t("Active must be true or false"))
        value = self.routine(ws, key)
        with self.store.transaction():
            if active and not value["active"]:
                value = self.resume_routine(ws, value)
            self.write_routine(ws, {**value, "active": active})
        self.worker.ensure(ws)

    def schedules(self, ws):
        result = []
        for r in decode_rows(
            self.db.execute(
                "SELECT value FROM bot_routines WHERE workspace=? AND bot IS NULL ORDER BY rowid",
                (ws,),
            )
        ):
            due = (
                datetime.fromtimestamp(r["nextDue"], ZoneInfo(r["zone"])).strftime("%a %H:%M")
                if r["nextDue"]
                else "—"
            )
            result.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "chat": r["conversationId"],
                    "on": r["active"],
                    "when": r["when"] if r["zone"] in r["when"] else r["when"] + " · " + r["zone"],
                    "next": due,
                }
            )
        return result

    async def notify(self, ws, key, line, reason, receipt, body, *, run):
        """Tell the user about a run, as the bot's notification settings say. A notification that did not go out is
        written on the run (the queued wake `run`)."""
        definition = self.entry(ws, key, "bot")
        notify = definition["notify"]
        if line in self.visible.get(ws, {}).values() or not notify[reason]:
            return
        target = {
            "workspaceId": ws,
            "panel": "agent.chat",
            "state": {"mode": "bots", "botLineId": line},
        }
        deliveries = [
            (
                "platform.notifications.toast",
                {"title": definition["name"][:200], "message": body[:4000], "navigation": target},
            )
        ]
        if notify["phone"]:
            deliveries.append(
                (
                    "platform.notifications.send",
                    {
                        "notificationId": receipt,
                        "title": definition["name"][:200],
                        "body": body[:20000],
                        "channelIds": [notify["phone"]],
                        "navigation": target,
                    },
                )
            )
        for action, payload in deliveries:
            try:
                await self.agent.ctx.call(action, payload, workspace=ws)
            except ContractError as exc:
                logging.getLogger(__name__).warning(
                    "Bot notification %s failed for %s: %s", action, receipt, exc.code
                )
                with self.store.transaction():
                    self.db.execute("UPDATE bot_wakes SET notice=? WHERE id=?", (encode(
                        {"action": action, "code": exc.code, "message": exc.message}), run))

    async def stop(self, ws, key, *, bot_scope=False):
        self.entry(ws, key)
        subject = "bot" if bot_scope else "line"
        with self.store.transaction():
            rows = self.db.execute(
                f"WITH RECURSIVE handed(id) AS (SELECT id FROM bot_wakes WHERE workspace=? AND {subject}=? "
                "UNION SELECT w.id FROM bot_wakes w JOIN handed h ON json_extract(w.cause,'$.parent')=h.id WHERE w.workspace=?) "
                "SELECT w.* FROM bot_wakes w JOIN handed h ON h.id=w.id WHERE w.result IS NULL",
                (ws, key, ws),
            ).fetchall()
            self.db.executemany(
                "UPDATE bot_wakes SET result='stopped' WHERE id=?", [(r["id"],) for r in rows]
            )
        for row in rows:
            if row["turn_id"] and self.store.turn(row["turn_id"])["state"] not in TERMINAL:
                await self.agent.stop(row["conversation"], ws)
        self.worker.signal(ws)
        return {"stopped": [r["id"] for r in rows]}

    async def patch(self, ws, key, change):
        change = self.checked(ws, key, change)
        if change.get("paused"):
            await self.stop(ws, key, bot_scope=True)
        self.write(ws, key, change)

    def edit(self, ws, key, change):
        """Change settings that need nothing stopped first, in the caller's transaction."""
        change = self.checked(ws, key, change)
        if "paused" in change:
            raise ContractError("invalid_input", t("Pause or resume the bot from its settings"))
        self.write(ws, key, change)

    def checked(self, ws, key, change):
        data = self.entry(ws, key)
        group = "members" in data
        common = {"name", "pinned", "hidden", "section"}
        fields = common | (
            {"members"}
            if group
            else {
                "look",
                "intro",
                "duty",
                "skills",
                "knowledge",
                "model",
                "thinking",
                "subagents",
                "permission",
                "memoryOn",
                "notify",
                "paused",
                "limit",
                "allowed",
            }
        )
        if set(change) - fields:
            raise ContractError("invalid_input", t("Unknown bot or group setting"))
        if "name" in change:
            change["name"] = text_value(change["name"], t("Enter a name"))
        if "look" in change:
            change["look"] = avatar(change["look"])
        for name in ("pinned", "hidden", "memoryOn", "subagents", "paused"):
            if name in change and type(change[name]) is not bool:
                raise ContractError("invalid_input", t("{name} requires a boolean", name=name))
        for name in ("skills", "knowledge", "allowed", "members"):
            if name in change and (
                not isinstance(change[name], list)
                or not all(isinstance(x, str) for x in change[name])
            ):
                raise ContractError("invalid_input", t("{name} requires a list of names", name=name))
        if "skills" in change and any(
            not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) for name in change["skills"]
        ):
            raise ContractError("invalid_input", t("Choose skill names from the library"))
        if "knowledge" in change:

            if any(
                (p != "/workspace" and not p.startswith(("/workspace/", "/knowledge/")))
                or ".." in PurePosixPath(p).parts
                or "\\" in p
                for p in change["knowledge"]
            ):
                raise ContractError("invalid_input", t("Choose knowledge from the library"))
        if "notify" in change:
            notify = change["notify"]
            if (
                not isinstance(notify, dict)
                or set(notify) != {"needsYou", "results", "phone"}
                or any(type(notify[k]) is not bool for k in ("needsYou", "results"))
                or not isinstance(notify["phone"], str)
            ):
                raise ContractError(
                    "invalid_input", t("Choose a notification channel and when to notify")
                )
        if (
            "section" in change
            and change["section"]
            and not self.db.execute(
                "SELECT 1 FROM bot_sections WHERE workspace=? AND name=?", (ws, change["section"])
            ).fetchone()
        ):
            raise ContractError("not_found", t("The section no longer exists"))
        if "members" in change:
            if len(set(change["members"])) != len(change["members"]) or len(change["members"]) < 2:
                raise ContractError("invalid_input", t("A group needs two distinct bots"))
            for member in change["members"]:
                self.entry(ws, member, "bot")
        return change

    def write(self, ws, key, change):
        with self.store.transaction():
            data = self.entry(ws, key)
            group = "members" in data
            if "limit" in change:
                status = self.agent.usage.group_status(key)
                self.agent.usage.configure_group(
                    key, ws, monthly_limit=change.pop("limit"), expected_version=status["version"]
                )
            if "allowed" in change:
                remaining = change.pop("allowed")
                for row in self.db.execute(
                    "SELECT conversation FROM bot_lines WHERE bot=?", (key,)
                ):
                    for rule in self.agent.permissions.list(row[0]):
                        if rule["action"] not in remaining:
                            self.agent.permissions.revoke(row[0], rule["id"])
            if change.get("paused") is False and data["paused"]:
                data["failures"] = 0
                for routine in decode_rows(
                    self.db.execute("SELECT value FROM bot_routines WHERE bot=?", (key,))
                ):
                    if routine["active"]:
                        self.write_routine(ws, self.resume_routine(ws, routine))
            data.update(change)
            self.put(ws, "group" if group else "bot", data)
            if group:
                for member in data["members"]:
                    self.line(ws, self.entry(ws, member), key)
            elif change.keys() & {"model", "thinking", "subagents", "permission", "memoryOn"}:
                for row in self.db.execute(
                    "SELECT conversation FROM bot_lines WHERE bot=?", (key,)
                ):
                    self.apply_preferences(ws, row[0], data)
        self.worker.ensure(ws)

    def section(self, ws, old, new):
        if new:
            new = text_value(new, t("Enter a section name"))
        with self.store.transaction():
            if new and not old:
                if self.db.execute(
                    "SELECT 1 FROM bot_sections WHERE workspace=? AND name=?", (ws, new)
                ).fetchone():
                    raise ContractError("conflict", t("This section exists"))
                self.db.execute("INSERT INTO bot_sections VALUES(?,?)", (ws, new))
            elif old:
                if (
                    new
                    and new != old
                    and self.db.execute(
                        "SELECT 1 FROM bot_sections WHERE workspace=? AND name=?", (ws, new)
                    ).fetchone()
                ):
                    raise ContractError("conflict", t("This section exists"))
                self.db.execute("DELETE FROM bot_sections WHERE workspace=? AND name=?", (ws, old))
                if new:
                    self.db.execute("INSERT INTO bot_sections VALUES(?,?)", (ws, new))
                for item in self.entries(ws):
                    if item.get("section") == old:
                        self.put(
                            ws, "group" if "members" in item else "bot", {**item, "section": new}
                        )

    async def delete_entry(self, ws, key):
        item = self.entry(ws, key)
        await self.stop(ws, key, bot_scope="members" not in item)
        lines = self.db.execute(
            "SELECT conversation FROM bot_lines WHERE bot=? OR line=?", (key, key)
        ).fetchall()
        for (line,) in lines:
            await self.agent.stop(line, ws)
            await self.agent.delete(line, ws)
        with self.store.transaction():
            self.db.execute("DELETE FROM bot_lines WHERE bot=? OR line=?", (key, key))
            self.db.execute("DELETE FROM bot_routines WHERE bot=?", (key,))
            self.db.execute("DELETE FROM bot_wakes WHERE bot=? OR line=?", (key, key))
            self.db.execute("DELETE FROM bot_messages WHERE line=?", (key,))
            self.db.execute(
                "UPDATE bot_messages SET turn_id=NULL,speaker=? WHERE speaker=?",
                ("former:" + item["name"], key),
            )
            self.db.execute("DELETE FROM bot_edits WHERE bot=?", (key,))
            self.agent.memory.forget_bot(key)
            self.db.execute("DELETE FROM bot_entries WHERE id=?", (key,))
            if "members" not in item:
                for group in self.entries(ws):
                    if key in group.get("members", []):
                        group["members"].remove(key)
                        self.put(ws, "group", group)
        if "duty" in item:
            self.agent.usage.delete_groups(ws, key)
        self.worker.signal(ws)

    async def delete_workspace(self, ws, *, preview):
        values = self.entries(ws)
        if preview:
            return [("Group" if "members" in b else "Bot") + " · " + b["name"] for b in values]
        if ws in self.worker.tasks:

            task = self.worker.tasks[ws]
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with self.store.transaction():
            self.db.execute(
                "DELETE FROM bot_lines WHERE bot IN (SELECT id FROM bot_entries WHERE workspace=?)",
                (ws,),
            )
            for table in (
                "bot_routines",
                "bot_wakes",
                "bot_messages",
                "bot_entries",
                "bot_sections",
                "bot_edits",
            ):
                self.db.execute(f"DELETE FROM {table} WHERE workspace=?", (ws,))
        self.agent.usage.delete_groups(ws)
        return []

    def restore(self, ws, call_id, direction):
        row = self.db.execute(
            "SELECT * FROM bot_edits WHERE workspace=? AND id=?", (ws, call_id)
        ).fetchone()
        if row is None:
            raise ContractError("not_found", t("This bot change is unavailable"))
        value = json.loads(row["before"] if direction == "undo" else row["after"])
        if row["field"] == "routine":
            with self.store.transaction():
                if value is None:
                    self.db.execute(
                        "DELETE FROM bot_routines WHERE workspace=? AND id=?",
                        (ws, row["object_id"]),
                    )
                else:
                    self.write_routine(ws, value)
            self.worker.ensure(ws)
            self.changed(ws)
            return
        bot = self.entry(ws, row["bot"], "bot")
        if row["field"] == "resources":
            bot.update(value)
        else:
            bot[row["field"]] = value
        with self.store.transaction():
            self.put(ws, "bot", bot)
