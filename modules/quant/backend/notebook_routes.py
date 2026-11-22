"""A notebook's private routes (file, kernel socket, packages) and the actions code and AI may call.

Socket messages, panel to Host (one JSON object per text frame):
  {"op": "execute", "cell": id, "code": text}   run a cell
  {"op": "interrupt"}  {"op": "restart"}
  {"op": "complete", "id": n, "code": text, "cursor": n}  {"op": "inspect", "id": n, "code": text, "cursor": n}
Host to panel:
  {"type": "state", sessionId, state, message, busy, running: [{cell, msgId, startedAt, preview}], panels, environment}
  {"type": "accepted", cell, msgId}  {"type": "input", cell, msgId, executionCount}
  {"type": "output", cell, msgId, output, displayId?}  {"type": "output_update", cell, msgId, displayId, output}  {"type": "clear_output", cell, msgId, wait}
  {"type": "reply", cell, msgId, status: ok|error|aborted|interrupted, executionCount}
  {"type": "complete_reply" | "inspect_reply", id, ...the kernel's reply}
  {"type": "input_request", cellId, prompt, password}  {"type": "input_done", cellId, text}   (text empty when the box is taken away)
  {"type": "error", op, cell?, id?, code, message}
  {"type": "comm", msgType: comm_open|comm_msg|comm_close, commId, data, target?}   a widget model's message from the kernel
Panel to Host also: {"op": "input_reply", "cell": id, "value": text}  {"op": "comm_msg", "commId": id, "data": {...}}
"""
from __future__ import annotations

import asyncio
import json
import keyword
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .kernels import Kernels
from .notebook_activation import CellActivation, identity
from .notebook_extract import KINDS, preview
from .notebook_file import NotebookFiles, new_cell, serialize
from .run_materials import RunMaterials, day_of, milliseconds_of

RUN_WAIT_SECONDS = 120
OUTPUT_TEXT = 4000
# A shell escape or the pip magic would install into the kernel's environment behind TALYX's back.
PIP = re.compile(r"^\s*(%{1,2}pip\b|%{1,2}conda\b|!\s*(python\S*\s+-m\s+)?(pip|uv|conda)\b)", re.MULTILINE)
PIP_MESSAGE = "Install packages from the Packages page (⋯ → Packages)"


def blocked_install(code):
    return PIP_MESSAGE if PIP.search(code) else None


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Extract(Input):
    cells: list[str]
    kind: str
    name: str | None = None


class Environment(Input):
    environment: str


class LibraryNotebook(Input):
    name: str
    version: int
    kind: str


def python_name(text):
    """The variable a library item's name makes: letters, digits and underscores, not starting with a digit."""
    name = re.sub(r"\W+", "_", text.strip().lower()).strip("_") or "item"
    if not name.isidentifier():
        name = "_" + "".join(char if ("_" + char).isidentifier() else "_" for char in name)
    return name + "_" if keyword.iskeyword(name) else name


class NotebookSave(Input):
    notebook: dict
    expectedVersion: int | str


