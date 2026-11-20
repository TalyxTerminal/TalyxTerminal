"""The Bot panel's private read model and commands."""

import asyncio
import base64
import binascii
import json
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from talyx_sdk import ContractError

from ..skills import SkillsService
from .records import decode_rows, stamp


def wake_label(cause):
    return cause.get("routine") or {
        "you": "Your messages",
        "mention": "Other bots",
        "resumed": "Resumed runs",
    }.get(cause["kind"], cause["kind"])


def present_wake(cause):
    if cause["kind"] == "catchup":
        return {**cause, "due": stamp(cause["due"])}
    return cause


def project_bot(bots, ws, bot):
    key = bot["id"]
    rows = bots.db.execute(
        "SELECT w.*,t.state,t.value FROM bot_wakes w LEFT JOIN turns t ON w.turn_id=t.id "
        "WHERE w.workspace=? AND w.bot=? ORDER BY w.queued DESC",
        (ws, key),
    ).fetchall()
    ledger = bots.agent.usage
    budget = ledger.group_status(key)
    costs = ledger.run_costs(key, budget["month"])
    time_zone = ZoneInfo(ledger.settings()["timezone"])
    effects_by_turn = defaultdict(list)
    for turn_id, raw, undone in bots.db.execute(
        "SELECT ch.turn_id,ch.item,ch.undone FROM turn_changes ch JOIN bot_wakes w ON w.turn_id=ch.turn_id WHERE w.bot=? AND w.workspace=? ORDER BY ch.rowid",
        (key, ws),
    ):
        item = json.loads(raw)
        effects_by_turn[turn_id].append(
            {
                "id": item["callId"],
                "kind": "memory" if item.get("memory") else "change",
                "label": item["label"],
                "undone": bool(undone),
                "reversible": item["reversible"],
            }
        )
    month = defaultdict(lambda: {"runs": 0, "amounts": defaultdict(Decimal), "unpriced": False})
    totals = defaultdict(Decimal)
    runs = []
    for row in rows:
        cause = json.loads(row["cause"])
        turn = json.loads(row["value"]) if row["value"] else {}
        start = turn.get("startedAt") or row["queued"]
        duration = max(0, int((turn.get("finishedAt") or time.time()) - start))
        # Costs are this month's, as the budget counts them; an older run shows its tokens nowhere here.
        tokens, amounts, unpriced = costs.get(row["turn_id"], (0, {}, False))
        started_this_month = datetime.fromtimestamp(start, time_zone).strftime("%Y-%m") == budget["month"]
        if started_this_month or amounts or unpriced:
            part = month[wake_label(cause)]
            part["runs"] += int(started_this_month)
            part["unpriced"] |= unpriced
            for currency, amount in amounts.items():
                part["amounts"][currency] += amount
        for currency, amount in amounts.items():
            totals[currency] += amount
        outcome = row["result"] or {
            "waiting_approval": "waiting",
            "failed": "failed",
            "cancelled": "stopped",
        }.get(row["state"], "running")
        amounts_text = [f"{amount:.4f} {currency}" for currency, amount in sorted(amounts.items())]
        if unpriced:
            amounts_text.append("Unpriced usage")
        effects = effects_by_turn[row["turn_id"]]
        runs.append(
            {
                "id": row["id"],
                "at": stamp(start),
                "wake": present_wake(cause),
                "outcome": outcome,
                "duration": f"{duration // 60}m {duration % 60}s",
                "cost": ", ".join(amounts_text),
                "tokens": str(tokens),
                "effects": effects,
                "notice": json.loads(row["notice"]) if row["notice"] else None,
            }
        )
    routines = []
    for routine in decode_rows(
        bots.db.execute(
            "SELECT value FROM bot_routines WHERE workspace=? AND bot=? ORDER BY rowid", (ws, key)
        )
    ):
        when = routine["when"] + (
            " · " + routine["zone"]
            if routine["kind"] == "schedule" and routine["zone"] not in routine["when"]
            else ""
        )
        if routine["kind"] == "event" and routine["source"] not in bots.sources:
            when += " · Trigger unavailable"
        routines.append(
            {
                **routine,
                "when": [when],
                "next": stamp(routine["nextDue"]) if routine["nextDue"] else None,
            }
        )
    state = "idle"
    if runs:
        state = {"failed": "failed", "running": "running", "waiting": "approval"}.get(
            runs[0]["outcome"], "idle"
        )
    if any(run["outcome"] == "waiting" for run in runs):
        state = "approval"
    elif any(run["outcome"] == "running" for run in runs):
        state = "running"
    rules = set()
    for (line,) in bots.db.execute("SELECT conversation FROM bot_lines WHERE bot=?", (key,)):
        rules.update(r["action"] for r in bots.agent.permissions.list(line))
    return {
        **bot,
        "model": bot["model"] or "",
        "state": "paused" if bot["paused"] or budget["paused"] else state,
        "runs": runs,
        "routines": routines,
        "allowed": sorted(rules),
        "spend": {
            "month": float(budget["spent"]),
            "currency": budget["currency"],
            "amounts": {k: str(v) for k, v in sorted(totals.items())},
            "unpriced": any(v["unpriced"] for v in month.values()),
            "limit": float(budget["monthlyLimit"]) if budget["monthlyLimit"] is not None else None,
            "parts": [{"label": label, "runs": value["runs"], "unpriced": value["unpriced"],
                       "amounts": {k: str(v) for k, v in sorted(value["amounts"].items())}} for label, value in month.items()],
        },
    }


