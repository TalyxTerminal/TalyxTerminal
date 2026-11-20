"""The Host: the platform's own process. It holds the router, the module processes and the platform's services;
it runs no module code."""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import logging
import secrets
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from talyx_sdk import memory, stall, wire
from talyx_sdk.contract import build
from talyx_sdk.errors import ContractError
from talyx_sdk.process import Codec, error_value

from kernel.router import Endpoint, Route, Router

from . import catalog as catalog_module
from .ai import AI, MCP
from .catalog import Catalog, read_docs, read_guide
from .files import default_data_dir as default_data_dir
from .files import finish_before_cancel
from .notifications import ChannelSecrets, NotificationService, Toasts
from .platform import declarations as platform
from .platform import services
from .records import CallLog, Runs, Tasks
from .secrets import Secrets
from .settings import GLOBAL, Settings
from .supervisor import ModuleProcess, Supervisor
from .workspaces import Workspaces, panel_modules

log = logging.getLogger("talyx.host")
ROOT = Path(__file__).resolve().parents[1]

# A module must stop a closing workspace's work within this time, or its process is restarted.
STOP_SECONDS = 5.0
# How long a module may take to be ready for, and then to do, its part of copying a workspace.
COPY_SECONDS = 60.0


@dataclass
class HostConfig:
    data_dir: Path
    user_dir: Path
    port: int = 8000
    ui_url: str = "http://127.0.0.1:5173/"
    official_root: Path = ROOT / "modules"
    platform_root: Path = ROOT
    secrets_store: Any = None
    only: set[str] | None = field(default=None)   # start only these modules (tests)
    desktop_token: str | None = field(default=None, repr=False)
    desktop: bool = False                         # run for the desktop app: apply the machine's General settings


class PlatformEndpoint(Endpoint):
    """The platform's own actions and streams, run in the Host."""

    name, module = "platform", "platform"

    def __init__(self, host: Host):
        self.host = host
        self.tasks: dict[str, asyncio.Task] = {}
        self.codecs: dict[int, Codec] = {}

    def codec(self, annotation) -> Codec:
        if id(annotation) not in self.codecs:
            self.codecs[id(annotation)] = Codec(annotation)
        return self.codecs[id(annotation)]

    def caller(self, envelope: dict) -> dict:
        raise ContractError("permission_denied", "The platform does not call itself through the router")

    def deliver(self, envelope: dict, body: bytes = b"") -> None:
        kind = envelope["t"]
        if kind == "call":
            self.tasks[envelope["id"]] = asyncio.create_task(self._call(envelope, body))
        elif kind == "sub":
            self.tasks[envelope["sub"]] = asyncio.create_task(self._stream(envelope, body))
        elif kind in ("unsub", "cancel"):
            task = self.tasks.pop(envelope.get("sub") or envelope.get("id"), None)
            if task is not None:
                task.cancel()

    async def _call(self, envelope: dict, body: bytes) -> None:
        name = envelope["cap"].removeprefix("platform.")
        spec = platform.module.actions[name]
        try:
            value = self.codec(spec.input).parse(body)
            result = await spec.handler(value, HostCall(envelope["from"]))
            data = self.codec(spec.output).check(result)
            self.host.router.answer(self, {"t": "result", "id": envelope["id"]}, data)
        except asyncio.CancelledError:
            self.host.router.answer(self, {"t": "error", "id": envelope["id"], "err": ContractError(
                "cancelled", "Cancelled", execution="unknown").to_dict()}, b"")
        except Exception as exc:  # noqa: BLE001 - every failure answers in the one error format
            self.host.router.answer(self, {"t": "error", "id": envelope["id"], "err": error_value(exc, invalid_input=True)},
                                    b"")
        finally:
            self.tasks.pop(envelope["id"], None)

    async def _stream(self, envelope: dict, body: bytes) -> None:
        name = envelope["cap"].removeprefix("platform.")
        spec = platform.module.streams[name]
        error = None
        try:
            params = self.codec(spec.params).parse(body)
            item = self.codec(spec.item)
            async for value in spec.handler(params, HostCall(envelope["from"])):
                self.host.router.item(self, {"t": "item", "sub": envelope["sub"]}, item.check(value))
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            error = error_value(exc, invalid_input=True)
        finally:
            self.tasks.pop(envelope["sub"], None)
        self.host.router.end(self, {"t": "end", "sub": envelope["sub"], **({"err": error} if error else {})})