class NotebookServices:
    def __init__(self, ctx, records, store, common, packages):
        self.ctx, self.store, self.packages = ctx, store, packages
        self.kernels = Kernels(ctx, records, common, self.packages)
        self.files = NotebookFiles(ctx, store)
        self.activation = CellActivation(ctx)

    def notebook(self, object_id):
        record = self.store.read(object_id)
        if record["kind"] != "notebook":
            raise ContractError("invalid_input", t("This Quant object is not a notebook."))
        return record

    def activities(self, workspace_id):
        """What a workspace lists for itself (and closes with): its notebook kernels and a running package installation."""
        return [*self.kernels.activities(workspace_id), *self.packages.activity(workspace_id)]

    async def stop_activity(self, workspace_id, activity_id, reason):
        if activity_id == "packages":
            await self.packages.cancel()
        else:
            await self.kernels.stop_activity(workspace_id, activity_id, reason)

    # a one-line backtest from a cell: a run of that strategy, tagged Notebook, nothing about the strategy changed
    async def start_backtest(self, args):
        strategy = RunMaterials(self.store, self.runs).objects_named(args["strategy"], "strategy", by_run=False)
        override = None
        if args.get("signal"):
            saved = await self.runs.saved_source(strategy)
            library_params = [item["name"] for item in self.store.parameters(saved)["parameters"] if item["type"] == "library"]
            wanted = args["signal"].get("param")
            if wanted is None:
                if len(library_params) != 1:
                    raise ContractError("invalid_input", t("{name} reads {library_params} library items ({library_params2}); pass signal=\"{value}\" to say which one data replaces.", name=strategy['name'], library_params=len(library_params), library_params2=', '.join(library_params), value=library_params[0] if library_params else 'signal'),
                                        {"parameters": library_params})
                wanted = library_params[0]
            elif wanted not in library_params:
                raise ContractError("invalid_input", t("{name} has no library input named {wanted}.", name=strategy['name'], wanted=wanted), {"parameters": library_params})
            override = {"param": wanted, "path": args["signal"]["path"]}
        started = await self.runs.start(strategy["id"], {**({"params": args["params"]} if args.get("params") else {}), "tags": ["notebook"], **({"signalOverride": override} if override else {})})
        return {"runId": started["id"], "number": started.get("number")}

    # a run, opened here with the first cell loaded
    def read_materials(self, args, brief=False):
        return RunMaterials(self.store, self.runs, self.runs.records.root / "factor-data" if getattr(getattr(self.runs, "records", None), "root", None) else None).read(args, brief=brief)

    async def open_run(self, run_id):
        """The notebook for a run: the one made for it before, or a new one whose first cell reads that run (not the latest one)."""
        record = self.runs.read(run_id)
        previous = record.get("notebookId")
        if previous:
            try:
                self.store.read(previous)
                return {"notebookId": previous, "created": False}
            except ContractError:
                pass
        number = RunMaterials(self.store, self.runs).number_of(record)
        name = f"{record['name']} #{number}"
        line = f"run = q.run({run_id!r})\nrun"
        first = new_cell("code", line)
        data = record.get("data") if isinstance(record.get("data"), dict) else {}
        shown = {"kind": record.get("objectKind"), "at": record.get("createdAt"), "dataFrom": milliseconds_of(data.get("dataFrom")), "dataTo": milliseconds_of(data.get("dataTo")), "heldOutFrom": milliseconds_of(data.get("heldOutFrom"))}
        made = await self.store.make("notebook", name, None, None, serialize({"cells": [first], "metadata": {}}),
                                     {"data": {}, "params": {}, "account": {}, "openedFrom": {"objectId": record["objectId"], "runId": run_id,
                                                                                               **{key: value for key, value in shown.items() if value is not None}}})
        self.runs.save(record, notebookId=made["id"])
        return {"notebookId": made["id"], "created": True}

    async def open_library_item(self, name, version, kind):
        """A new notebook whose first cell reads one library version (a model is loaded, anything else is read as values)."""
        if kind == "model":
            line = f"{python_name(name)} = q.model({name!r}, {version})\n{python_name(name)}"
        else:
            line = f"{python_name(name)} = q.library.read({name!r}, version={version})\n{python_name(name)}"
        made = await self.store.make("notebook", f"{name} v{version}", None, None, serialize({"cells": [new_cell("code", line)], "metadata": {}}),
                                     {"data": {}, "params": {}, "account": {}})
        return {"notebookId": made["id"], "created": True}

    # the kernel's environment
    def list_environments(self):
        return self.packages.kernel_environments()

    async def set_kernel(self, object_id, environment):
        """Store the notebook's environment and restart its kernel in it. A deep-learning environment that is not installed is D's to install first."""
        record = self.notebook(object_id)
        wanted = next((item for item in self.packages.kernel_environments() if item["id"] == environment), None)
        if wanted is None:
            raise ContractError("invalid_input", t("There is no kernel environment named {environment}.", environment=environment), {"environments": [item["id"] for item in self.packages.kernel_environments()]})
        if not wanted["installed"]:
            offer = self.packages.deep_status().get("offer") if getattr(self.packages, "deep_status", None) else None
            raise ContractError("deep_not_installed", t("The deep-learning environment is not installed."), {"offer": offer})
        session = self.kernels.find(object_id)
        if session is not None:
            session.check_restart()
        saved = self.store.save(record, settings={**record["settings"], "kernel": {"environment": environment}})
        if session is not None:
            session.record = saved
            await session.restart()
        return {"objectId": object_id, "environment": environment}

    # extracting cells into a new object
    async def extract_view(self, args):
        if args["kind"] not in KINDS:
            raise ContractError("invalid_input", t("Choose one of: {kinds}.", kinds=', '.join(KINDS)))
        cells = (await self.files.read(args["notebookId"]))["notebook"]["cells"]
        known = {cell["id"] for cell in cells}
        if not args["cells"] or any(item not in known for item in args["cells"]):
            raise ContractError("invalid_input", t("Select cells of this notebook to take."), {"cells": args["cells"]})
        settings = self.notebook(args["notebookId"])["settings"]
        held = settings["data"].get("heldOutFrom") or day_of(settings.get("openedFrom", {}).get("heldOutFrom"))
        shown = preview(args["cells"], cells, args["kind"], held)
        # A run read by its id is shown as the run band shows it: the object's name, the run's number and when it was made.
        for item in shown["dataSettings"]["runs"]:
            record = self.runs.records.find("qrun", item["object"])
            if record is not None:
                item.update(name=record["name"], number=RunMaterials(self.store, self.runs).number_of(record), at=record.get("createdAt"))
        return {**shown, "heldOutFrom": held}

    async def create_from_notebook(self, args):
        """A new object whose code is the chosen cells as written and whose data settings are what they read; always in code mode, since the cells are not the standard flow."""
        shown = await self.extract_view(args)
        found = shown["dataSettings"]
        data = {key: found[key] for key in ("since", "until", "timeframe") if found[key]}
        if found["symbols"]:
            symbols = found["symbols"] if isinstance(found["symbols"], list) else [found["symbols"]]
            data["symbols"] = [{"symbol": name} for name in symbols]
            data = await self.store.exact_symbols(data)
        if found["universe"]:
            data["universe"] = found["universe"]
        if found.get("index"):
            from .public_settings import store_data
            selection = await store_data(self.store.ctx, {"index": found["index"]}, {})
            data["index"] = selection["index"]
        if found.get("list"):
            name = found["list"]
            choice = next((row for row in self.store.lists.list()["items"] if row["name"].casefold() == name.casefold()), None)
            if choice is None:
                raise ContractError("not_found", t("There is no research list named {name}.", name=name))
            data["list"] = choice["id"]
        if found["libraryInputs"]:
            data["libraryInputs"] = found["libraryInputs"]
        if shown["heldOutFrom"] and not found["heldOutStudied"]:
            data["heldOutFrom"] = shown["heldOutFrom"]
        settings = {"data": data, "params": {}, "account": {}, "mode": "code", "fromNotebook": {"notebookId": args["notebookId"], "cells": args["cells"]}}
        if args["kind"] in {"factor", "model"}:
            settings["formula"] = ""
        made = await self.store.make(args["kind"], args["name"], None, None, shown["code"], settings)
        return {**made, "dataSettings": found, "notTaken": shown["notTaken"]}

    # the panel asks to run a cell
    async def user_execute(self, session, panel, op):
        code = op["code"]
        message = blocked_install(code)
        if message is not None:
            session.send(panel, {"type": "error", "op": "execute", "cell": op["cell"], "code": "pip_blocked", "message": message})
            return
        await session.execute(op["cell"], code, allow_stdin=True)

    async def write_cell(self, args):
        """Add, replace or delete one cell and save the notebook, the way the panel saves it."""
        read = await self.files.read(args["objectId"])
        cells = read["notebook"]["cells"]
        ids = [cell["id"] for cell in cells]
        for key in ("cellId", "after"):
            if key in args and args[key] not in ids:
                raise ContractError("not_found", t("This notebook has no cell {args}.", args=args[key]), {"cells": ids})
        if args["op"] == "add":
            cell = new_cell(args.get("type", "code"), args["source"])
            cells.insert(ids.index(args["after"]) + 1 if "after" in args else len(cells), cell)
        elif args["op"] == "replace":
            index = ids.index(args["cellId"])
            cell = cells[index] = {**cells[index], "source": args["source"], "outputs": [], "executionCount": None}
        else:
            cell = cells.pop(ids.index(args["cellId"]))
        saved = await self.files.write(args["objectId"], read["notebook"], read["version"])
        return {"objectId": args["objectId"], "cellId": cell["id"], "version": saved["version"],
                "cells": [{"cellId": item["id"], "type": item["type"]} for item in cells]}

    # an AI asks to run a cell
    async def ai_run_cell(self, args):
        """Run the saved cell in its workspace kernel, including when the notebook has no panel."""
        code = await self.files.cell_code(args["objectId"], args["cellId"])
        message = blocked_install(code)
        if message is not None:
            raise ContractError("permission_denied", message)
        await self.activation.approve_ai(args["objectId"], args["cellId"], code)
        session = self.kernels.ensure(self.notebook(args["objectId"]))
        waiter = await session.execute(args["cellId"], code, wait=True)
        async def completed():
            result = await waiter
            await self.files.save_execution(args["objectId"], args["cellId"], code, result)
            return result

        completion = self.ctx.start(completed(), "Run notebook cell")
        try:
            async with asyncio.timeout(args["timeoutSeconds"] if "timeoutSeconds" in args else RUN_WAIT_SECONDS):
                result = await asyncio.shield(completion)
        except TimeoutError as error:
            raise ContractError("unavailable", t("The cell is still running; use control_notebook_kernel to read its state or interrupt it."), {"cellId": args["cellId"]}) from error
        return {"objectId": args["objectId"], "cellId": args["cellId"], "codeIdentity": identity(code), "status": result["status"],
                "executionCount": result["executionCount"], "outputs": [text_of(output) for output in result["outputs"]]}


