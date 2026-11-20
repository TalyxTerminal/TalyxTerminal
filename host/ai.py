"""AI's way in: one tool catalog for AI clients inside TALYX and external ones (MCP), made from the contracts.

The catalog is every public action of every enabled module and of the platform, each with its effects. The
platform runs what AI calls and records the call; it refuses nothing by effect. Whether to ask the user first is
the AI client's own decision (its permission tier), which the effects let it make. One answer is at most
``ANSWER_LIMIT`` characters: a longer one is an error, never cut.
"""

from __future__ import annotations

import asyncio
import base64
import json
from ipaddress import ip_address
from pathlib import Path

from talyx_sdk.errors import ContractError

ANSWER_LIMIT = 20_000


def summary_paths(schema, definitions, seen=frozenset()):
    """Locate vocabulary Summary values without putting whole output schemas in the model's tool list."""
    if schema.get("x-talyx-vocabulary") == "https://talyx.local/vocab/v2#/$defs/Summary":
        yield ()
        return
    ref = schema.get("$ref")
    if ref and ref.startswith("#/$defs/") and ref not in seen:
        yield from summary_paths(definitions[ref.removeprefix("#/$defs/")], definitions, seen | {ref})
    for kind in ("allOf", "anyOf", "oneOf"):
        for option in schema.get(kind, []):
            yield from summary_paths(option, definitions, seen)
    for key, child in schema.get("properties", {}).items():
        for path in summary_paths(child, definitions, seen):
            yield (key, *path)
    if isinstance(schema.get("items"), dict):
        for path in summary_paths(schema["items"], definitions, seen):
            yield ("*", *path)


def output_fields(schema, definitions):
    """Summarise fields and units to two levels, including fields present in just one union branch."""
    found = {}

    def visit(node, path, depth, seen):
        ref = node.get("$ref")
        if ref and ref not in seen:
            target = definitions[ref.rsplit("/", 1)[1]]
            visit({**target, **{k: v for k,v in node.items() if k != "$ref"}}, path, depth, seen | {ref})
            return
        for key in ("anyOf", "oneOf", "allOf"):
            for branch in node.get(key, []):
                visit(branch, path, depth, seen)
        kind = node.get("type")
        if path and kind:
            item = found.setdefault(path, {"kinds": [], "unit": None})
            for t in kind if isinstance(kind, list) else [kind]:
                if t not in item["kinds"]:
                    item["kinds"].append(t)
            if node.get("x-unit"):
                item["unit"] = node["x-unit"]
        if depth < 2:
            for name, child in node.get("properties", {}).items():
                visit(child, f"{path}.{name}" if path else name, depth + 1, seen)
            if isinstance(node.get("items"), dict):
                visit(node["items"], path + "[]", depth, seen)

    visit(schema, "", 0, set())
    return [{"name": key, "type": "|".join(value["kinds"]), "unit": value["unit"]} for key, value in found.items()]


def tool_name(capability: str) -> str:
    return capability.replace(".", "_")


def capture_content(file: dict, data_dir: Path) -> str:
    folder = (data_dir / "platform" / "captures").resolve()
    path = Path(file["path"]).resolve()
    if path.parent != folder or path.suffix != ".png":
        raise ContractError("invalid_output", "The window returned an invalid capture reference")
    return base64.b64encode(path.read_bytes()).decode("ascii")


def annotations(effects: list[str]) -> dict:
    """Effects as MCP's standard tool annotations."""
    return {"readOnlyHint": effects == ["read"], "destructiveHint": "delete" in effects or "funds" in effects,
            "idempotentHint": effects == ["read"], "openWorldHint": "external" in effects or "funds" in effects}


def tool_schema(schema: dict, definitions: dict) -> dict:
    """An action's input as a tool takes it: the object itself at the top (MCP wants ``type: object`` there) and only
    the shared definitions it refers to. With all of its module's definitions repeated in every tool, the catalog was
    19 MB (one module alone 14 MB): more than a frame carries, and far more than any client reads."""
    body = dict(schema)
    while "$ref" in body:
        reference = body.pop("$ref")
        body = {**definitions[reference.removeprefix("#/$defs/")], **body}
    # A union of input models: each branch is an object, and so is the input.
    body.setdefault("type", "object")
    used, stack = set(), [body]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            reference = item.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/") and reference[8:] not in used:
                used.add(reference[8:])
                stack.append(definitions[reference[8:]])
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return {**body, "$defs": {name: definitions[name] for name in sorted(used)}} if used else body