class HostCall:
    def __init__(self, caller: dict):
        self.caller = caller


class InternalEndpoint(Endpoint):
    """Calls the Host itself makes for someone else (AI, running code, a window action): the caller is given."""

    name, module = "internal", None

    def __init__(self, host: Host):
        self.host = host
        self.ids = itertools.count(1)
        self.pending: dict[str, asyncio.Future] = {}

    def caller(self, envelope: dict) -> dict:
        return envelope["from"]

    def deliver(self, envelope: dict, body: bytes = b"") -> None:
        future = self.pending.pop(envelope.get("id"), None)
        if future is None or future.done():
            return
        if envelope["t"] == "result":
            future.set_result(body)
        else:
            future.set_exception(ContractError.from_dict(envelope["err"]))

    async def call_raw(self, caller: dict, capability: str, value, *, endpoint: Endpoint | None = None,
                       host_id: str | None = None) -> bytes:
        request_id = f"i{next(self.ids)}"
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.host.router.call(self, {"t": "call", "id": request_id, "cap": capability, "from": caller},
                              wire.encode(value), route=Route("window", endpoint) if endpoint else None,
                              host_id=host_id)
        try:
            return await future
        finally:
            self.pending.pop(request_id, None)

    async def call(self, caller: dict, capability: str, value, **options):
        return wire.decode(await self.call_raw(caller, capability, value, **options))