def text_of(output):
    """An output as text an AI can read: images and rich displays name their type instead of carrying their bytes."""
    kind = output["output_type"]
    if kind == "stream":
        return {"type": "stream", "name": output["name"], "text": output["text"][-OUTPUT_TEXT:]}
    if kind == "error":
        return {"type": "error", "name": output["ename"], "text": output["evalue"], "traceback": [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in output["traceback"]][-12:]}
    data = output["data"]
    if "text/plain" in data:
        return {"type": kind, "text": data["text/plain"][-OUTPUT_TEXT:], "mimeTypes": sorted(data)}
    return {"type": kind, "mimeTypes": sorted(data)}


def mount_notebooks(router: APIRouter, services: NotebookServices):
    files, kernels, packages = services.files, services.kernels, services.packages

    @router.get("/notebooks/{object_id}")
    async def read_notebook(object_id: str):
        return {**await files.read(object_id), "kernel": kernels.status(object_id)}

    @router.put("/notebooks/{object_id}")
    async def save_notebook(object_id: str, body: NotebookSave):
        return await files.write(object_id, body.notebook, body.expectedVersion)

    @router.post("/runs/{run_id}/notebook")
    async def open_run_notebook(run_id: str):
        return await services.open_run(run_id)

    @router.post("/library/notebook")
    async def open_library_notebook(body: LibraryNotebook):
        return await services.open_library_item(body.name, body.version, body.kind)

    @router.get("/kernel-environments")
    async def kernel_environments():
        return services.list_environments()

    @router.post("/notebooks/{object_id}/kernel/environment")
    async def kernel_environment(object_id: str, body: Environment):
        return await services.set_kernel(object_id, body.environment)

    @router.post("/notebooks/{object_id}/extract/preview")
    async def extract_preview(object_id: str, body: Extract):
        return await services.extract_view({"notebookId": object_id, "cells": body.cells, "kind": body.kind})

    @router.post("/notebooks/{object_id}/extract")
    async def extract_create(object_id: str, body: Extract):
        return await services.create_from_notebook({"notebookId": object_id, "cells": body.cells, "kind": body.kind, "name": body.name or "From notebook"})

    @router.get("/notebooks/{object_id}/kernel")
    async def kernel_status(object_id: str):
        services.notebook(object_id)
        return kernels.status(object_id)

    @router.post("/notebooks/{object_id}/kernel/stop")
    async def stop_kernel(object_id: str):
        services.notebook(object_id)
        session = kernels.find(object_id)
        if session is not None:
            await session.stop()
        return kernels.status(object_id)

    @router.websocket("/notebooks/{object_id}/kernel/socket")
    async def kernel_socket(socket: WebSocket, object_id: str, panel: str):
        await socket.accept()
        try:
            session = kernels.ensure(services.notebook(object_id))
        except ContractError as error:
            await socket.send_text(json.dumps({"type": "error", "op": "connect", "code": error.code, "message": error.message}))
            await socket.close(1008)
            return
        connection = session.attach(panel)
        order = asyncio.Lock()
        work: set[asyncio.Task] = set()

        async def send_out():
            while True:
                message = await connection.queue.get()
                if message is None:   # dropped for falling behind: closing makes the panel reconnect and read the state again
                    await socket.close(1013)
                    return
                await socket.send_text(json.dumps(message, ensure_ascii=False, allow_nan=False))

        async def handle(op):
            kind = op["op"]
            if kind == "execute":
                async with order:
                    await services.user_execute(session, panel, op)
            elif kind == "variable":
                session.ask_table(panel, op["name"])
            elif kind == "input_reply":
                session.input_reply(op["cell"], op["value"])
            elif kind == "comm_msg":
                session.comm_to_kernel(op["commId"], op["data"])
            elif kind == "interrupt":
                session.interrupt()
            elif kind == "restart":
                await session.restart()
            elif kind in {"complete", "inspect"}:
                await session.ask(panel, kind, op["id"], op["code"], op["cursor"])
            else:
                raise ContractError("invalid_input", t("Unknown operation {kind}.", kind=kind))

        async def run(op):
            try:
                await handle(op)
            except ContractError as error:
                session.send(panel, {"type": "error", "op": op["op"], **{key: op[key] for key in ("cell", "id") if key in op}, "code": error.code, "message": error.message})

        sender = asyncio.create_task(send_out())
        try:
            while True:
                try:
                    op = json.loads(await socket.receive_text())
                    if not isinstance(op["op"], str):
                        raise TypeError
                except (ValueError, KeyError, TypeError):
                    session.send(panel, {"type": "error", "op": "message", "code": "invalid_input", "message": t("A message is a JSON object with an op.")})
                    continue
                task = asyncio.create_task(run(op))
                work.add(task)
                task.add_done_callback(work.discard)
                if connection.dropped or sender.done():
                    break
        except WebSocketDisconnect:
            pass
        finally:
            for task in (sender, *work):
                task.cancel()
            await asyncio.gather(sender, *work, return_exceptions=True)
            if session.panels.get(panel) is connection:
                session.detach(panel)

    @router.get("/packages")
    async def read_packages():
        return packages.status()

    @router.post("/packages/install")
    async def install_packages():
        return packages.start_install()

    @router.post("/packages/cancel")
    async def cancel_packages():
        return await packages.cancel()


