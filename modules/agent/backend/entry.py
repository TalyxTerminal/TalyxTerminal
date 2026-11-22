"""Agent's declarations: public research, conversation, bot and settings actions, panel events, and the panel's and
settings pages' private routes. The contract is generated from this file.

Agent is an AI client of the platform: its conversations read the platform's AI catalog and call actions through
``platform.ai.call``. Whether to ask the user first is Agent's own decision (``service.decision``)."""
import asyncio
import importlib
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import Field
from talyx_sdk import ContractError
from talyx_sdk.declare import Model, Module
from talyx_sdk.i18n import t
from talyx_sdk.process import Call, Context

from .action_inputs import BotInput, ChatInput, OperationResult, ResultFile, SettingsInput

module = Module("agent")
service = None   # the running AgentService, set when the process starts


class Empty(Model):
    pass


class OpenFile(Model):
    path: Annotated[str, Field(min_length=1)]


module.extension_point("open_file", description="Open a selected workspace file or attachment for the user.",
                       descriptor=Empty, actions={"open": (OpenFile, Empty, "Open the file for viewing or editing.")})


class SearchWebInput(Model):
    mode: Literal["search", "page"]
    query: Annotated[str, Field(min_length=1)] | None = None
    objective: Annotated[str, Field(max_length=4000)] | None = None
    url: Annotated[str, Field(min_length=1)] | None = None


class ConversationChanged(Model):
    conversationId: Annotated[str, Field(min_length=1)]
    workspaceId: Annotated[str, Field(min_length=1)]
    seq: Annotated[int, Field(ge=1)]


module.event("conversation_changed", ConversationChanged,
             "A conversation got new events; read them from the conversation's event cursor.")
module.event("settings_changed", Empty, "Agent's connections, models, credentials or defaults changed; read the settings again.")

# The settings operations that change what a chat can choose (its models and their defaults).
SETTINGS_CHANGES = {"put_connection", "put_model", "delete_connection", "delete_model", "credential", "delete_credential",
                    "set_defaults", "search"}


@module.on_start
async def start(ctx: Context) -> None:
    global service
    # Importing the service (providers, HTTP clients, search) takes most of a second: a worker thread does it, so the
    # event loop never stops for it.
    await asyncio.to_thread(importlib.import_module, ".service", __package__)
    from .service import AgentService
    service = AgentService(ctx, await ctx.platform.storage())
    await service.start()
    await service.activities.start()
    await service.bots.start()


@module.on_copy_workspace
async def copy_workspace(source: str, copy: str) -> None:
    """A copied workspace gets its own copies of the conversations and memories (the original's stay as they are)."""
    await service.copy_workspace(source, copy)


@module.on_delete_workspace
async def delete_workspace(workspace_id: str, *, preview: bool) -> list[str]:
    return await service.delete_workspace(workspace_id, preview=preview)


@module.action(effects=["read", "external"], examples=[{"mode": "search", "query": "BTC market news"}],
               errors=["invalid_input"])
async def search_web(input: SearchWebInput, call: Call) -> dict[str, Any]:
    """Search current sources or read an exact HTTP(S) page. mode=search requires query and optionally objective;
    mode=page requires url. Uses the configured search source or supported provider search and records its actual
    usage; errors never switch sources. Cite observed URLs directly as Markdown links. Examples:
    {"mode":"search","query":"BTC market news"}; {"mode":"page","url":"https://example.org/report"}."""
    value = input.model_dump(exclude_none=True)
    if value["mode"] == "page":
        if "url" not in value or {"query", "objective"} & value.keys():
            raise ContractError("invalid_input", t("Page mode requires only an exact URL"))
        return await service.search.read_page(value["url"])
    if "query" not in value or "url" in value:
        raise ContractError("invalid_input", t("Search mode requires query and optional objective"))
    model_id, usage = None, None
    turn = service.calling_turn(call)
    if turn is not None:
        usage = service.usage_context(turn)
        model_id = service.execution_conversation(turn)["modelId"]
        if service.catalogue.resolve(model_id)["searchMode"] == "provider":
            return await service.providers.provider_search(model_id, value["query"], value.get("objective", ""),
                                                           usage_context=usage)
    return await service.search.search(value["query"], value.get("objective", ""), model_id=model_id,
                                       usage_context=usage)