class AI:
    def __init__(self, host):
        self.host = host
        self._catalog: tuple[int, dict] | None = None

    def _entries(self) -> dict:
        """Index the declarations once per catalog change for internal clients and MCP."""
        version = self.host.catalog_version
        if self._catalog is None or self._catalog[0] != version:
            tools, modules, hidden = [], [], set()
            for owner, contract in self.host.contracts():
                start = len(tools)
                for name, spec in contract["actions"].items():
                    full_name = f"{owner}.{name}"
                    if "callers" in spec and "ai" not in spec["callers"]:
                        hidden.update((full_name, tool_name(full_name)))
                        continue
                    schema = tool_schema(spec["input"], contract["$defs"])
                    description = spec["description"]
                    if full_name == "platform.panels.open":
                        panels = [f'{panel["title"]}: {json.dumps({"type": f"{item.id}.{key}"}, ensure_ascii=False)}'
                                  f' — {panel.get("description") or item.manifest["name"]}'
                                  for item in self.host.catalog.modules.values() if item.enabled
                                  for key, panel in item.manifest.get("panels", {}).items()
                                  if panel.get("visibility") != "private"]
                        description += "\n" + "\n".join(panels)
                    tools.append({"name": full_name, "description": description,
                                  "effects": spec["effects"], "task": spec["task"], "input": schema,
                                  "examples": spec.get("examples", []), "errors": spec.get("errors", []),
                                  "effectBranches": spec.get("effectBranches"), "target": spec.get("target"),
                                  "summaryPaths": [list(path) for path in sorted(set(summary_paths(spec["output"], contract["$defs"])))]})
                item = self.host.catalog.modules.get(owner)
                guide = item.guide if item else None
                modules.append({"module": owner, "name": item.manifest["name"] if item else "TALYX",
                                "description": item.manifest.get("description", "") if item else "Workspace and platform services.",
                                "actions": len(tools) - start,
                                "guide": {key: guide[key] for key in ("name", "description")} if guide else None,
                                "docs": [{"topic": topic, "title": doc["title"]}
                                         for topic, doc in (item.docs if item else {}).items()]})
            names = [tool_name(tool["name"]) for tool in tools]
            if len(names) != len(set(names)):
                raise ContractError("internal", "Two actions map to the same AI tool name")
            self._catalog = (version, {"tools": tools, "modules": modules, "hidden": hidden})
        return self._catalog[1]

    def catalog(self, module: str | None = None, cursor: int = 0) -> dict:
        entries = self._entries()
        if module is None:
            key, items = "modules", entries["modules"]
        else:
            key = "tools"
            items = [tool for tool in entries[key] if tool["name"].partition(".")[0] == module]
        page = {"version": self.host.catalog_version, key: [], "nextCursor": None}
        size = len(json.dumps(page, ensure_ascii=False)) + 32
        for index in range(cursor, len(items)):
            length = len(json.dumps(items[index], ensure_ascii=False, separators=(",", ":"))) + 1
            if size + length > ANSWER_LIMIT:
                if not page[key]:
                    raise ContractError("too_large", "A single declaration exceeds the AI catalog page size")
                page["nextCursor"] = index
                break
            page[key].append(items[index])
            size += length
        return page

    def detail(self, action: str, module: str | None = None) -> dict:
        tool = self.find(action if not module or action.startswith(module + ".") else f"{module}.{action}")
        owner, name = tool["name"].split(".", 1)
        contract = dict(self.host.contracts())[owner]
        return {**tool, "outputFields": output_fields(contract["actions"][name]["output"], contract["$defs"])}

    def guide(self, module: str, topic: str | None, cursor: int) -> dict:
        """The module's guide, or with ``topic`` one of its authoring docs."""
        item = self.host.catalog.get(module)
        if not item.enabled or not item.guide:
            raise ContractError("unavailable", f"{module} has no enabled AI guide; read the module catalog")
        if topic is None:
            text = item.guide["text"]
        elif topic in item.docs:
            text = item.docs[topic]["text"]
        else:
            raise ContractError("not_found", f"{module} has no doc {topic}; its docs are "
                                             f"{', '.join(item.docs) or 'none'}")
        stop = min(len(text), cursor + ANSWER_LIMIT - 256)
        while len(json.dumps(text[cursor:stop], ensure_ascii=False)) > ANSWER_LIMIT - 256:
            stop = cursor + (stop - cursor) // 2
        return {"text": text[cursor:stop], "nextCursor": stop if stop < len(text) else None}

    def find(self, name: str) -> dict:
        entries = self._entries()
        if name in entries["hidden"]:
            raise ContractError("permission_denied", f"{name} does not accept AI calls")
        for tool in entries["tools"]:
            if tool["name"] == name or tool_name(tool["name"]) == name:
                return tool
        raise ContractError("unknown_capability", f"No action {name}; read the catalog again")

    async def call(self, caller: dict, value: dict) -> object:
        """An AI client inside TALYX calls an action as AI in one workspace."""
        if caller["origin"] != "module":
            raise ContractError("permission_denied", "ai.call is for an AI client module")
        ai_caller = {"origin": "ai", "module": caller["module"], "workspaceId": value["workspaceId"],
                     "ai": {"client": value["client"], "conversation": value.get("conversation")}}
        call_id = self.host.router.next_id()
        try:
            result = await self.invoke(ai_caller, value["action"], value["input"], call_id=call_id)
        except ContractError as error:
            error.call_id = call_id
            raise
        return {"result": result, "callId": call_id}

    async def invoke(self, caller: dict, action: str, value, *, call_id: str | None = None) -> object:
        tool = self.find(action)
        self.host.workspaces.require(caller["workspaceId"])
        body = await self.host.internal.call_raw(caller, tool["name"], value, host_id=call_id)
        size = len(body.decode("utf-8"))
        if size > ANSWER_LIMIT:
            message = f"The answer has {size} characters; AI reads at most {ANSWER_LIMIT} at a time."
            if tool["effects"] != ["read"]:
                raise ContractError("too_large", f"The action executed, but its answer was too large to deliver. Read the result before retrying. {message}",
                                    {"limit": ANSWER_LIMIT}, execution="unknown", effects="may_have_occurred")
            raise ContractError("too_large", f"{message} Ask for a smaller page.", {"limit": ANSWER_LIMIT}, execution="failed")
        return json.loads(body)


