"""Notebook kernels: one ipykernel per workspace and notebook, shared by every panel that shows the notebook.

The kernel runs in the research environment as an ordinary process Quant starts (it talks to Quant over loopback). The Host speaks to it with jupyter_client's asynchronous client and passes what the kernel says
on to the panels. A kernel dies with the workspace, with the Host, or when its notebook's last panel is closed and stopped.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import time
from pathlib import Path
from queue import Empty
from uuid import uuid4

from jupyter_client import write_connection_file
from jupyter_client.asynchronous import AsyncKernelClient
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from . import research_process
from .materials import plain
from .project_paths import MODULE

LAUNCH = MODULE / "native" / "kernel_launch.py"
READY_SECONDS = 180
PANEL_QUEUE = 20000
PREVIEW = 120
STATES = ("preparing", "starting", "idle", "busy", "restarting", "dead")
PROCESS_FREE = ("MPLBACKEND", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")


class InterruptEvent:
    """The named event the kernel's launch script opens: setting it interrupts the running cell on Windows."""

    def __init__(self):
        self.name = "Local\\talyx-kernel-" + uuid4().hex
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.SetEvent.argtypes = kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = kernel32.CreateEventW(None, True, False, self.name)
        if not self.handle:
            raise ctypes.WinError()

    def set(self):
        if not ctypes.windll.kernel32.SetEvent(self.handle):
            raise ctypes.WinError()

    def close(self):
        ctypes.windll.kernel32.CloseHandle(self.handle)


def stream_output(content):
    return {"output_type": "stream", "name": content["name"], "text": content["text"]}


def data_output(kind, content):
    output = {"output_type": kind, "data": content["data"], "metadata": content["metadata"]}
    if kind == "execute_result":
        output["execution_count"] = content["execution_count"]
    return output


def display_id(content):
    """The id a later update_display_data names; only some displays carry one (the kernel's messages are the boundary here)."""
    return content.get("transient", {}).get("display_id")


CAPTURED = {"stream", "display_data", "execute_result", "error", "clear_output"}  # what an Output widget takes while it captures a request


def error_output(content):
    return {"output_type": "error", "ename": content["ename"], "evalue": content["evalue"], "traceback": content["traceback"]}


class Panel:
    """One panel's connection: messages wait in its queue until its socket sends them."""

    def __init__(self, panel_id):
        self.id = panel_id
        self.queue = asyncio.Queue(PANEL_QUEUE)
        self.dropped = False