# ── the panel's and the settings pages' own requests ───────────────────────────────────────────────────────────────

async def conversation(operation: str, args: dict, workspace: str | None):
    if workspace is None:
        raise ContractError("invalid_target", t("Open a workspace before using a conversation"))
    if operation in {"delete", "update", "fork"}:
        if service.store.conversation(args["conversationId"], workspace)["kind"] != "chat":
            raise ContractError("invalid_target", t("Manage bot and group threads through their bot or group"))
    if operation == "create":
        if args.get("modelId") is not None:
            service.catalogue.model(args["modelId"])
        return service.store.create(workspace, title=args.get("title", "New conversation"), model=args.get("modelId"))
    if operation == "list":
        return service.store.list(workspace, args.get("offset", 0))
    if operation == "search":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ContractError("invalid_input", t("Enter text to search chat messages"))
        return [row[0] for row in service.store.db.execute(
            "SELECT DISTINCT c.id FROM conversations c JOIN cards m ON m.conversation=c.id WHERE c.workspace=? "
            "AND coalesce(json_extract(c.value,'$.kind'),'chat')='chat' AND json_extract(m.value,'$.type')='answer' "
            "AND instr(lower(json_extract(m.value,'$.text')),lower(?))>0", (workspace, query))]
    if operation == "composer":
        view = await service.current_view(workspace)
        opened = await service.ctx.call("platform.workspace.list_open", {})
        name = next((w["name"] for w in opened if w["workspaceId"] == workspace), None)
        return {"name": name, "view": view["summary"] if view else None}
    if operation == "find_files":
        return await service.files.choices(workspace, args.get("query", ""))
    if operation == "attach_paths":
        service.store.conversation(args["conversationId"], workspace)
        return [(await service.files.attach(workspace, args["conversationId"], path))["id"] for path in args["paths"]]
    if operation == "open_file":
        service.store.conversation(args["conversationId"], workspace)
        return await service.files.open(workspace, args["path"], args["conversationId"])
    if operation == "export":
        return await service.export(args["conversationId"], workspace)
    if operation == "fork":
        return await service.history.fork(args["conversationId"], workspace, args["itemId"])
    if operation == "rewrite":
        return await service.history.rewrite(args["conversationId"], workspace, args["itemId"], args["text"], args["undoChanges"])
    if operation in {"retry", "plan_decision", "memory_decision"}:
        return await service.card_decision(operation, args, workspace)
    if operation == "queue":
        return service.queue(args["conversationId"], workspace, args)
    if operation == "revert":
        return await service.changes.revert(workspace, args["conversationId"], args["turnId"], args["direction"])
    if operation == "read":
        return service.store.read(args["conversationId"], workspace, before=args.get("before"))
    if operation == "restore_selection":
        return service.store.panel_conversation(workspace, args["conversationId"])
    if operation == "update":
        return service.update_conversation(args["conversationId"], workspace, args["changes"])
    if operation == "presence":
        service.store.conversation(args["conversationId"], workspace)
        view = (workspace, args.get("panelId", args["conversationId"]))
        if args.get("active"):
            service.focused_views.add(view)
        else:
            service.focused_views.discard(view)
        if args["visible"]:
            service.store.seen(args["conversationId"])
        return None
    if operation == "send":
        return await service.send(args["conversationId"], workspace, args["text"],
                                  include_view=args.get("includeView", True), attachments=args.get("attachments", []),
                                  supplemental=args.get("supplemental", True))
    if operation == "stop":
        return await service.stop(args["conversationId"], workspace)
    if operation == "child_read":
        return service.children.details(args["childRunId"], workspace, args["conversationId"], args.get("after", 0))
    if operation == "child_stop":
        return await service.children.stop(args["childRunId"], workspace, args["conversationId"])
    if operation == "decide":
        return await service.decide(args["approvalId"], workspace, args["version"], args["decision"], args.get("reason", ""))
    if operation == "permission_rules":
        service.store.conversation(args["conversationId"], workspace)
        return service.permissions.list(args["conversationId"])
    if operation == "revoke_permission":
        service.store.conversation(args["conversationId"], workspace)
        return service.permissions.revoke(args["conversationId"], args["ruleId"])
    if operation == "delete":
        await service.delete(args["conversationId"], workspace)
        return None
    if operation == "events":
        return service.store.events(args["conversationId"], workspace, args.get("after", 0))
    if operation == "upload_start":
        return service.upload_start(args["conversationId"], workspace, args["name"], args["mime"], args["size"])
    if operation == "upload_chunk":
        return service.upload_chunk(args["conversationId"], workspace, args["attachmentId"], args["offset"], args["data"])
    if operation == "upload_finish":
        return service.upload_finish(args["conversationId"], workspace, args["attachmentId"])
    if operation == "remove_attachment":
        service.remove_attachment(args["conversationId"], workspace, args["attachmentId"])
        return None
    if operation == "reference":
        return service.references.read(args["referenceId"], workspace)
    raise ContractError("invalid_input", t("Unknown conversation operation"))


