"""Private graph tools for managing the user's bots, groups and chat schedules."""

import json
from uuid import NAMESPACE_URL, uuid5

from langchain_core.tools import StructuredTool
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from ..store import encode
from ..tools import TOOL_CALL
from .api import dispatch
from .records import MENTION, text_value


def bot_tools(service, turn):
    bots = service.bots
    ws = turn["workspaceId"]
    conversation = service.execution_conversation(turn)
    owner = conversation.get("botId")

    async def read(id: str | None = None):
        """Without id: this workspace's bots (each with its routines: name, when, on or off, next run), groups, this
        chat's own schedules and the module events a bot routine can follow. With id: that bot, group, routine or event
        source in full (a bot's duty, skills, knowledge, settings and routines' instructions; an event's params)."""
        return encode(bots.directory(ws, owner, id))

    async def manage(operation: str, value: dict):
        """Manage the user's bots or schedules. create_bot takes name, intro and duty and optionally chosen skills and knowledge. set_duty takes id and duty;
        use only on creation or at the user's explicit request.
        set_resources takes id and the chosen skills (names) and knowledge (virtual paths).
        post_to_group sends a message to a group this bot belongs to; takes groupId and text.
        Mention a member as [[ref:member id]] to wake it. Use when the duty specifies a different destination.
        create_group takes member IDs. save_routine takes name, instruction, when (user's wording), zone (IANA),
        rrule (RFC 5545 e.g. FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=0;BYSECOND=0), optional start
        (Unix seconds), optional id to edit. For a bot's module event supply source
        and params instead of zone and rrule. botId targets a bot; otherwise the routine belongs to this chat.
        toggle_routine takes id and active. run_routine takes the id of a bot's routine and runs it once now, as its
        Test run does: the run shows in that bot's runs, the way the schedule will run it.
        move_to_bot takes name,intro,duty and moves this chat's schedules to it."""

        call_id = uuid5(NAMESPACE_URL, turn["id"] + ":" + TOOL_CALL.get()).hex

        def created(label):
            # Listed on the turn's change card and counted as "its own" for deleting; not undoable.
            service.changes.add(turn, "bots", {"callId": call_id, "objectId": call_id, "kind": "created", "label": label,
                                               "openTarget": None, "reversible": False})

        if operation == "post_to_group":

            if not owner:
                raise ContractError("invalid_input", t("A group post must come from a member bot"))
            group = bots.entry(ws, text_value(value.get("groupId"), t("Enter the group ID")), "group")
            if owner not in group["members"]:
                raise ContractError(
                    "permission_denied", t("This bot is not a member of the destination group")
                )
            message = text_value(value.get("text"), t("Enter a group message"))
            tag = "post:" + call_id
            with bots.store.transaction():
                if bots.db.execute("SELECT 1 FROM bot_messages WHERE turn_id=?", (tag,)).fetchone():
                    return "Posted"
                mentions = MENTION.findall(message)
                wake = bots.db.execute(
                    "SELECT id FROM bot_wakes WHERE turn_id=?", (turn["id"],)
                ).fetchone()
                bots.message(ws, group["id"], owner, message, mentions=mentions, turn=tag)
                notice = bots.worker.group_handoffs(
                    ws,
                    group["id"],
                    bots.entry(ws, owner),
                    message,
                    mentions,
                    wake[0] if wake else turn["id"],
                )
            bots.changed(ws)
            bots.worker.ensure(ws)
            reason, body = notice or ("results", message)
            await bots.notify(ws, owner, group["id"], reason, call_id, body, run=wake[0] if wake else None)
            return (
                "Posted to the group. Return NO_REPLY if the original line needs no further answer."
            )
        if operation in {"create_bot", "move_to_bot"}:
            if not bots.db.execute(
                "SELECT 1 FROM bot_entries WHERE workspace=? AND id=?", (ws, call_id)
            ).fetchone():
                with bots.store.transaction():
                    result = bots.make_bot(ws, value, key=call_id)
                    created(value.get("name") or "Bot")
            else:
                result = {"id": call_id}
            if operation == "move_to_bot":
                bot = bots.entry(ws, call_id)
                line = bots.line(ws, bot, call_id)
                with bots.store.transaction():
                    for row in bots.db.execute(
                        "SELECT value FROM bot_routines WHERE conversation=? AND bot IS NULL",
                        (turn["conversationId"],),
                    ).fetchall():
                        routine = json.loads(row[0])
                        bots.write_routine(
                            ws, {**routine, "botId": call_id, "conversationId": line}
                        )
            return encode(result)
        if operation == "run_routine":
            if bots.db.execute("SELECT 1 FROM bot_wakes WHERE id=?", (call_id,)).fetchone() is None:
                bots.test_routine(ws, text_value(value.get("id"), t("Enter the routine ID")), run=call_id)
            return "Started. The run shows in the bot's runs; its answer comes in the bot's own chat."
        existing = bots.db.execute(
            "SELECT * FROM bot_edits WHERE workspace=? AND id=?", (ws, call_id)
        ).fetchone()
        if existing:
            return encode({"id": existing["object_id"]})

        def record(bot_id, field, object_id, before, after, label):
            bots.db.execute(
                "INSERT INTO bot_edits VALUES(?,?,?,?,?,?,?)",
                (call_id, ws, bot_id, field, object_id, encode(before), encode(after)),
            )
            service.changes.add(
                turn,
                "bots",
                {
                    "callId": call_id,
                    "objectId": object_id,
                    "kind": "created"
                    if before is None
                    else "deleted"
                    if after is None
                    else "updated",
                    "label": label,
                    "openTarget": None,
                    "reversible": True,
                },
            )

        if operation == "set_resources":
            bot = bots.entry(ws, value.get("id") or owner, "bot")
            selected = {key: value[key] for key in ("skills", "knowledge") if key in value}
            if not selected:
                raise ContractError("invalid_input", t("Choose skills or knowledge"))
            with bots.store.transaction():
                before = {key: bot[key] for key in selected}
                bots.edit(ws, bot["id"], selected)
                record(
                    bot["id"],
                    "resources",
                    bot["id"],
                    before,
                    selected,
                    bot["name"] + " skills and knowledge",
                )
            return "Saved"
        if operation == "set_duty":
            bot = bots.entry(ws, value.get("id") or owner, "bot")
            before, after = bot["duty"], text_value(value.get("duty"), t("Enter a duty"))
            with bots.store.transaction():
                bots.edit(ws, bot["id"], {"duty": after})
                record(bot["id"], "duty", bot["id"], before, after, bot["name"] + " duty")
            return "Saved"
        if operation == "create_group":
            if not bots.db.execute(
                "SELECT 1 FROM bot_entries WHERE workspace=? AND id=?", (ws, call_id)
            ).fetchone():
                with bots.store.transaction():
                    result = bots.make_group(ws, value.get("members"), key=call_id)
                    created(bots.entry(ws, result["id"])["name"])
                return encode(result)
            return encode({"id": call_id})
        if operation in {"save_routine", "toggle_routine"}:
            before = bots.routine(ws, value["id"]) if value.get("id") else None
            with bots.store.transaction():
                if operation == "save_routine":
                    args = {
                        **(
                            {}
                            if before
                            else {"conversationId": turn["conversationId"], "botId": owner}
                        ),
                        **value,
                    }
                    result = bots.save_routine(ws, args, key=call_id)
                else:
                    bots.toggle_routine(
                        ws, text_value(value.get("id"), t("Enter the routine ID")), value.get("active")
                    )
                    result = {"id": value["id"]}
                after = bots.routine(ws, result["id"])
                record(after["botId"] or "", "routine", result["id"], before, after, after["name"])
            return encode(result)
        raise ContractError("invalid_input", t("Unknown bot management operation"))

    async def delete(id: str, kind: str):
        """Delete a routine (kind=routine) or a bot or group (kind=bot or group) by its workspace ID."""
        if kind not in {"routine", "bot", "group"}:
            raise ContractError("invalid_input", t("Choose routine, bot or group"))
        if id == owner:
            raise ContractError("invalid_input", t("Stop the current bot before deleting it"))
        await dispatch(bots, ws, "delete_routine" if kind == "routine" else "delete", {"id": id})
        return "Deleted"

    return [
        StructuredTool.from_function(coroutine=handler, name=name)
        for name, handler in (("bot_read", read), ("bot_manage", manage), ("bot_delete", delete))
    ]