class MCP:
    """Streamable HTTP MCP at ``/mcp/workspaces/<workspaceId>``, bound to one open workspace."""

    def __init__(self, ai: AI):
        from mcp import types
        from mcp.server.lowlevel import Server
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        self.ai, self.types = ai, types
        self.server = Server("TALYX", version="0.5.0",
                             instructions="You are connected to one TALYX workspace. Tools are its modules' and the "
                                          "platform's public actions; each says its effects.")
        self.manager = StreamableHTTPSessionManager(self.server, json_response=True)
        self.clients: dict[str, dict] = {}
        self._register()

    def _caller(self) -> dict:
        request = self.server.request_context.request
        return request.scope["talyx.caller"]

    def _register(self):
        types = self.types

        @self.server.list_tools()
        async def list_tools():
            self._caller()
            return [types.Tool(name=tool_name(tool["name"]), description=tool["description"],
                               inputSchema=tool["input"], annotations=types.ToolAnnotations(**annotations(
                                   tool["effects"])), _meta={"talyx/effects": tool["effects"]})
                    for tool in self.ai._entries()["tools"]]

        @self.server.call_tool(validate_input=False)
        async def call_tool(name, arguments):
            caller = self._caller()
            try:
                value = await self.ai.invoke(caller, name, arguments)
                if name == tool_name("platform.panels.capture"):
                    data = await asyncio.to_thread(capture_content, value, self.ai.host.config.data_dir)
                    return types.CallToolResult(content=[types.ImageContent(type="image", mimeType="image/png", data=data)])
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(
                    value, ensure_ascii=False, separators=(",", ":")))], isError=False)
            except ContractError as error:
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(
                    {"error": error.to_dict()}, ensure_ascii=False))], isError=True)

    async def __call__(self, scope, receive, send):
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        request = Request(scope, receive)
        if not ip_address(request.client.host).is_loopback:
            error = ContractError("permission_denied", "MCP only accepts this machine")
            await JSONResponse({"error": error.to_dict()}, status_code=403)(scope, receive, send)
            return
        workspace = scope["path_params"]["workspace_id"]
        if not self.ai.host.workspaces.is_open(workspace):
            error = ContractError("target_gone", f"Workspace {workspace} is not open")
            await JSONResponse({"error": error.to_dict()}, status_code=404)(scope, receive, send)
            return
        # A client is known by its MCP session for as long as the session lasts; a request outside one is its own.
        session = request.headers.get("mcp-session-id")
        client = {"name": request.headers.get("user-agent", "MCP client")[:80]}
        if session:
            client = self.clients.setdefault(session, client)
        caller = {"origin": "ai", "module": None, "workspaceId": workspace,
                  "ai": {"client": client["name"], "conversation": session}}
        try:
            await self.manager.handle_request({**scope, "talyx.caller": caller}, receive, send)
        finally:
            if session and request.method == "DELETE":
                self.clients.pop(session, None)