async def settings(operation: str, args: dict, workspace: str | None):
    ctx = service.ctx
    if operation in {"page", "set_defaults", "instructions", "folders", "bind_folder", "folder_access", "unbind_folder", "open_folder", "spending"}:
        from .settings_pages import page_request
        return await page_request(service, operation, args, workspace)
    if operation == "memory_list":
        # A bot's settings page lists that bot's own; the Personalization page the rest.
        return service.memory.list(workspace, bot=args.get("botId"), status=args.get("status"), offset=args.get("offset", 0))
    if operation == "memory_edit":
        return service.memory.edit(workspace, args["memoryId"], args["expectedRevision"], args["body"],
                                        delete=args.get("delete", False))
    if operation == "read":
        connections = service.catalogue.list_connections()
        for connection in connections:
            connection["credential"] = await service.credential(connection["secretRef"])
        search = service.search.settings()
        search["credential"] = await service.credential(search["secretRef"])
        skill_folder = Path(await ctx.platform.workspace_folder(workspace)) / "skills" if workspace else None
        skills = await asyncio.to_thread(service.skills.snapshot, skill_folder)
        return {"connections": connections, "models": service.catalogue.list_models(), "search": search,
                "skills": skills,
                "defaults": service.store.defaults(workspace), "usage": service.usage.settings()}
    if operation == "put_connection":
        connection = service.catalogue.put_connection(args)
        service.providers.connection_added()
        return connection
    if operation == "put_model":
        return service.catalogue.put_model(args)
    if operation == "delete_connection":
        service.catalogue.delete_connection(args["id"])
        return None
    if operation == "delete_model":
        service.catalogue.delete_model(args["id"])
        service.store.forget_default_model(args["id"])
        return None
    if operation in {"credential", "delete_credential"}:
        reference = args["reference"]
        old = await ctx.platform.secret(reference)
        new = args["value"] if operation == "credential" else None
        if old != new:
            await ctx.platform.set_secret(reference, new)
            for connection in service.catalogue.list_connections():
                if connection["secretRef"] == reference:
                    service.catalogue.credentials_changed(connection["id"])
        return await service.credential(reference) if operation == "credential" else None
    if operation == "profiles":
        await service.providers.first_info()
        return service.catalogue.profiles(args["provider"], offset=args.get("offset", 0))
    if operation == "discover_models":
        return await service.providers.discover(args["kind"], base_url=args.get("baseUrl"))
    if operation == "search":
        return service.search.configure(provider=args["provider"], secret_ref=args.get("secretRef"), mode=args["mode"],
                                        connection_id=args.get("connectionId"), prices=args.get("prices"))
    if operation == "usage":
        return service.usage.configure(timezone_name=args["timezone"], monthly_limit=args["monthlyLimit"],
                                       currency=args.get("currency", "USD"))
    if operation == "read_usage":
        group_by = args.get("groupBy", "day")
        result = await asyncio.to_thread(service.usage.summary, group_by=group_by, month=args.get("month"), group_id=args.get("groupId"))
        if group_by == "owner":
            for group in result["groups"]:
                row = service.store.db.execute("SELECT json_extract(value,'$.name'),'Bot' FROM bot_entries WHERE id=? "
                    "UNION ALL SELECT json_extract(value,'$.title'),'Chat' FROM conversations WHERE id=?", (group["key"],group["key"])).fetchone()
                group["label"],group["kind"] = tuple(row) if row else (t("Removed or unassigned"), "")
        elif group_by == "workspace":
            opened = await service.ctx.call("platform.workspace.list_open", {})
            names = {w["workspaceId"]:w["name"] for w in opened}
            for group in result["groups"]:
                group["label"] = names.get(group["key"], group["key"] or t("Unassigned"))
        return result
    if operation == "test_connection":
        return await service.providers.test_connection(args["modelId"], thinking=args.get("thinking"),
                                                       test_search=args.get("testSearch", True))
    if operation == "refresh_profiles":
        return await service.providers.refresh_profiles()
    raise ContractError("invalid_input", t("Unknown Agent settings operation"))