def thread(bots, ws, line):
    bots.entry(ws, line)
    blocks = []
    for row in bots.db.execute(
        "SELECT m.*, w.id AS graph_result FROM bot_messages m LEFT JOIN bot_wakes w ON w.turn_id=m.turn_id AND w.line=m.line AND w.bot=m.speaker WHERE m.workspace=? AND m.line=? ORDER BY m.seq",
        (ws, line),
    ):
        if row["graph_result"]:
            continue  # the answer and its tools are projected together below
        key = f"message-{row['seq']}"
        item = {
            "kind": "user" if row["speaker"] is None else "answer",
            "id": key,
            "text": row["text"],
            "refs": {},
        }
        blocks.append(
            {
                "id": key,
                "at": stamp(row["at"]),
                "time": row["at"],
                "bot": row["speaker"],
                "items": [item],
                "reply": json.loads(row["reply"]) if row["reply"] else None,
            }
        )
    running = False
    for row in bots.db.execute(
        "SELECT * FROM bot_wakes WHERE workspace=? AND line=? AND turn_id IS NOT NULL ORDER BY queued",
        (ws, line),
    ):
        if row["result"] in {"skipped", "quiet"}:
            continue
        turn = bots.store.turn(row["turn_id"])
        values = decode_rows(
            bots.db.execute(
                "SELECT value FROM cards WHERE turn_id=? AND "
                "COALESCE(json_extract(value,'$.role'),'')!='user' ORDER BY created,rowid",
                (turn["id"],),
            )
        )
        running |= turn["state"] in {"queued", "running"}
        if not values or any(
            c.get("role") == "assistant" and c.get("text", "").strip() == "NO_REPLY" for c in values
        ):
            continue
        snapshot = {
            "conversation": bots.store.describe(bots.store.conversation(row["conversation"], ws)),
            "turns": [turn],
            "cards": values,
            "watermark": 0,
            "nextBefore": None,
        }
        blocks.append(
            {
                "id": row["id"],
                "bot": row["bot"],
                "at": stamp(row["queued"]),
                "time": row["queued"],
                "wake": present_wake(json.loads(row["cause"])),
                "snapshot": snapshot,
            }
        )
    return {"id": line, "running": running, "blocks": sorted(blocks, key=lambda item: item["time"])}


async def library(bots, ws):
    folder = Path(await bots.agent.ctx.platform.workspace_folder(ws))

    folders = bots.agent.files.folders(ws)
    skills = [
        {"name": s["name"], "detail": s["description"], "place": "global"}
        for s in bots.agent.skills.snapshot()["items"]
    ]
    local = folder / "skills"
    if local.is_dir():
        skills.extend(
            {"name": s["name"], "detail": s["description"], "place": "workspace"}
            for s in SkillsService(local).snapshot()["items"]
        )

    def files():
        result = []
        mounts = [
            ("/workspace", folder, "workspace"),
            *[
                (
                    f"/knowledge/{row['id']}",
                    Path(row["path"]),
                    "workspace" if row["workspace"] else "global",
                )
                for row in folders
            ],
        ]
        for prefix, path, place in mounts:
            if not path.is_dir():
                continue
            for item in [path, *path.rglob("*")]:
                if not item.resolve().is_relative_to(path.resolve()):
                    continue
                is_dir = item.is_dir()
                result.append(
                    {
                        "path": prefix
                        + ("/" + item.relative_to(path).as_posix() if item != path else ""),
                        "label": path.name if item == path else item.relative_to(path).as_posix(),
                        "folder": path.name,
                        "place": place,
                        "kind": "folder" if is_dir else "file",
                        "size": "" if is_dir else f"{item.stat().st_size:,} B",
                    }
                )
        return result

    skills = list({skill["name"]: skill for skill in skills}.values())
    return {
        "knowledge": await asyncio.to_thread(files),
        "skills": skills,
        "channels": await bots.agent.ctx.call("platform.notifications.channels", {}),
    }