class KernelSession:
    def __init__(self, manager, workspace_id, record):
        self.manager, self.workspace_id = manager, workspace_id
        self.object_id, self.name, self.directory = record["id"], record["name"], Path(record["directory"])
        self.record = record
        self.panels = {}
        self.cells = {}
        self.requests = {}
        self.inputs = {}
        self.variables, self.variable_queries, self.table_queries = [], set(), {}
        self.comms = {}  # widget models alive in the kernel: comm id -> the comm_open the kernel sent, replayed to a panel that attaches later
        self.capturing = {}  # an Output widget's msg_id (the request whose outputs it takes, as `with out:` sets it) -> its comm id
        self.clearing = set()  # Output widgets that clear_output(wait=True) empties when their next output comes
        self.loaded, self.memory, self.started_at = [], None, None
        self.state, self.message = "preparing", None
        self.since = int(time.time() * 1000)
        self.epoch = None
        self.environment = None
        self.client = self.lease = self.event = self.preparation = None
        self.ready = asyncio.Event()
        self.failed = None
        self.task = None
        self.stopping = self.restarting = False

    # views
    def view(self):
        running = [{"cell": item["cell"], "msgId": msg_id, "startedAt": item["startedAt"], "preview": item["preview"]} for msg_id, item in self.cells.items()]
        return {"objectId": self.object_id, "sessionId": self.epoch, "state": self.state, "message": self.message, "busy": bool(running), "running": running,
                "panels": len(self.panels), "environment": self.environment, "environmentId": self.wanted_environment(), "since": self.since}

    def broadcast(self, message):
        if "msgId" in message and self.cells[message["msgId"]].get("waiter") is not None:
            message = {**message, "savedByAction": True}
        for panel in list(self.panels.values()):
            try:
                panel.queue.put_nowait(message)
            except asyncio.QueueFull:
                # A panel that cannot keep up would stall the kernel's output: what waits for it is dropped and its socket closed (None), so the
                # panel reconnects at once and reads the state again, instead of staying open and hearing nothing more.
                panel.dropped = True
                self.panels.pop(panel.id, None)
                while not panel.queue.empty():
                    panel.queue.get_nowait()
                panel.queue.put_nowait(None)

    def send(self, panel_id, message):
        if panel_id in self.panels:
            self.panels[panel_id].queue.put_nowait(message)

    def set_state(self, state, message=None):
        # When the kernel came to this state: a first start may prepare the environment for a minute, and the panel counts the time.
        if state != self.state:
            self.since = int(time.time() * 1000)
        self.state, self.message = state, message
        self.broadcast({"type": "state", **self.view()})

    def attach(self, panel_id):
        panel = Panel(panel_id)
        self.panels[panel_id] = panel
        # Every panel hears the new count, so the one closing last knows it is last.
        self.broadcast({"type": "state", **self.view()})
        if self.variables:
            panel.queue.put_nowait(self.variables_message())
        for comm_id, opened in self.comms.items():
            panel.queue.put_nowait({"type": "comm", "msgType": "comm_open", "commId": comm_id, **opened})
        for cell_id, pending in self.inputs.items():
            panel.queue.put_nowait({"type": "input_request", "cellId": cell_id, "prompt": pending["prompt"], "password": pending["password"]})
        return panel

    def detach(self, panel_id):
        self.panels.pop(panel_id, None)
        self.broadcast({"type": "state", **self.view()})

    # life
    def start(self):
        """Run the kernel's whole life in the workspace's background; a second start after a death begins a fresh run."""
        self.reset_snapshot()
        self.failed, self.stopping = None, False
        self.ready = asyncio.Event()
        self.set_state("preparing")
        ctx = self.manager.ctx
        # The workspace owns the reusable kernel. A cell's caller waits for the cell, not the kernel's lifetime.
        activity = ctx.sdk.activity(self.workspace_id, t("Notebook kernel · {name}", name=self.name), call_id=None,
                                    open_target={"module": "quant", "panelType": "workspace",
                                                 "objectId": "object:" + self.object_id})
        self.task = ctx.start(self.run(), activity=activity)

    async def run(self):
        try:
            while True:
                self.restarting = False
                await self.launch()
                await self.serve()
                if not self.restarting:
                    break
                self.set_state("restarting")
        except asyncio.CancelledError:
            self.set_state("dead", "Stopped.")
            self.manager.sessions.pop((self.workspace_id, self.object_id), None)
            raise
        except ContractError as error:
            self.failed = error
            self.set_state("dead", error.message)
        finally:
            self.ready.set()
            await self.close_process()
            await self.release_environment()

    def wanted_environment(self):
        settings = self.record["settings"] if "settings" in self.record else {}
        return (settings.get("kernel") or {}).get("environment", "research")

    async def launch(self):
        ctx = self.manager.ctx
        self.epoch = uuid4().hex
        self.comms, self.capturing, self.clearing = {}, {}, set()
        self.set_state("preparing")

        def environment_progress(state):
            self.environment = {**(self.environment or {}), "preparation": {"state": state["state"], "message": state["message"] if "message" in state else None}}
            self.broadcast({"type": "state", **self.view()})

        environment = await self.manager.packages.kernel_environment(self.wanted_environment(), environment_progress)
        self.environment = {"lockDigest": environment["lockDigest"], "lockPath": environment["lockPath"], "pythonVersion": environment["pythonVersion"],
                            "interpreterPath": environment["interpreterPath"], "preparation": {"state": "completed", "message": None}}
        ctx.environments.hold(environment["preparationId"], "qkernel:" + self.epoch)
        self.preparation = environment["preparationId"]
        try:
            await self.manager.packages.point_vscode(environment, [self.record])
        except ContractError as error:
            # Its file is the user's own; a settings file VS Code cannot use must not keep the kernel from starting.
            self.environment["vscode"] = error.message
        self.set_state("starting")
        directory = self.manager.root / self.epoch
        directory.mkdir(parents=True)
        connection, _ = write_connection_file(str(directory / "kernel.json"), ip="127.0.0.1")
        self.event = InterruptEvent()
        support = research_process.write_support(directory)
        env = research_process.environment()
        for name in PROCESS_FREE:
            env.pop(name, None)
        env.update(IPYTHONDIR=str(self.manager.root / "ipython"), MATPLOTLIBRC=str(MODULE / "native" / "notebook.matplotlibrc"))
        launch = {"submission_key": "qkernel-" + self.epoch,
                  "argv": [environment["interpreterPath"], "-B", str(LAUNCH), "-f", connection, "--interrupt-event", self.event.name,
                           "--path", str(support), "--path", str(MODULE), "--path", str(self.manager.common.root)],
                  "cwd": str(self.directory), "env": env, "stdout_path": str(directory / "stdout.log"), "stderr_path": str(directory / "stderr.log"),
                  "channel": {"directory": str(directory / "channel"), "context": {"objectId": self.object_id, "kind": "notebook"}},
                  "activity": "notebook:" + self.object_id}
        launching = asyncio.create_task(ctx.processes.start(launch))
        try:
            self.lease = plain(await asyncio.shield(launching))
        except asyncio.CancelledError:
            # The process exists as soon as the platform answers; nothing may outlive the session that asked for it.
            self.lease = plain(await launching)
            raise
        self.client = AsyncKernelClient(connection_file=connection)
        self.client.load_connection_file()
        # No heartbeat channel: the platform watches the process. The stdin channel is open, but only a cell the user runs from a panel allows input.
        self.client.start_channels(shell=True, iopub=True, stdin=True, hb=False, control=False)
        try:
            async with asyncio.timeout(READY_SECONDS):
                waiting = asyncio.create_task(self.wait_ready())
                exited = asyncio.create_task(self.exit(self.lease))
                done, _ = await asyncio.wait({waiting, exited}, return_when=asyncio.FIRST_COMPLETED)
                for task in (waiting, exited):
                    if task not in done:
                        task.cancel()
                if exited in done:
                    raise ContractError("unavailable", t("The kernel stopped while it was starting."), {"stderr": self.stderr_tail(directory)})
                waiting.result()
        except TimeoutError as error:
            raise ContractError("unavailable", t("The kernel did not answer within three minutes."), {"stderr": self.stderr_tail(directory)}) from error
        self.ready.set()
        self.set_state("idle")

    async def wait_ready(self):
        """Ask until the kernel answers, then drop what it said while the connection was forming."""
        while True:
            self.client.kernel_info()
            try:
                message = await self.client.get_shell_msg(timeout=1)
            except Empty:
                continue
            if message["msg_type"] == "kernel_info_reply":
                break
        with contextlib.suppress(Empty):
            while True:
                await self.client.get_iopub_msg(timeout=.3)

    @staticmethod
    def stderr_tail(directory):
        log = Path(directory) / "stderr.log"
        return log.read_text(encoding="utf-8", errors="replace")[-3000:] if log.is_file() else ""

    async def exit(self, lease):
        """Returns when the kernel's process has ended."""
        while True:
            try:
                if not plain(await self.manager.ctx.processes.status(lease))["running"]:
                    return
            except ContractError as error:
                if error.code != "target_gone":
                    raise
                return
            await asyncio.sleep(.2)

    async def serve(self):
        """Pass the kernel's messages on until its process ends; then every cell it was running is interrupted."""
        pumps = [asyncio.create_task(self.iopub()), asyncio.create_task(self.shell()), asyncio.create_task(self.stdin())]
        exited = asyncio.create_task(self.exit(self.lease))
        try:
            done, _ = await asyncio.wait({exited, *pumps}, return_when=asyncio.FIRST_COMPLETED)
            if exited not in done:
                # A pump that ends while the process lives is a bug or a broken connection, never a normal stop.
                for task in done:
                    task.result()
                raise ContractError("unavailable", t("The connection to the kernel closed."))
            exited.result()
        finally:
            for task in (*pumps, exited):
                task.cancel()
            await asyncio.gather(*pumps, exited, return_exceptions=True)
            await self.close_process()
            await self.release_environment()
            for msg_id in list(self.cells):
                self.finish(msg_id, "interrupted", None, ended=True)
            self.close_inputs()
            if not (self.stopping or self.restarting):
                self.set_state("dead", "The kernel stopped unexpectedly. Restart it to continue; variables are lost.")

    async def close_process(self):
        for target, _ in self.table_queries.values():
            if isinstance(target, asyncio.Future) and not target.done():
                target.set_exception(ContractError("unavailable", t("The notebook kernel stopped during the variable read.")))
        self.table_queries.clear()
        self.requests.clear()
        if self.client is not None:
            self.client.stop_channels()
            self.client = None
        if self.lease is not None:
            lease, self.lease = self.lease, None
            with contextlib.suppress(ContractError):
                await self.manager.ctx.processes.cancel(lease)
            with contextlib.suppress(ContractError):
                await self.manager.ctx.processes.close(lease)
        if self.event is not None:
            self.event.close()
            self.event = None

    async def release_environment(self):
        if self.preparation is not None:
            preparation, self.preparation = self.preparation, None
            with contextlib.suppress(ContractError):
                self.manager.ctx.environments.release(preparation, "qkernel:" + self.epoch)

    # messages from the kernel
    async def iopub(self):
        while True:
            self.heard(await self.client.get_iopub_msg())

    def heard(self, message):
        """One message from the kernel's iopub channel."""
        parent, kind, content = message["parent_header"]["msg_id"] if message["parent_header"] else None, message["msg_type"], message["content"]
        if kind in {"comm_open", "comm_msg", "comm_close"}:
            self.comm(kind, content)
            return
        if parent in self.capturing and kind in CAPTURED:
            # Inside `with out:` (what interact does on every change) the outputs belong to the Output widget, not to the cell that ran it.
            self.capture(self.capturing[parent], kind, content)
            return
        cell = self.cells.get(parent)
        if cell is None:
            return
        number = cell["cell"]
        if kind == "status":
            if content["execution_state"] == "idle":
                cell["idle"] = True
                self.settle(parent)
        elif kind == "execute_input":
            self.broadcast({"type": "input", "cell": number, "msgId": parent, "executionCount": content["execution_count"]})
        elif kind == "stream":
            self.output(parent, stream_output(content))
        elif kind in {"display_data", "execute_result"}:
            self.output(parent, data_output(kind, content), display_id(content))
        elif kind == "update_display_data":
            key = content["transient"]["display_id"]
            output = data_output("display_data", content)
            for index in cell.get("displayIds", {}).get(key, []):
                cell["outputs"][index] = output
            self.broadcast({"type": "output_update", "cell": number, "msgId": parent, "displayId": key, "output": output})
        elif kind == "error":
            self.output(parent, error_output(content))
        elif kind == "clear_output":
            cell["clearPending"] = content["wait"]
            if not cell["clearPending"]:
                cell["outputs"].clear()
                cell.pop("displayIds", None)
            self.broadcast({"type": "clear_output", "cell": number, "msgId": parent, "wait": content["wait"]})

    def comm(self, kind, content):
        """A widget model's messages (open, update, close) go to every panel; the open ones are kept so a panel that comes later can build the same models."""
        comm_id = content["comm_id"]
        if kind == "comm_open":
            self.comms[comm_id] = {"target": content.get("target_name"), "data": content.get("data", {})}
        elif kind == "comm_close":
            self.comms.pop(comm_id, None)
            self.capturing = {key: value for key, value in self.capturing.items() if value != comm_id}
        elif kind == "comm_msg" and comm_id in self.comms:
            data = content.get("data") or {}
            if data.get("method") == "update":
                self.fold(comm_id, data.get("state") or {})
        state = (self.comms.get(comm_id) or {}).get("data", {}).get("state") or {}
        if state.get("_model_name") == "OutputModel" and "msg_id" in ((content.get("data") or {}).get("state") or {}):
            # The kernel only says which request an Output widget takes the outputs of; a front end does the taking (JupyterLab does it in the page).
            self.capturing = {key: value for key, value in self.capturing.items() if value != comm_id}
            if state.get("msg_id"):
                self.capturing[state["msg_id"]] = comm_id
        self.broadcast({"type": "comm", "msgType": kind, "commId": comm_id, "data": content.get("data", {}), **({"target": content["target_name"]} if "target_name" in content else {})})

    def fold(self, comm_id, changes):
        """A state update folded into the kept open, so a late panel sees the current values."""
        kept = self.comms[comm_id]["data"]
        if isinstance(kept.get("state"), dict):
            self.comms[comm_id]["data"] = {**kept, "state": {**kept["state"], **changes}}

    def capture(self, comm_id, kind, content):
        """One output inside an Output widget: its `outputs` state changes and every panel's widget hears the update, as Jupyter's Output widget model
        would set it. clear_output(wait=True) empties it when the next output comes."""
        if comm_id not in self.comms:
            return
        outputs = list((self.comms[comm_id]["data"].get("state") or {}).get("outputs") or [])
        if kind == "clear_output":
            if content.get("wait"):
                self.clearing.add(comm_id)
                return
            self.clearing.discard(comm_id)
            changes = {"outputs": []}
        else:
            if comm_id in self.clearing:
                self.clearing.discard(comm_id)
                outputs = []
            if kind == "stream":
                output = stream_output(content)
                if outputs and outputs[-1].get("output_type") == "stream" and outputs[-1].get("name") == output["name"]:
                    output = {**outputs[-1], "text": outputs[-1]["text"] + output["text"]}
                    outputs = outputs[:-1]
            elif kind == "error":
                output = error_output(content)
            else:
                output = data_output("display_data" if kind == "display_data" else "execute_result", content)
            changes = {"outputs": [*outputs, output]}
        self.fold(comm_id, changes)
        self.broadcast({"type": "comm", "msgType": "comm_msg", "commId": comm_id, "data": {"method": "update", "state": changes, "buffer_paths": []}})

    def comm_to_kernel(self, comm_id, data):
        """A panel's widget changed (a slider moved): the same comm message JupyterLab would send, on the shell channel."""
        if self.client is None or comm_id not in self.comms:
            return False
        self.client.shell_channel.send(self.client.session.msg("comm_msg", {"comm_id": comm_id, "data": data}))
        return True

    async def stdin(self):
        """A cell the user runs may ask for input(): the prompt goes to every panel showing the notebook, and the first answer goes back to the kernel."""
        while True:
            message = await self.client.get_stdin_msg()
            if message["msg_type"] != "input_request":
                continue
            parent = message["parent_header"]["msg_id"] if message["parent_header"] else None
            cell = self.cells.get(parent)
            if cell is None:
                continue
            content = message["content"]
            self.inputs[cell["cell"]] = {"msgId": parent, "prompt": content["prompt"], "password": bool(content["password"])}
            self.broadcast({"type": "input_request", "cellId": cell["cell"], "prompt": content["prompt"], "password": bool(content["password"])})

    def input_reply(self, cell_id, value):
        """The first reply wins; the line it makes (`prompt value`, or dots for a password) becomes part of the cell's output."""
        pending = self.inputs.pop(cell_id, None)
        if pending is None or self.client is None:
            return False
        self.client.input(value)
        text = pending["prompt"] + ("········" if pending["password"] else value)
        cell = self.cells.get(pending["msgId"])
        if cell is not None:
            self.output(pending["msgId"], {"output_type": "stream", "name": "stdin", "text": text + "\n"})
        self.broadcast({"type": "input_done", "cellId": cell_id, "text": text})
        return True

    def close_inputs(self, cell_id=None):
        """An interrupt, a restart or a stop takes the input boxes away: the cell is over or about to be."""
        for key in [cell_id] if cell_id is not None else list(self.inputs):
            if self.inputs.pop(key, None) is not None:
                self.broadcast({"type": "input_done", "cellId": key, "text": ""})

    def output(self, msg_id, output, display_id=None):
        cell = self.cells[msg_id]
        if cell.pop("clearPending", False):
            cell["outputs"].clear()
            cell.pop("displayIds", None)
        if display_id is not None:
            cell.setdefault("displayIds", {}).setdefault(display_id, []).append(len(cell["outputs"]))
        cell["outputs"].append(output)
        self.broadcast({"type": "output", "cell": cell["cell"], "msgId": msg_id, "output": output, **({"displayId": display_id} if display_id else {})})

    async def shell(self):
        while True:
            message = await self.client.get_shell_msg()
            parent, kind, content = message["parent_header"]["msg_id"], message["msg_type"], message["content"]
            if kind == "execute_reply" and parent in self.cells:
                self.cells[parent].update(replied=True, status=content["status"], executionCount=content["execution_count"])
                self.settle(parent)
            elif kind == "execute_reply" and parent in self.table_queries:
                self.read_table(parent, content)
            elif kind == "execute_reply" and parent in self.variable_queries:
                self.variable_queries.discard(parent)
                self.read_variables(content)
            elif kind in {"complete_reply", "inspect_reply"} and parent in self.requests:
                panel, request = self.requests.pop(parent)
                self.send(panel, {"type": kind, "id": request, **content})

    def ask_variables(self):
        """After a cell, one silent request for the variable list; it queues behind whatever the user sent next and never takes input."""
        if self.client is None or self.restarting or self.state == "dead":
            return
        self.variable_queries.add(self.client.execute("", silent=True, store_history=False, allow_stdin=False, user_expressions={"v": "__talyx_vars__()"}))

    def read_variables(self, content):
        """The list arrives as the repr of a JSON string in `user_expressions`; a failed query leaves the last list."""
        import ast
        import json
        try:
            shown = content["user_expressions"]["v"]
            if shown["status"] != "ok":
                return
            found = json.loads(ast.literal_eval(shown["data"]["text/plain"]))
        except (KeyError, ValueError, SyntaxError, TypeError):
            return
        self.variables, self.loaded = found["variables"], found["loaded"]
        self.memory, self.started_at = found.get("memory"), found.get("startedAt")
        self.broadcast(self.variables_message())

    def reset_snapshot(self):
        self.variables, self.loaded, self.memory, self.started_at = [], [], None, None
        self.variable_queries.clear()
        self.broadcast(self.variables_message())

    def variables_message(self):
        return {"type": "variables", "variables": self.variables, "loaded": self.loaded, "memory": self.memory, "startedAt": self.started_at}

    def ask_table(self, panel, name):
        """One variable as a table for the explorer: a silent request whose answer goes to the asking panel only."""
        if self.client is None:
            return
        msg_id = self.client.execute("", silent=True, store_history=False, allow_stdin=False, user_expressions={"v": f"__talyx_table__({name!r})"})
        self.table_queries[msg_id] = (panel, name)
        return msg_id

    async def variable_table(self, name):
        await self.running_client()
        result = asyncio.get_running_loop().create_future()
        msg_id = self.ask_table(result, name)
        try:
            return await result
        finally:
            self.table_queries.pop(msg_id, None)

    def read_table(self, msg_id, content):
        import ast
        import json
        panel, name = self.table_queries.pop(msg_id)
        try:
            table = json.loads(ast.literal_eval(content["user_expressions"]["v"]["data"]["text/plain"]))
        except (KeyError, ValueError, SyntaxError, TypeError):
            table = None
        if isinstance(panel, asyncio.Future):
            if not panel.done():
                panel.set_result(table)
        else:
            self.send(panel, {"type": "variable_table", "name": name, "table": table})

    def settle(self, msg_id):
        """A cell is over once the kernel has replied and gone idle for it: only then is its output complete."""
        cell = self.cells[msg_id]
        if cell["replied"] and cell["idle"]:
            self.finish(msg_id, cell["status"], cell["executionCount"])

    def finish(self, msg_id, status, execution_count, ended=False):
        cell = self.cells[msg_id]
        self.close_inputs(cell["cell"])
        self.broadcast({"type": "reply", "cell": cell["cell"], "msgId": msg_id, "status": status, "executionCount": execution_count})
        del self.cells[msg_id]
        if not ended and not self.cells and self.state == "busy":
            self.set_state("idle")
        if not ended:
            self.ask_variables()
        if cell["waiter"] is not None and not cell["waiter"].done():
            cell["waiter"].set_result({"status": status, "executionCount": execution_count, "outputs": cell["outputs"]})

    # what panels ask
    async def running_client(self):
        await self.ready.wait()
        if self.failed is not None:
            raise self.failed
        if self.client is None:
            raise ContractError("unavailable", t("The kernel is not running. Restart it to continue."), {"state": self.state})
        return self.client

    async def execute(self, cell_id, code, *, wait=False, allow_stdin=False):
        """Send the cell's code to the kernel; with `wait`, return what the kernel did once it finished. Only a cell the user runs may ask for input."""
        client = await self.running_client()
        msg_id = client.execute(code, allow_stdin=allow_stdin, stop_on_error=True)
        waiter = asyncio.get_running_loop().create_future() if wait else None
        self.cells[msg_id] = {"cell": cell_id, "startedAt": int(time.time() * 1000), "preview": code[:PREVIEW], "replied": False, "idle": False,
                              "outputs": [], "waiter": waiter, "status": None, "executionCount": None}
        self.broadcast({"type": "accepted", "cell": cell_id, "msgId": msg_id})
        self.set_state("busy")
        return waiter if wait else msg_id

    async def ask(self, panel, kind, request, code, cursor):
        """Completion or hover text for one panel's editor; the answer goes to that panel only."""
        client = await self.running_client()
        msg_id = client.complete(code, cursor) if kind == "complete" else client.inspect(code, cursor, detail_level=0)
        self.requests[msg_id] = (panel, request)

    def interrupt(self):
        """Stop the running cell now: variables stay. Windows has no message interrupt, so the launch script's event does it."""
        if self.event is None or self.state not in {"idle", "busy"}:
            raise ContractError("conflict", t("The kernel is not running."), {"state": self.state})
        self.event.set()
        self.close_inputs()

    def check_restart(self):
        if self.state in {"preparing", "starting", "restarting"}:
            raise ContractError("conflict", t("The kernel is already starting."), {"state": self.state})

    async def restart(self):
        """A new kernel process in the same session: every variable is lost and the running cells are interrupted."""
        self.check_restart()
        if self.task is None or self.task.done():
            self.start()
            return
        self.restarting = True
        self.ready.clear()
        self.set_state("restarting")
        self.reset_snapshot()
        self.close_inputs()
        # Only the process goes; the serving task sees it end, interrupts the cells and starts the next one.
        with contextlib.suppress(ContractError):
            await self.manager.ctx.processes.cancel(self.lease)

    async def stop(self):
        self.stopping = True
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.manager.sessions.pop((self.workspace_id, self.object_id), None)