async def bots(operation, args, workspace):
    from .bots.api import dispatch as bot_request
    value = await bot_request(service.bots, workspace, operation, args)
    if operation not in {"list","read","library","schedules","presence"}:
        service.bots.changed(workspace)
    return value


async def change_settings(operation, args, workspace):
    value = await settings(operation, args, workspace)
    if operation in SETTINGS_CHANGES:
        process = service.ctx.process
        process.send({"t": "event", "cap": "agent.settings_changed"},
                     process.codec(process.module.events["settings_changed"].payload).check({}))
    return value


def effects(*groups):
    """Declare each operation's effects and target for the platform and the chat's approval card."""
    return {"path": "operation.op", "branches": {
        op: {"effects": risks.split(), **({"target": "operation." + target} if target else {})}
        for risks, target, names in groups for op in names.split()
    }}


async def public_result(value):
    """Keep the observed result intact even when a live conversation changes after this call."""
    from .result_files import publish_result
    from .store import encode
    raw = encode(value)
    if len(raw) < 18000:
        return {"result": value}
    return {"file": await asyncio.to_thread(publish_result, service.root, raw)}


@module.action(effects=effects(
    ("read", None, "list search find_files reference"),
    ("write", None, "create"),
    ("read", "conversationId", "read permission_rules"),
    ("write", "conversationId", "export fork update attach_paths"),
    ("delete", "conversationId", "delete"),
    ("write delete", "turnId", "revert"),
    ("read", "childRunId", "child_read"),
    ("write", "childRunId", "child_stop"),
), examples=[{"operation": {"op": "list"}}])
async def chat(input: ChatInput, call: Call) -> OperationResult | ResultFile:
    """List, search, read, create, fork, update, export or delete this workspace's chats. Inspect file candidates,
    references, permission rules and child runs. Revert a turn's changes with conversationId, turnId and direction.
    Export writes Markdown in the workspace. Oversized results return a JSON file reference."""
    op = input.operation
    value = await conversation(op.op, op.model_dump(exclude={"op"}, exclude_unset=True), call.workspace_id)
    return await public_result(value)


@module.action(effects=effects(
    ("read", None, "list library schedules sources"),
    ("read", "id", "read inspect"),
    ("write", "id", "stop patch recipients upload toggle_routine"),
    ("delete", "id", "delete delete_routine"),
    ("write delete", "id", "revert_run"),
    ("write code external", "id", "test_routine send retry"),
    ("write", None, "create create_group"),
    ("write delete", None, "section"),
    ("write", "conversationId", "save_routine"),
), examples=[{"operation": {"op": "list"}}])
async def bot(input: BotInput, call: Call) -> OperationResult | ResultFile:
    """Manage workspace bots, groups, routines and runs. List for panel settings and spend, inspect by id for
    a bot, group, event source or routine. Read a thread by id. Patch with changes (model, duty, knowledge, skills,
    permission, limit, section). To send files, use recipients for the conversation IDs, agent.chat operation attach_paths
    for attachment IDs, then send with attachments keyed by bot ID. Upload adds base64 data to knowledge.
    Oversized results return a JSON file reference. Save routines with botId or conversationId, name, instruction,
    when, and either zone + rrule or source + params."""
    op = input.operation
    ws = call.workspace_id
    if ws is None:
        raise ContractError("invalid_target", t("Open a workspace to use Bots"))
    if op.op in {"sources", "inspect"}:
        value = service.bots.directory(ws, id=op.id) if op.op == "inspect" else service.bots.sources
    else:
        value = await bots(op.op, op.model_dump(exclude={"op"}, exclude_unset=True), ws)
    return await public_result(value)