async def upload(bots, ws, args):
    bot = bots.entry(ws, args["id"], "bot")
    name = args["name"]
    if not isinstance(name, str) or Path(name).name != name or name in {".", "..", ""}:
        raise ContractError("invalid_input", "Choose a file name")
    folder = Path(await bots.agent.ctx.platform.workspace_folder(ws))
    path = (folder / name).resolve()
    if path.parent != folder.resolve():
        raise ContractError("invalid_input", "The upload must stay in the workspace folder")
    try:
        data = base64.b64decode(args["data"], validate=True)
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError as exc:
        raise ContractError(
            "conflict", "A file with this name already exists in the workspace"
        ) from exc
    except (ValueError, binascii.Error) as exc:
        raise ContractError("invalid_input", "Could not read the uploaded file") from exc
    await bots.patch(ws, bot["id"], {"knowledge": [*bot["knowledge"], "/workspace/" + name]})


async def dispatch(bots, ws, operation, args):
    if ws is None:
        raise ContractError("invalid_target", "Open a workspace to use Bots")
    if not isinstance(args, dict):
        raise ContractError("invalid_input", "Bot requests require an object")
    if operation == "list":
        entries = bots.entries(ws)
        return {
            "bots": [project_bot(bots, ws, v) for v in entries if "duty" in v],
            "groups": [v for v in entries if "members" in v],
            "sections": [
                r[0]
                for r in bots.db.execute(
                    "SELECT name FROM bot_sections WHERE workspace=? ORDER BY rowid", (ws,)
                )
            ],
        }
    if operation == "read":
        return thread(bots, ws, args["id"])
    if operation == "library":
        return await library(bots, ws)
    if operation == "upload":
        return await upload(bots, ws, args)
    if operation == "presence":
        line = args.get("id")
        if line:
            entry = bots.entry(ws, line)
            with bots.store.transaction():
                bots.put(
                    ws, "group" if "members" in entry else "bot", {**entry, "unread": False}
                )
        panels = bots.visible.setdefault(ws, {})
        panel = args.get("panelId") or ""
        if line:
            panels[panel] = line
        else:
            panels.pop(panel, None)
        return None
    if operation == "create":
        return bots.make_bot(ws, args)
    if operation == "create_group":
        return bots.make_group(ws, args["members"])
    if operation == "patch":
        return await bots.patch(ws, args["id"], args["changes"])
    if operation == "delete":
        return await bots.delete_entry(ws, args["id"])
    if operation == "section":
        return bots.section(ws, args.get("old"), args.get("name"))
    if operation == "recipients":
        entry, _, _, _, targets = bots.recipients(ws, args)
        with bots.store.transaction():
            return {key: bots.line(ws, bots.entry(ws, key), entry["id"]) for key in targets}
    if operation == "test_routine":
        return bots.test_routine(ws, args["id"])
    if operation == "send":
        return await bots.send(ws, args)
    if operation == "stop":
        return await bots.stop(ws, args["id"])
    if operation == "schedules":
        return bots.schedules(ws)
    if operation == "save_routine":
        return bots.save_routine(ws, args)
    if operation == "toggle_routine":
        return bots.toggle_routine(ws, args["id"], args["active"])
    if operation == "delete_routine":
        bots.routine(ws, args["id"])
        with bots.store.transaction():
            bots.db.execute("DELETE FROM bot_routines WHERE id=?", (args["id"],))
        bots.worker.signal(ws)
        return None
    if operation == "retry":
        row = bots.db.execute(
            "SELECT conversation,turn_id FROM bot_wakes WHERE workspace=? AND id=? AND result='failed'",
            (ws, args["id"]),
        ).fetchone()
        if row is None:
            raise ContractError("conflict", "This bot run cannot be retried now")
        await bots.agent.card_decision("retry", {"conversationId": row[0], "cardId": args["cardId"]}, ws)
        with bots.store.transaction():
            bots.db.execute("UPDATE bot_wakes SET result=NULL WHERE id=?", (args["id"],))
        bots.worker.ensure(ws)
        return None
    if operation == "revert_run":
        row = bots.db.execute(
            "SELECT conversation,turn_id FROM bot_wakes WHERE id=? AND workspace=?",
            (args["id"], ws),
        ).fetchone()
        if not row or not row["turn_id"]:
            raise ContractError("not_found", "This run's changes are unavailable")
        return await bots.agent.changes.revert(ws, row[0], row[1], args["direction"])
    raise ContractError("invalid_input", "Unknown bot operation")