class Kernels:
    def __init__(self, ctx, records, common, packages):
        self.ctx, self.common, self.packages = ctx, common, packages
        self.root = records.root / "kernels"
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions = {}
        packages.kernels = self

    def key(self, object_id):
        return self.ctx.workspace(), object_id

    def find(self, object_id):
        return self.sessions.get(self.key(object_id))

    def session(self, object_id):
        session = self.find(object_id)
        if session is None:
            raise ContractError("not_found", t("This notebook has no kernel in this workspace. Open it in a panel to start one."))
        return session

    def ensure(self, record):
        """The notebook's kernel in this workspace, started if it was not running."""
        workspace, object_id = self.key(record["id"])
        if workspace is None:
            raise ContractError("invalid_input", t("A notebook's kernel belongs to a workspace."))
        session = self.sessions.get((workspace, object_id))
        if session is None:
            session = self.sessions[(workspace, object_id)] = KernelSession(self, workspace, record)
            session.start()
        elif session.task is None or session.task.done():
            session.start()
        return session

    def status(self, object_id):
        session = self.find(object_id)
        return session.view() if session is not None else {"objectId": object_id, "sessionId": None, "state": "none", "message": None, "busy": False, "running": [], "panels": 0, "environment": None}

    def stale(self, lock_digest):
        """Kernels running an environment older than the lock new work would use: they need a restart to see the new packages."""
        return [{"objectId": item.object_id, "name": item.name, "workspaceId": item.workspace_id} for item in self.sessions.values()
                if item.state not in {"dead", "preparing"} and item.environment is not None and item.environment["lockDigest"] != lock_digest]

    # the workspace's close list
    def activities(self, workspace_id):
        return [{"id": "notebook:" + session.object_id, "kind": "calculation", "title": "Notebook kernel · " + session.name,
                 "target": {"panel": "quant.workspace", "objectId": session.object_id}}
                for (workspace, _), session in self.sessions.items() if workspace == workspace_id]

    async def stop_activity(self, workspace_id, activity_id, reason):
        session = self.sessions.get((workspace_id, activity_id.removeprefix("notebook:")))
        if session is not None:
            await session.stop()

    async def close(self):
        for session in list(self.sessions.values()):
            await session.stop()