@module.action(effects=effects(
    ("read", None, "read folders spending page read_instructions read_usage memory_list"),
    ("write", None, "set_defaults write_instructions search usage"),
    ("read external", None, "profiles discover_models"),
    ("write external", None, "refresh_profiles test_connection"),
    ("write", "id", "put_connection put_model"),
    ("delete", "id", "delete_connection delete_model"),
    ("write", "memoryId", "memory_edit"),
    ("delete", "memoryId", "memory_delete"),
), examples=[{"operation": {"op": "read"}}])
async def configure(input: SettingsInput, call: Call) -> OperationResult | ResultFile:
    """Read or change Agent connections, models, search, defaults, instructions, memories and budgets.
    read lists configured models and connections. profiles lists provider models, discover_models finds local models.
    scope selects global or workspace instructions and defaults. Memory edits require expectedRevision.
    Enter credentials in Settings. Oversized results return a JSON file reference."""
    op = input.operation
    name = op.op
    args = op.model_dump(exclude={"op"}, exclude_unset=True)
    if name in {"read_instructions", "write_instructions", "set_defaults", "page"}:
        args["scope"] = op.scope
    if name in {"read_instructions", "write_instructions"}:
        name = "instructions"
    elif name in {"memory_delete", "memory_edit"}:
        if name == "memory_delete":
            args.update(body="", delete=True)
        value = service.memory.edit(call.workspace_id, args["memoryId"], args["expectedRevision"], args["body"],
                                    delete=name == "memory_delete", source="inference")
        return {"result": value}
    elif name == "put_connection":
        # A new endpoint receives a fresh credential slot. A caller cannot redirect an existing key.
        previous = next((c for c in service.catalogue.list_connections() if c["id"] == op.id), None)
        same_endpoint = previous and all(previous[k] == getattr(op, k) for k in ("baseUrl", "providerId", "protocol", "kind", "site"))
        if op.kind != "local":
            args["secretRef"] = previous["secretRef"] if same_endpoint else "model." + uuid4().hex
    elif name == "search":
        args["secretRef"] = "search.tavily" if op.provider == "tavily" else None
    value = await change_settings(name, args, call.workspace_id)
    if op.op == "write_instructions":
        return {"result": {"scope": op.scope, "saved": True}}
    if name == "read":
        for account in [*value["connections"], value["search"]]:
            account["credential"] = {"configured": account["credential"]["configured"]}
    return await public_result(value)


@module.private_routes
def routes(ctx: Context):
    """POST /conversation and /settings with {operation, value}: the chat panel's and the settings pages' requests,
    for the workspace the Host names (the user's, in a window)."""
    from fastapi import FastAPI, Request, WebSocket
    from fastapi.responses import Response

    from .store import encode

    app = FastAPI()

    @app.websocket("/bot-events")
    async def bot_events(socket: WebSocket):
        from starlette.websockets import WebSocketDisconnect
        workspace=socket.headers.get("x-talyx-workspace")
        await socket.accept()
        changes=service.bots.changes(workspace)
        gone=asyncio.create_task(socket.receive())
        try:
            while True:
                update=asyncio.create_task(anext(changes))
                done,_=await asyncio.wait((gone,update),return_when=asyncio.FIRST_COMPLETED)
                if gone in done:
                    update.cancel()
                    await asyncio.gather(update,return_exceptions=True)
                    break
                await socket.send_json(update.result())
        except WebSocketDisconnect:
            return
        finally:
            gone.cancel()
            await asyncio.gather(gone,return_exceptions=True)
            await changes.aclose()

    handlers = {"conversation": conversation, "settings": settings, "bots": bots}
    statuses = {"invalid_input": 400, "permission_denied": 403, "target_gone": 404, "not_found": 404, "conflict": 409}

    @app.post("/{name}")
    async def handle(name: str, request: Request):
        try:
            if name not in handlers:
                raise ContractError("not_found", t("Agent has no route {name}", name=name))
            body = await request.json()
            workspace = request.headers.get("x-talyx-workspace") or None
            handler = change_settings if name == "settings" else handlers[name]
            value = await handler(body["operation"], body.get("value") or {}, workspace)
            return Response(encode(value), media_type="application/json")
        except ContractError as error:
            return Response(encode({"error": error.to_dict()}), media_type="application/json",
                            status_code=statuses.get(error.code, 500))
    return app