class Host:
    def __init__(self, config: HostConfig):
        self.config = config
        data = config.data_dir
        self.instance = secrets.token_hex(8)
        self.catalog = Catalog(config.official_root, config.user_dir / "modules", data / "platform" / "modules.json",
                               data / "platform" / "environments")
        self.catalog_version = 0
        self.calls = CallLog(data / "platform" / "calls")
        self.router = Router(self.resolve, self.authorize, self.record)
        self.platform = PlatformEndpoint(self)
        self.internal = InternalEndpoint(self)
        self.asked: dict[str, tuple[ModuleProcess, asyncio.Future]] = {}
        self.sessions: dict[str, Endpoint] = {}
        self.workspaces = Workspaces(data / "platform", config.user_dir / "workspaces",
                                     (data.resolve(), (config.user_dir / "modules").resolve()))
        self.settings = Settings(data / "platform" / "settings.json", self.setting_declarations,
                                 self.workspaces.is_open)
        self.tasks = Tasks(self.cancel_task)
        self.runs = Runs(lambda: f"http://127.0.0.1:{self.config.port}/api/runs/call", self.calls)
        self.secrets = Secrets(data, config.secrets_store)
        self.notifications = NotificationService(data / "platform" / "notifications", ChannelSecrets(self.secrets))
        self.toasts = Toasts()
        self.click_listeners: set[asyncio.Queue] = set()
        self.supervisor = Supervisor(self.router, config.platform_root, self.check_workspace, self.process_changed,
                                     self.module_frame)
        # Shared by every module: request-limit domains and cooldowns of remote APIs several modules call.
        self.supervisor.environment["TALYX_SHARED_DIR"] = str(data / "shared")
        self.ai = AI(self)
        self.mcp = MCP(self.ai)
        self.change_listeners: set[asyncio.Queue] = set()
        self.activity_listeners: set[asyncio.Queue] = set()
        self.desktop = None
        self.platform_contract = build(platform.module)
        services.HOST = self

    # ── starting and stopping ────────────────────────────────────────────

    async def start(self) -> None:
        self.stall = stall.watch("host")
        await asyncio.to_thread(self.calls.sweep)
        await self.notifications.start()
        if self.config.desktop:
            from .desktop_effects import DesktopEffects
            self.desktop = DesktopEffects(self, self.config.data_dir)
            self.desktop.start()
        self.catalog.discover()
        for skipped, reason in self.catalog.skipped.items():
            log.warning("Module %s is not loaded: %s", skipped, reason.problem)
        await self.supervisor.listen()
        for item in self.catalog.modules.values():
            if item.enabled and (self.config.only is None or item.id in self.config.only):
                await self.start_module(item)
        self.collector = memory.settle(lambda: bool(self.router.calls))

    async def start_module(self, item: catalog_module.Installed) -> ModuleProcess:
        if item.manifest.get("python", {}).get("requirements"):
            await self.prepare_environment(item)
        process = ModuleProcess(self.supervisor, item.id, item.directory, item.package,
                                item.manifest["entries"]["backend"], item.contract, item.python)
        self.supervisor.add(process)
        if item.environment_problem:
            process.state, process.problem = "failed", item.environment_problem
            return process
        await process.start()
        return process

    async def prepare_environment(self, item: catalog_module.Installed) -> None:
        """A module needing extra packages gets its own environment on top of TALYX's; when it cannot be built the
        module does not start and says what is missing (it never falls back to another environment)."""
        folder = self.catalog.environments_root / item.id
        python = self.catalog.environment_python(item)
        requirements = item.manifest["python"]["requirements"]
        item.environment_problem = None
        if not python.exists():
            done = await asyncio.to_thread(subprocess.run, ["uv", "venv", "--python", sys.executable, str(folder)],
                                           capture_output=True, text=True)
            if done.returncode != 0:
                item.environment_problem = f"Its Python environment could not be created:\n{done.stderr[-3000:]}"
                return
            # TALYX's own packages come after the module's: what it installs wins, the rest is shared.
            found = await asyncio.to_thread(subprocess.run, [str(python), "-c", "import sysconfig; "
                                                             "print(sysconfig.get_path('purelib'))"],
                                            capture_output=True, text=True, check=True)
            shared = [sysconfig.get_path("purelib"), sysconfig.get_path("platlib")]
            await asyncio.to_thread((Path(found.stdout.strip()) / "talyx-platform.pth").write_text,
                                    "".join(f"import site; site.addsitedir({path!r})\n"
                                            for path in dict.fromkeys(shared)), encoding="utf-8")
        done = await asyncio.to_thread(subprocess.run, ["uv", "pip", "install", "--python", str(python),
                                                        *requirements], capture_output=True, text=True)
        if done.returncode != 0:
            item.environment_problem = ("Its packages could not be installed (" + ", ".join(requirements) + "):\n"
                                        + done.stderr[-3000:])

    async def close(self) -> None:
        memory.restore(getattr(self, "collector", None))
        self.router.end_streams("platform.", ContractError("unavailable", "TALYX is closing"))
        await self.supervisor.stop_all()
        await self.calls.written()
        await self.notifications.close()
        if self.desktop is not None:
            await self.desktop.close()

    # ── where calls go and who may make them ─────────────────────────────

    def resolve(self, capability: str, caller: dict, kind: str) -> Route:
        owner, _, name = capability.partition(".")
        if owner == "platform":
            if kind != "call" and name in platform.module.events:
                return Route("platform", None, event=True)
            declared = platform.module.actions if kind == "call" else platform.module.streams
            if name not in declared:
                raise ContractError("unknown_capability", f"The platform has no {capability}")
            if kind == "call":
                self.check_callers(caller, capability, declared[name].callers)
            return Route("platform", self.platform)
        if ":" in name:
            point, implementer, rest = name.split(":", 2)
            if caller.get("module") != owner:
                raise ContractError("permission_denied", f"Only {owner} calls implementations of {owner}.{point}")
            return self._module_route(implementer)
        item = self.catalog.modules.get(owner)
        if item is None:
            raise ContractError("unknown_capability", f"No module {owner} is installed")
        contract = item.contract
        if kind == "call":
            if name not in contract["actions"]:
                raise ContractError("unknown_capability", f"{owner} has no action {name}")
            action = contract["actions"][name]
            self.check_callers(caller, capability, action.get("callers"))
            return self._module_route(owner, tuple(action["effects"]))
        if name in contract["events"]:
            return Route(owner, None, event=True)
        if name not in contract["streams"]:
            raise ContractError("unknown_capability", f"{owner} has no stream or event {name}")
        return self._module_route(owner)

    def _module_route(self, module_id: str, effects: tuple = ()) -> Route:
        item = self.catalog.modules.get(module_id)
        process = self.supervisor.processes.get(module_id)
        if item is None or not item.enabled or process is None:
            raise ContractError("unavailable", f"{module_id} is not enabled")
        if process.state == "failed":
            raise ContractError("unavailable", f"{module_id} could not start: {process.problem}")
        endpoint = process.endpoint if process.state == "ready" else None
        return Route(module_id, endpoint, effects)

    def authorize(self, caller: dict, capability: str, kind: str) -> None:
        owner = capability.split(".", 1)[0]
        if caller["origin"] == "module" and owner not in ("platform", caller["module"]):
            if kind == "sub":
                provider = self.catalog.modules.get(owner)
                name = capability.partition(".")[2]
                if provider is not None and provider.enabled and provider.contract["streams"].get(name, {}).get("trigger"):
                    return
            item = self.catalog.modules.get(caller["module"])
            if item is None or owner not in item.requires:
                raise ContractError("permission_denied", f"{caller['module']} does not list {owner} in its requires")

    @staticmethod
    def check_callers(caller: dict, capability: str, allowed) -> None:
        if allowed is not None and caller["origin"] not in allowed:
            raise ContractError("permission_denied", f"{capability} only accepts calls from {', '.join(allowed)}")

    def record(self, caller: dict, capability: str, body: bytes, call_id: str) -> None:
        self.calls.begin(caller, capability, body, call_id)
        call = self.router.calls[call_id] if call_id in self.router.calls else None
        previous = call.on_end if call else None

        def ended(_body, error):
            self.calls.end(call_id, error)
            if previous:
                previous(_body, error)

        if call is not None:
            call.on_end = ended

    def check_workspace(self, workspace_id: str) -> None:
        if not self.workspaces.is_open(workspace_id):
            raise ContractError("permission_denied", f"Workspace {workspace_id} is not open")

    def calling_module(self, caller: dict) -> str:
        if caller["module"] is None:
            raise ContractError("permission_denied", "This belongs to a module's backend")
        return caller["module"]

    def module_backend(self, caller: dict) -> str:
        """The calling module, when the call comes from its own backend (not AI acting through it)."""
        if caller["origin"] != "module":
            raise ContractError("permission_denied", "This is for the module's own backend only")
        return caller["module"]

    # ── modules ──────────────────────────────────────────────────────────

    def contracts(self) -> list[tuple[str, dict]]:
        return [("platform", self.platform_contract)] + [(item.id, item.contract)
                                                          for item in self.catalog.modules.values() if item.enabled]

    def setting_declarations(self, module: str) -> tuple[dict, dict]:
        contract = self.platform_contract if module == "platform" else self.catalog.get(module).contract
        return contract["settings"], contract["$defs"]

    def module_state(self, item: catalog_module.Installed) -> dict:
        process = self.supervisor.processes.get(item.id)
        state = process.state if process else "stopped"
        problem = (process.problem if process else None) or item.environment_problem
        return {"id": item.id, "name": item.manifest["name"], "version": item.manifest["version"],
                "description": item.manifest.get("description", ""),
                "source": item.source, "enabled": item.enabled, "state": state, "problem": problem,
                "pid": process.endpoint.pid if process and process.endpoint else None}

    def modules_list(self) -> list[dict]:
        return ([self.module_state(item) for item in self.catalog.modules.values()]
                + [item.state() for item in self.catalog.skipped.values()])

    def definitions(self) -> list[dict]:
        return [{**self.module_state(item), "manifest": item.manifest, "contract": item.contract,
                 "digest": item.digest, "assetBase": f"/api/modules/{item.id}/files/"}
                for item in self.catalog.modules.values()] + [
                {"id": "platform", "name": "TALYX", "version": "0.5.0", "description": "", "source": "official",
                 "enabled": True, "state": "ready", "problem": None, "pid": None, "digest": "platform",
                 "manifest": {"id": "platform", "name": "TALYX", "entries": {}}, "contract": self.platform_contract,
                 "assetBase": ""}]

    async def enable(self, module_id: str) -> dict:
        item = self.catalog.get(module_id)
        missing = [name for name, spec in item.requires.items() if not spec.get("optional") and not (
            name in self.catalog.modules and self.catalog.modules[name].enabled)]
        if missing:
            raise ContractError("unavailable", f"{module_id} requires {', '.join(missing)}")
        if "ai" in item.manifest:
            guide = await asyncio.to_thread(read_guide, item.directory, item.manifest["ai"]["guide"])
            item.docs = await asyncio.to_thread(read_docs, item.directory, item.manifest["ai"].get("docs", []))
            item.guide = guide
        item.enabled = True
        self.catalog.disabled.discard(module_id)
        self.catalog.save_state()
        if module_id not in self.supervisor.processes or self.supervisor.processes[module_id].state == "stopped":
            await self.start_module(item)
        self.modules_changed()
        return self.module_state(item)

    async def disable(self, module_id: str) -> dict:
        item = self.catalog.get(module_id)
        dependents = self.catalog.dependents(module_id)
        if dependents:
            raise ContractError("conflict", f"{', '.join(dependents)} cannot work without {module_id}")
        await self.stop_module(module_id, "disable")
        item.enabled = False
        self.catalog.disabled.add(module_id)
        self.catalog.save_state()
        self.modules_changed()
        return self.module_state(item)

    async def stop_module(self, module_id: str, reason: str) -> None:
        # Workspace frames can hold unsaved documents in another module's embedded views too.
        windows = [(self.sessions[session], info["workspaceId"]) for session, info in self.workspaces.sessions.items()]
        try:
            async with asyncio.timeout(STOP_SECONDS):
                await asyncio.gather(*(self.internal.call(
                    {"origin": "module", "module": "platform", "workspaceId": workspace},
                    "platform.prepare_module_stop", {"id": module_id, "reason": reason}, endpoint=window)
                    for window, workspace in windows))
        except TimeoutError as exc:
            raise ContractError("unavailable", f"An open workspace did not finish preparing {module_id} to stop") from exc
        process = self.supervisor.processes.pop(module_id, None)
        if process is not None:
            await process.stop()
            self.tasks.module_gone(module_id)

    async def reload(self, module_id: str) -> dict:
        """Regenerate the contract from the code, then restart only this module; other modules keep running. The
        environment its manifest asks for now is prepared first, and the contract is generated in it: the code that
        declares the module imports what the module runs with."""
        if failed := self.catalog.skipped.get(module_id):
            try:
                fresh = self.catalog.load(failed.directory, failed.source)
                self.catalog.register(fresh)
            except ContractError as exc:
                failed.problem = exc.message
                self.modules_changed()
                raise
            if fresh.enabled:
                await self.start_module(fresh)
            self.modules_changed()
            return self.module_state(fresh)
        item = self.catalog.get(module_id)
        staged = dataclasses.replace(item, manifest=catalog_module.read_manifest(item.directory), python=sys.executable,
                                     environment_problem=None)
        if staged.manifest.get("python", {}).get("requirements"):
            staged.python = str(self.catalog.environment_python(staged))
            await self.prepare_environment(staged)
            if staged.environment_problem:
                raise ContractError("invalid_module", staged.environment_problem)
        done = await asyncio.create_subprocess_exec(
            staged.python, "-B", str(self.config.platform_root / "scripts/contracts/generate.py"), "--module-dir",
            str(item.directory), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=self._child_env(item))
        output, _ = await done.communicate()
        if done.returncode != 0:
            raise ContractError("invalid_module", "Its contract could not be generated:\n" + output.decode()[-3000:])
        fresh = self.catalog.load(item.directory, item.source)
        await self.stop_module(module_id, "reload")
        self.catalog.modules[module_id] = fresh
        if fresh.enabled:
            await self.start_module(fresh)
        self.modules_changed()
        return self.module_state(fresh)

    def _child_env(self, item: catalog_module.Installed) -> dict:
        import os
        root = self.config.platform_root
        return {**os.environ, "PYTHONPATH": os.pathsep.join([str(root), str(root / "sdk"),
                                                             str(item.directory.parents[item.package.count(".")])])}

    async def install(self, path: str) -> dict:
        item = await asyncio.to_thread(self.catalog.install, Path(path))
        await self.start_module(item)
        self.modules_changed()
        return self.module_state(item)

    async def uninstall(self, module_id: str) -> None:
        if failed := self.catalog.skipped.get(module_id):
            if failed.source != "installed":
                raise ContractError("permission_denied", f"{failed.name} comes with TALYX and cannot be uninstalled")
            await asyncio.to_thread(shutil.rmtree, failed.directory)
            del self.catalog.skipped[module_id]
            self.modules_changed()
            return
        item = self.catalog.get(module_id)
        if item.source != "installed":
            raise ContractError("permission_denied", f"{module_id} comes with TALYX and cannot be uninstalled")
        dependents = self.catalog.dependents(module_id)
        if dependents:
            raise ContractError("conflict", f"{', '.join(dependents)} cannot work without {module_id}")
        await self.stop_module(module_id, "uninstall")
        await asyncio.to_thread(self.catalog.uninstall, module_id)
        self.modules_changed()

    async def module_changes(self):
        queue: asyncio.Queue = asyncio.Queue()
        self.change_listeners.add(queue)
        try:
            while True:
                await queue.get()
                yield self.catalog_version
        finally:
            self.change_listeners.discard(queue)

    def modules_changed(self) -> None:
        self.catalog_version += 1
        for queue in self.change_listeners:
            queue.put_nowait(None)
        for process in self.supervisor.processes.values():
            if process.endpoint is None:
                continue
            for point in process.contract.get("extensionPoints", {}):
                full = f"{process.module_id}.{point}"
                process.endpoint.deliver({"t": "extensions_changed", "point": full},
                                         wire.encode(self.catalog.implementations(full)))

    def process_changed(self, process: ModuleProcess) -> None:
        """A module's state or its activities changed."""
        for queue in self.activity_listeners:
            queue.put_nowait(None)
        if process.state in ("ready", "failed", "stopped"):
            self.catalog_version += 1
            for queue in self.change_listeners:
                queue.put_nowait(None)
        if process.state == "starting" and process.endpoint is None:
            self.tasks.module_gone(process.module_id)
        if process.endpoint is None:
            for owner, future in self.asked.values():
                if owner is process and not future.done():
                    future.set_exception(ContractError("provider_restarted", f"{process.module_id} disconnected",
                                                       execution="unknown", effects="may_have_occurred"))

    def module_frame(self, process: ModuleProcess, envelope: dict, body: bytes) -> None:
        """Frames only the platform reads: task progress and end, answers to what the platform asked."""
        if envelope["t"] == "activity_progress":
            for queue in self.activity_listeners:
                queue.put_nowait(None)
        elif envelope["t"] == "done":
            asker, future = self.asked.get(envelope.get("id"), (None, None))
            if asker is process and not future.done():
                if "err" in envelope:
                    future.set_exception(ContractError.from_dict(envelope["err"]))
                else:
                    future.set_result(wire.decode(body) if body else None)
        elif envelope["t"] == "task_progress":
            self.tasks.progress(process.module_id, envelope["task"], wire.decode(body))
        else:
            self.tasks.end(process.module_id, envelope["task"], wire.decode(body) if body else None, envelope.get("err"))

    def cancel_task(self, module_id: str, task_id: str) -> None:
        process = self.supervisor.processes.get(module_id)
        if process is not None and process.endpoint is not None:
            process.endpoint.deliver({"t": "cancel", "id": task_id})

    # ── workspaces ───────────────────────────────────────────────────────

    def activities(self, workspace_id: str) -> list[dict]:
        return [{"module": process.module_id, **{key: value for key, value in activity.items() if key != "workspaceId"}}
                for process in self.supervisor.processes.values()
                for activity in process.activities.values() if activity["workspaceId"] == workspace_id]

    async def activity_changes(self):
        queue: asyncio.Queue = asyncio.Queue()
        self.activity_listeners.add(queue)
        try:
            while True:
                yield {}  # Read current activities on every subscription, including a reconnected window's.
                await queue.get()
                while not queue.empty():
                    queue.get_nowait()
        finally:
            self.activity_listeners.discard(queue)

    async def _ask_module(self, process: ModuleProcess, envelope: dict, seconds: float):
        """Ask a running module to perform one declared hook or stop its work, and await its reply."""
        if process.endpoint is None:
            raise ContractError("unavailable", f"{process.module_id} is not running")
        request_id = f"s{secrets.token_hex(6)}"
        future = asyncio.get_running_loop().create_future()
        self.asked[request_id] = (process, future)
        process.endpoint.deliver({**envelope, "id": request_id})
        try:
            return await asyncio.wait_for(future, seconds)
        finally:
            self.asked.pop(request_id, None)

    async def _stop_in(self, process: ModuleProcess, envelope: dict, what: str) -> None:
        """Have a module stop work (it answers once its tasks and process trees ended). One that does not confirm
        in time is restarted, its process tree ending with it: nothing asked to stop keeps running."""
        if process.endpoint is None:
            return
        try:
            await self._ask_module(process, envelope, STOP_SECONDS)
        except (TimeoutError, ContractError):
            log.error("%s did not stop %s in %.0f s; restarting it (a bug to fix)", process.module_id, what,
                      STOP_SECONDS)
            exited, job = process.process, process.job
            await process.restart()
            if exited is not None:
                await exited.wait()
            if job is not None:
                await job.ended()

    async def stop_activities(self, workspace_id: str, chosen: list[tuple[str, str]]) -> None:
        """Stop some of a workspace's activities, each ``(module, id)`` as activity.list gave it, by the module
        running it (two modules may number their activities alike)."""
        self.workspaces.require(workspace_id)
        owners: dict[str, list[str]] = {}
        for activity in self.activities(workspace_id):
            if (activity["module"], activity["id"]) in chosen:
                owners.setdefault(activity["module"], []).append(activity["id"])
        await asyncio.gather(*(self._stop_in(self.supervisor.processes[module], {"t": "stop_activities", "ids": chosen},
                                             f"work of workspace {workspace_id}")
                               for module, chosen in owners.items()))

    def _workspace_deleters(self) -> list[ModuleProcess]:
        owners = []
        for module_id, item in self.catalog.modules.items():
            if item.contract.get("deleteWorkspace"):
                process = self.supervisor.processes.get(module_id)
                if process is None or process.state != "ready":
                    raise ContractError("unavailable", f"Start {item.manifest['name']} to delete this workspace.",
                                        {"module": module_id})
                owners.append(process)
        return owners

    async def deletion_preview(self, workspace_id: str) -> list[str]:
        if workspace_id not in self.workspaces.opened:
            await self.workspaces.saved_of(workspace_id)
        items = []
        for process in self._workspace_deleters():
            items.extend(await self._ask_module(process, {"t": "delete_workspace", "ws": workspace_id,
                                                         "preview": True}, COPY_SECONDS))
        return [*items, *await self.workspaces.folder_preview(workspace_id)]

    async def _stop_workspace_work(self, workspace_id: str) -> None:
        owners = {self.supervisor.processes[a["module"]] for a in self.activities(workspace_id)}
        await asyncio.gather(*(self._stop_in(process, {"t": "stop_workspace", "ws": workspace_id},
                                             f"the work of workspace {workspace_id}") for process in owners))

    async def delete_workspace_contents(self, workspace_id: str) -> None:
        owners = self._workspace_deleters()
        await self._stop_workspace_work(workspace_id)
        for process in owners:
            await self._ask_module(process, {"t": "delete_workspace", "ws": workspace_id, "preview": False},
                                   COPY_SECONDS)
        await self.settings.delete_workspace(workspace_id)

    async def close_workspace(self, workspace_id: str) -> None:
        """Stop everything running for the workspace, then close it: nothing keeps running for a closed workspace."""
        record = self.workspaces.require(workspace_id)
        self.workspaces.closing.add(workspace_id)
        try:
            if record["snapshotId"] is None:
                await self.delete_workspace_contents(workspace_id)
                await self.workspaces.discard_folder(workspace_id)
            else:
                await self._stop_workspace_work(workspace_id)
        except BaseException:
            self.workspaces.closing.discard(workspace_id)
            raise
        await self.workspaces.closed(workspace_id)

    async def copy_workspace(self, workspace_id: str, name: str | None) -> dict:
        """Copy a saved workspace, open or not: its settings, its folder and what each module keeps for it go to a new
        workspace, saved with the same layout under a new name. The copy is not opened."""
        source = await self.workspaces.saved_of(workspace_id)
        for module_id in sorted(panel_modules(source["workspace"])):
            process = self.supervisor.processes.get(module_id)
            if process is None or process.state not in ("starting", "ready"):
                item = self.catalog.modules.get(module_id)
                name = item.manifest["name"] if item else module_id
                why = "is not installed" if item is None else "is turned off" if not item.enabled else \
                    "could not start" if process is not None and process.state == "failed" else "is not running"
                raise ContractError("unavailable", f"{name} {why}, so its panels' content cannot be copied with the "
                                    f"workspace. Start {name}, then copy again.", {"module": module_id})
        return await finish_before_cancel(self._copy_contents(source, name))

    async def _copy_contents(self, source: dict, name: str | None) -> dict:
        workspace_id, copy_id = source["workspaceId"], uuid4().hex
        async with self.workspaces.reserve_copy(source, copy_id, name) as name:
            participants = [process for process in self.supervisor.processes.values()
                            if process.state in ("starting", "ready")]
            attempted = []
            try:
                await self.workspaces.copy_folder(workspace_id, copy_id, name)
                await self.settings.copy_workspace(workspace_id, copy_id)
                attempted = participants
                outcomes = await asyncio.gather(*(self._copy_in(process, workspace_id, copy_id)
                                                  for process in participants), return_exceptions=True)
                for outcome in outcomes:
                    if isinstance(outcome, BaseException):
                        raise outcome
                saved = await self.workspaces.save({"name": name, "workspace": source["workspace"],
                                                    "summary": source["summary"]}, copy_id)
                copy = {**saved, "name": name}
            except BaseException:
                try:
                    owners = [p for p in attempted if self.catalog.get(p.module_id).contract.get("deleteWorkspace")]
                    await asyncio.gather(*(self._ask_module(p, {"t": "delete_workspace", "ws": copy_id,
                                                                "preview": False}, COPY_SECONDS) for p in owners))
                finally:
                    try:
                        await self.settings.delete_workspace(copy_id)
                    finally:
                        await self.workspaces.discard_folder(copy_id, rollback_copy=True)
                raise
        self.workspaces.publish({"kind": "copied", "workspaceId": copy_id, "sourceWorkspaceId": workspace_id})
        return copy

    async def _copy_in(self, process: ModuleProcess, source: str, copy: str) -> None:
        """A module copies what it keeps for the workspace; one still starting does so once it is ready."""
        try:
            await asyncio.wait_for(process.ready_event.wait(), COPY_SECONDS)
        except TimeoutError as exc:
            raise ContractError("unavailable", f"{process.module_id} is not running, so the workspace cannot be "
                                               "copied with its content") from exc
        await self._ask_module(process, {"t": "copy_workspace", "from_ws": source, "ws": copy}, COPY_SECONDS)


    # ── the window ───────────────────────────────────────────────────────

    async def window_call(self, caller: dict, action: str, value) -> Any:
        """Run one of the window's own actions (panels, layout) in the window showing the caller's workspace."""
        workspace = caller.get("workspaceId")
        session = self.workspaces.window_of(workspace) if workspace else caller.get("session")
        endpoint = self.sessions.get(session) if session else None
        if endpoint is None:
            raise ContractError("ui_required", "No window shows this workspace")
        return await self.internal.call(caller, f"platform.{action}", value, endpoint=endpoint)

    # ── data kept for modules ────────────────────────────────────────────

    def directories(self, module_id: str) -> dict:
        data = self.config.data_dir / "modules" / module_id
        user = self.config.user_dir / "module-files" / module_id
        data.mkdir(parents=True, exist_ok=True)
        user.mkdir(parents=True, exist_ok=True)
        environments = self.config.data_dir / "python-environments"
        environments.mkdir(parents=True, exist_ok=True)
        return {"dataDir": str(data), "userDir": str(user), "environmentsDir": str(environments),
                "modulesDir": str(self.catalog.installed_root)}

    async def secret(self, caller: dict, key: str) -> str | None:
        return await asyncio.to_thread(self.secrets.read, self.module_backend(caller), key)

    async def set_secret(self, caller: dict, key: str, value: str | None) -> None:
        """Keys are entered by the user in the module's own page, which hands them to its backend."""
        await asyncio.to_thread(self.secrets.write, self.module_backend(caller), key, value)

    async def secret_configured(self, caller: dict, key: str) -> bool:
        return await asyncio.to_thread(self.secrets.read, self.module_backend(caller), key) is not None


    def _navigation(self, module_id: str, caller: dict, navigation: dict | None) -> None:
        if navigation is not None and (not navigation["panel"].startswith(module_id + ".")
                                       or navigation["workspaceId"] != caller.get("workspaceId")):
            raise ContractError("permission_denied", "A notification leads to the sender's own panel in its workspace")

    async def send_notification(self, caller: dict, value: dict) -> dict:
        module_id = self.calling_module(caller)
        self._navigation(module_id, caller, value.get("navigation"))
        return await self.notifications.send(module_id, value)

    async def toast(self, caller: dict, value: dict) -> None:
        module_id = self.calling_module(caller)
        navigation = value.get("navigation")
        self._navigation(module_id, caller, navigation)
        activate = None
        if navigation is not None:
            loop, event = asyncio.get_running_loop(), {"id": secrets.token_hex(8), "navigation": navigation}

            def activate():
                loop.call_soon_threadsafe(lambda: [queue.put_nowait(event) for queue in self.click_listeners])
        await self.toasts.show(value["title"], value["message"], activate=activate)

    async def toast_clicks(self):
        queue: asyncio.Queue = asyncio.Queue()
        self.click_listeners.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self.click_listeners.discard(queue)

__all__ = ["GLOBAL", "Host", "HostConfig"]
