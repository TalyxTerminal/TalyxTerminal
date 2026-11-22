"""Private settings reads and edits from the global and workspace sections."""
import asyncio
import os
import time
from pathlib import Path

from talyx_sdk import ContractError
from talyx_sdk.i18n import t


async def page_request(service, operation, args, workspace):
    scope = args.get("scope", "global")
    if scope not in {"global", "workspace"} or scope == "workspace" and not workspace:
        raise ContractError("invalid_target", t("Choose a settings scope with an open workspace"))
    layer = workspace if scope == "workspace" else None
    kind = args.get("kind", "general")
    if kind not in {"general", "chat", "bot"}:
        raise ContractError("invalid_input", t("Choose an Agent settings page"))
    if operation == "set_defaults":
        return service.configure_defaults(args.get("changes", {}), layer, kind=kind, reset=args.get("reset"))
    if operation == "spending":
        groups = service.usage.summary(group_by="model", month=service.usage.month(time.time()))["groups"]
        limits = []
        for row in service.store.db.execute("SELECT id,json_extract(value,'$.name') FROM bot_entries WHERE kind='bot'"):
            status = service.usage.group_status(row[0])
            if status["monthlyLimit"] is not None:
                limits.append({"name":row[1], **status})
        return {"spent": str(service.usage.month_spent()), "bots": limits,
                "unpriced": [g for g in groups if g["tokenOnlyRequests"]]}
    if operation == "page":
        channels = await service.ctx.call("platform.notifications.channels", {}) if kind == "bot" else []
        return {"defaults": service.store.defaults(layer, kind=kind), "overrides": service.store.overrides(layer, kind),
                "channels": channels}
    if operation == "instructions":
        root = Path(await service.ctx.platform.workspace_folder(workspace)) if layer else service.user_root
        path = root / "AGENTS.md"
        if "text" in args:
            value = args["text"]
            if not isinstance(value, str) or len(value.encode("utf-8")) > 1024 * 1024:
                raise ContractError("invalid_input", t("Instructions must be UTF-8 text of at most 1 MiB"))
            await asyncio.to_thread(path.write_text, value, encoding="utf-8")
        try:
            return await asyncio.to_thread(path.read_text, encoding="utf-8-sig")
        except FileNotFoundError:
            return ""
    if operation == "bind_folder":
        service.files.bind(layer, args["path"], args["writable"])
    if operation == "open_folder":
        if not workspace:
            raise ContractError("invalid_target", t("Open a workspace first"))
        folder = await service.ctx.platform.workspace_folder(workspace)
        await asyncio.to_thread(os.startfile, folder)
    if operation == "unbind_folder":
        service.files.unbind(workspace, args["id"])
    if operation == "folder_access":
        if type(args.get("writable")) is not bool:
            raise ContractError("invalid_input", t("Choose a knowledge folder access level"))
        with service.store.transaction():
            row = service.store.db.execute("UPDATE knowledge_folders SET writable=? WHERE id=? AND (workspace IS NULL OR workspace=?)",
                                           (args["writable"], args["id"], workspace))
            if not row.rowcount:
                raise ContractError("not_found", t("This knowledge folder is unavailable"))
    return {"folders": service.files.folders(workspace),
            "workspaceFolder": str(await service.ctx.platform.workspace_folder(workspace)) if workspace else None}