def handle_notebook_actions(ctx, services: NotebookServices):
    """Operations for AI and for the kernel's own code; the declarations name them in the contract."""

    async def run_cell(args, call):
        return await services.ai_run_cell(args)

    async def read_notebook_variables(args, call):
        session = services.kernels.session(args["notebookId"])
        if "name" in args:
            from .public_pages import complete_json
            name = args["name"]
            return await complete_json(ctx, {"name": name, "table": await session.variable_table(name)}, "variable.json")
        return {"variables": session.variables, "loaded": session.loaded}

    async def start_notebook_backtest(args, call):
        return await services.start_backtest(args)

    async def read_run_materials(args, call):
        return services.read_materials(args, brief=True)

    async def open_run_in_notebook(args, call):
        return await services.open_run(args["runId"])

    async def set_notebook_kernel(args, call):
        return await services.set_kernel(args["notebookId"], args["environment"])

    async def control_notebook_kernel(args, call):
        identity, op = args["objectId"], args["op"]
        record = services.notebook(identity)
        kernels = services.kernels
        session = kernels.find(identity)
        if op == "restart":
            if session is None:
                kernels.ensure(record)
            else:
                await session.restart()
        elif op == "stop" and session is not None:
            await session.stop()
        elif op == "interrupt":
            kernels.session(identity).interrupt()
        return kernels.status(identity)

    async def preview_notebook_extract(args, call):
        from .public_pages import complete_json
        return await complete_json(ctx, await services.extract_view(args), "extract.json")

    async def save_notebook(args, call):
        return await services.files.write(args["objectId"], args["notebook"], args["expectedVersion"])

    async def write_cell(args, call):
        return await services.write_cell(args)

    for handler in (control_notebook_kernel, preview_notebook_extract, save_notebook, run_cell, write_cell, read_notebook_variables, read_run_materials, start_notebook_backtest, open_run_in_notebook,
                    set_notebook_kernel):
        ctx.handlers[handler.__name__] = handler
