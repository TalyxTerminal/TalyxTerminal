"""A module's own process: connects to the Host, serves the module's declarations, calls others.

The Host starts ``python -m talyx_sdk.process`` with the module's package in the environment. Everything the module
does with the platform goes through the ``Context`` handed to its ``on_start`` functions and the ``Call`` handed to
each handler.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import itertools
import logging
import os
import sys
import traceback
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from types import EllipsisType
from typing import Any, is_typeddict

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from . import memory, stall, wire
from .declare import ActionSpec, Module, StreamSpec
from .errors import ContractError

log = logging.getLogger("talyx.module")
GLOBAL = "global"
# Who made the call being handled: work registered while handling it (and in tasks it starts) is theirs.
_CALLER: ContextVar[Caller | None] = ContextVar("talyx_caller", default=None)
_CALL_ID: ContextVar[str | None] = ContextVar("talyx_call_id", default=None)
# The long task (``task=True``) being handled: background work registered while handling it is part of it.
_TASK_ACTIVITY: ContextVar[Activity | None] = ContextVar("talyx_task_activity", default=None)


@dataclass(frozen=True)
class Caller:
    """Who is calling, stamped by the Host where the call entered TALYX; nobody along the way can change it.

    ``origin`` is ``user`` (the window), ``ai`` (an AI client inside TALYX or an external one), ``module`` (another
    module's backend) or ``code`` (a running process with a run token)."""

    module: str | None
    workspace_id: str | None
    origin: str
    ai: dict | None = None
    run: dict | None = None

    @classmethod
    def of(cls, value: dict) -> Caller:
        return cls(value.get("module"), value.get("workspaceId"), value["origin"], value.get("ai"), value.get("run"))


class Call:
    """One handler invocation."""

    def __init__(self, process: ModuleProcess, caller: Caller, request_id: str, task_id: str | None = None):
        self.process, self.caller, self.request_id, self.task_id = process, caller, request_id, task_id

    @property
    def workspace_id(self) -> str | None:
        return self.caller.workspace_id

    @property
    def call_id(self) -> str:
        return self.request_id

    def progress(self, value: Any) -> None:
        """Report progress of a long task (a small JSON value)."""
        if self.task_id is not None:
            self.process.send({"t": "task_progress", "task": self.task_id}, wire.encode(value))
            activity = _TASK_ACTIVITY.get()
            if activity is not None:
                activity.progress(value)

    async def call(self, name: str, input: Any, *, workspace: str | None = None) -> Any:
        """Call another capability on behalf of this module, in this call's workspace unless told otherwise."""
        return await self.process.ctx.call(name, input, workspace=workspace or self.workspace_id)


class Codec:
    """One declared type on the wire, built once: ``parse`` what arrives (refused as ``invalid_input``), ``check``
    what goes out (refused as ``invalid_output``), ``accept`` an answer that came back (``invalid_output`` of the
    one who sent it)."""

    def __init__(self, annotation: Any):
        # Floats directly in the declared type (``float``, ``list[float]``…) refuse NaN like those in a Model do.
        own_config = isinstance(annotation, type) and (
            issubclass(annotation, BaseModel) or dataclasses.is_dataclass(annotation) or is_typeddict(annotation))
        self.adapter = TypeAdapter(annotation) if own_config else TypeAdapter(
            annotation, config=ConfigDict(allow_inf_nan=False))
        self.finite = _refuses_non_finite(self.adapter.core_schema)

    def parse(self, body: bytes) -> Any:
        return self.adapter.validate_json(body or b"null")

    def check(self, value: Any) -> bytes:
        """The bytes that go out, which are the value as it is: it goes out only when they are, strictly, of the
        declared type. Nothing is converted: a ``"7"`` for an ``int`` is refused, not sent as 7 or as "7".

        A value of exactly the declared Python types passes at once (a page of columns does). Otherwise its JSON is
        checked: a date or an enum value written as the string the declaration's JSON form is passes there."""
        try:
            self.adapter.validate_python(value, strict=True)
        except ValidationError:
            data = wire.encode(_plain(value))
            try:
                self.adapter.validate_json(data, strict=True)
            except ValidationError as exc:
                raise ContractError("invalid_output", _short(exc), execution="failed") from exc
            return data
        return wire.encode(_plain(value), checked=self.finite)

    def accept(self, value: Any) -> Any:
        try:
            self.adapter.validate_python(value, strict=True)
        except ValidationError:
            try:
                self.adapter.validate_json(wire.encode(value), strict=True)
            except ValidationError as exc:
                raise ContractError("invalid_output", f"The answer does not match the declaration: {_short(exc)}",
                                    execution="unknown", effects="may_have_occurred") from exc
        return value


def _plain(value: Any) -> Any:
    """Handlers may return pydantic models; the wire carries their JSON form."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_unset=True, by_alias=True)
    return value


def _refuses_non_finite(schema: dict) -> bool:
    """True when validating against this core schema has already refused every NaN and infinity: no value of an
    undeclared type (``Any``, a bare ``dict``) and no float under a config that allows them."""
    stack: list[tuple[Any, bool]] = [(schema, False)]
    while stack:
        item, allowed = stack.pop()
        if isinstance(item, dict):
            kind = item.get("type")
            if kind == "any" or (kind == "float" and item.get("allow_inf_nan", allowed)):
                return False
            if kind in ("model", "dataclass", "typed-dict") and "config" in item:
                allowed = item["config"].get("allow_inf_nan", True)
            stack.extend((value, allowed) for key, value in item.items() if key not in ("config", "metadata", "cls"))
        elif isinstance(item, list | tuple):
            stack.extend((value, allowed) for value in item)
    return True


def _short(exc: ValidationError) -> str:
    errors = exc.errors(include_url=False)[:3]
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in errors)


class Subscription:
    """An open stream from another module (or the platform). Iterate it; ``close()`` ends it.

    When the provider's process restarts, the subscription is opened again from the last cursor it saw, so a
    consumer (a watcher, say) keeps receiving without doing anything."""

    def __init__(self, process: ModuleProcess, name: str, params: Any, workspace: str | None, cursor: Any,
                 item: Codec | None = None):
        self.process, self.name, self.params, self.workspace, self.cursor = process, name, params, workspace, cursor
        self.item = item
        self.queue: asyncio.Queue = asyncio.Queue()
        self.id: str | None = None
        self.closed = False

    async def open(self) -> None:
        self.id = self.process.next_id()
        self.process.subscriptions[self.id] = self
        envelope = {"t": "sub", "sub": self.id, "cap": self.name, "ws": self.workspace}
        if self.cursor is not None:
            envelope["cursor"] = self.cursor
        try:
            await self.process.request(envelope, wire.encode(self.params))
        except BaseException:
            await self.close()
            raise

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._items()

    async def _items(self):
        while True:
            kind, value, cursor = await self.queue.get()
            if kind == "item":
                if cursor is not None:
                    self.cursor = cursor
                yield value if self.item is None else self.item.accept(value)
                continue
            if kind == "end" and value is not None and value.code == "provider_restarted" and not self.closed:
                await self.open()     # the Host holds it until the provider is ready again
                continue
            if value is not None:
                raise value
            return

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.id in self.process.subscriptions:
            del self.process.subscriptions[self.id]
            self.process.send({"t": "unsub", "sub": self.id})
        self.queue.put_nowait(("end", None, None))


class Platform:
    """The platform's services, as this module sees them."""

    def __init__(self, ctx: Context):
        self.ctx = ctx

    async def storage(self) -> dict:
        """``{"dataDir", "userDir", "environmentsDir", "modulesDir"}``: this module's data directory, its folder
        among the user's files, the Python environments all modules share (``talyx_sdk.uv``), and the folder of the
        modules the user installs or writes."""
        return await self.ctx.call("platform.storage.directories", {})

    async def secret(self, key: str) -> str | None:
        value = await self.ctx.call("platform.secrets.get", {"key": key})
        return value["value"]

    async def set_secret(self, key: str, value: str | None) -> None:
        await self.ctx.call("platform.secrets.set", {"key": key, "value": value})

    async def secret_configured(self, key: str) -> bool:
        return (await self.ctx.call("platform.secrets.configured", {"key": key}))["configured"]

    async def setting(self, key: str, *, workspace: str) -> Any:
        """A setting of this module as it applies in ``workspace``, or ``GLOBAL``. There is no implicit default:
        background work of a workspace reads that workspace's value; work for all of TALYX reads ``GLOBAL``."""
        value = await self.ctx.call("platform.settings.read", {"module": self.ctx.module_id, "keys": [key],
                                                                "workspaceId": workspace})
        return value["values"][key]["value"]

    async def workspace_folder(self, workspace: str) -> str:
        return (await self.ctx.call("platform.workspace.folder", {"workspaceId": workspace}))["path"]

    async def open_workspaces(self) -> list[str]:
        return [item["workspaceId"] for item in await self.ctx.call("platform.workspace.list_open", {})]


class Activity:
    """Background work of a workspace: asyncio tasks and process trees the platform stops when it closes."""

    def __init__(self, process: ModuleProcess, workspace_id: str, title: str, origin: str, call_id: str | None = None):
        self.process, self.workspace_id, self.title, self.origin = process, workspace_id, title, origin
        self.call_id = call_id
        self.id = process.next_id()
        self.tasks: set[asyncio.Task] = set()
        self.jobs: list = []
        self.outcome = "completed"
        self.summary = None

    def add_task(self, task: asyncio.Task) -> asyncio.Task:
        self.tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def add_job(self, job) -> None:
        self.jobs.append(job)

    def _done(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if self.outcome == "completed":
            if task.cancelled():
                self.outcome = "stopped"
            elif task.exception() is not None:
                self.outcome = "failed"
        if not self.tasks and not any(job.alive() for job in self.jobs):
            self.finish()

    def progress(self, value: Any) -> None:
        self.process.send({"t": "activity_progress", "activity": self.id}, wire.encode(value))

    def set_result(self, summary: Any) -> None:
        """Attach the result delivered when all registered work ends."""
        self.summary = summary

    def finish(self, outcome: str | None = None) -> None:
        if self.process.activities.pop(self.id, None) is not None:
            self.process.send({"t": "activity_end", "activity": self.id, "outcome": outcome or self.outcome},
                              wire.encode(self.summary))

    async def stop(self) -> None:
        self.outcome = "stopped"
        for job in self.jobs:
            job.terminate()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, *(job.ended() for job in self.jobs), return_exceptions=True)
        self.finish()


class Context:
    """What a module's ``on_start`` functions get: calling others, subscribing, publishing, the platform."""

    def __init__(self, process: ModuleProcess):
        self.process = process
        self.module_id = process.module.id
        self.platform = Platform(self)

    @property
    def call_id(self) -> str | None:
        """The Host's durable ID of the action being handled, also available in work it starts."""
        return _CALL_ID.get()

    async def call_with_id(self, name: str, input: Any, *, workspace: str | None = None) -> tuple[Any, str]:
        """Call a public action and keep its Host ID, e.g. to undo a child call with its parent."""
        return await self.process.request({"t": "call", "cap": name, "ws": workspace}, wire.encode(input),
                                          with_id=True)

    async def call(self, name: str, input: Any, *, workspace: str | None = None) -> Any:
        """Call a public action of another module or the platform. Calls made while that module is still
        starting wait for it."""
        return await self.process.request({"t": "call", "cap": name, "ws": workspace}, wire.encode(input))

    async def subscribe(self, name: str, params: Any = None, *, workspace: str | None = None,
                        cursor: Any = None) -> Subscription:
        subscription = Subscription(self.process, name, {} if params is None else params, workspace, cursor)
        await subscription.open()
        return subscription

    async def publish(self, event: str, payload: Any) -> None:
        spec = self.process.module.events[event]
        body = self.process.codec(spec.payload).check(payload)
        self.process.send({"t": "event", "cap": f"{self.module_id}.{event}"}, body)

    def activity(self, workspace_id: str, title: str, *, origin: str | None = None, call_id: str | None | EllipsisType = ...,
                 open_target: dict | None = None) -> Activity:
        """Register background work under ``workspace_id``; add its tasks and process jobs to it. ``title`` is what
        the user reads before closing the workspace. The work is the caller's whose call is being handled (an AI's
        when an AI asked for it); the module's own otherwise. A module that is itself an AI client says ``origin``
        "ai" for the work its AI does, whoever asked.

        Every call of a long task (``task=True``) is registered by the SDK under the action's name; work registered
        while handling one, for the same workspace, is added to it, so one thing someone started is one entry."""
        # A restored job may explicitly have no known initiating call. Only omission inherits the live call.
        owner = self.call_id if call_id is ... else call_id
        current = _TASK_ACTIVITY.get()
        if current is not None and current.workspace_id == workspace_id and current.call_id == owner and current.id in self.process.activities:
            return current
        caller = _CALLER.get()
        activity = Activity(self.process, workspace_id, title, origin or (caller.origin if caller else "module"), owner)
        self.process.activities[activity.id] = activity
        self.process.send({"t": "activity", "activity": activity.id, "ws": workspace_id},
                          wire.encode({"title": title, "origin": activity.origin, "callId": owner,
                                       "openTarget": open_target, "progress": None}))
        return activity

    async def run_token(self, workspace_id: str | None, context: Any = None, *, conversation: str | None = None) -> dict:
        """A token for a process this module starts, so its calls to TALYX are recorded as ``code`` from this
        module and workspace; ``context`` reaches the called handlers as it is. ``conversation``: the AI
        conversation the code acts for, which may list and undo its changes."""
        return await self.call("platform.runs.start", {"workspaceId": workspace_id, "context": context,
                                                       "conversation": conversation})

    async def extensions(self, point: str) -> list[dict]:
        """Implementations of one of this module's extension points: ``{moduleId, id, descriptor}``."""
        return await self.call("platform.catalog.implementations", {"point": f"{self.module_id}.{point}"})

    def watch_extensions(self, point: str, callback: Callable) -> None:
        self.process.extension_watchers.setdefault(f"{self.module_id}.{point}", []).append(callback)

    async def call_extension(self, point: str, module_id: str, id: str, name: str, input: Any, *,
                             workspace: str | None = None) -> Any:
        """Call one implementation of this module's extension point. What goes in and what comes back are checked
        against the point's declaration, whoever implements it."""
        full = f"{self.module_id}.{point}"
        input_type, output_type, _ = self.process.module.extension_points[point].actions[name]
        body = self.process.codec(input_type).check(input)
        if module_id == self.module_id:
            handler = self.process.module.implementations[full][id]["actions"][name]
            result = await handler(wire.decode(body), None)
        else:
            result = await self.process.request({"t": "call", "cap": f"{full}:{module_id}:{id}.{name}", "ws": workspace}, body)
        return self.process.codec(output_type).accept(_plain(result))

    async def subscribe_extension(self, point: str, module_id: str, id: str, name: str, params: Any) -> Subscription:
        params_type, item_type, _ = self.process.module.extension_points[point].streams[name]
        self.process.codec(params_type).check(params)
        subscription = Subscription(self.process, f"{self.module_id}.{point}:{module_id}:{id}.{name}", params, None,
                                    None, self.process.codec(item_type))
        await subscription.open()
        return subscription


class ModuleProcess:
    def __init__(self, module: Module, reader, writer):
        self.module, self.reader, self.writer = module, reader, writer
        self.ctx = Context(self)
        self.ids = itertools.count(1)
        self.pending: dict[str, asyncio.Future] = {}
        self.running: dict[str, asyncio.Task] = {}
        self.streams: dict[str, asyncio.Task] = {}
        self.subscriptions: dict[str, Subscription] = {}
        self.activities: dict[str, Activity] = {}
        self.extension_watchers: dict[str, list[Callable]] = {}
        self.codecs: dict[int, Codec] = {}

    def next_id(self) -> str:
        return f"m{next(self.ids)}"

    def codec(self, annotation: Any) -> Codec:
        key = id(annotation)
        if key not in self.codecs:
            self.codecs[key] = Codec(annotation)
        return self.codecs[key]

    def send(self, envelope: dict, body: bytes = b"") -> None:
        self.writer.write(wire.frame(envelope, body))

    async def request(self, envelope: dict, body: bytes, *, with_id: bool = False) -> Any:
        request_id = envelope.get("id") or self.next_id()
        envelope["id"] = request_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            self.send(envelope, body)
            result, call_id = await future
            return (result, call_id) if with_id else result
        except asyncio.CancelledError:
            if envelope["t"] == "call" and request_id in self.pending:
                self.send({"t": "cancel", "id": request_id})
            raise
        finally:
            self.pending.pop(request_id, None)


    # ── serving ──────────────────────────────────────────────────────────

    async def serve(self) -> None:
        while True:
            try:
                envelope, body = await wire.read_frame(self.reader)
            except (asyncio.IncompleteReadError, ConnectionError):
                log.info("The Host closed the connection")
                os._exit(0)
            kind = envelope["t"]
            if kind == "call":
                self.running[envelope["id"]] = asyncio.create_task(self._call(envelope, body))
            elif kind == "sub":
                self.streams[envelope["sub"]] = asyncio.create_task(self._stream(envelope, body))
            elif kind == "unsub":
                task = self.streams.pop(envelope["sub"], None)
                if task is not None:
                    task.cancel()
            elif kind == "cancel":
                task = self.running.get(envelope["id"])
                if task is not None:
                    task.cancel()
            elif kind in ("result", "error"):
                future = self.pending.get(envelope["id"])
                if future is not None and not future.done():
                    if kind == "result":
                        future.set_result((wire.decode(body) if body else None, envelope.get("callId")))
                    else:
                        future.set_exception(ContractError.from_dict(envelope["err"]))
            elif kind == "item":
                subscription = self.subscriptions.get(envelope["sub"])
                if subscription is not None:
                    subscription.queue.put_nowait(("item", wire.decode(body), envelope.get("cursor")))
            elif kind == "end":
                subscription = self.subscriptions.pop(envelope["sub"], None)
                if subscription is not None:
                    error = ContractError.from_dict(envelope["err"]) if envelope.get("err") else None
                    subscription.queue.put_nowait(("end", error, None))
            elif kind == "stop_workspace":
                asyncio.create_task(self._stop_workspace(envelope))
            elif kind == "stop_activities":
                asyncio.create_task(self._stop_activities(envelope))
            elif kind == "copy_workspace":
                asyncio.create_task(self._copy_workspace(envelope))
            elif kind in ("list_changes", "revert_changes"):
                asyncio.create_task(self._changes(envelope))
            elif kind == "delete_workspace":
                asyncio.create_task(self._delete_workspace(envelope))
            elif kind == "extensions_changed":
                for callback in self.extension_watchers.get(envelope["point"], []):
                    result = callback(wire.decode(body))
                    if asyncio.iscoroutine(result):
                        await result

    def _find_action(self, name: str) -> tuple[Callable, Any, Any, ActionSpec | None]:
        spec = self.module.actions.get(name.removeprefix(self.module.id + "."))
        if spec is not None and ":" not in name:
            return spec.handler, spec.input, spec.output, spec
        point, _, rest = name.partition(":")
        implementation, _, method = rest.rpartition(".")
        handler = self.module.implementations[point][implementation.split(":", 1)[1]]["actions"][method]
        return handler, None, None, None

    async def _call(self, envelope: dict, body: bytes) -> None:
        request_id = envelope["id"]
        try:
            handler, input_type, output_type, spec = self._find_action(envelope["cap"])
            caller = Caller.of(envelope["from"])
            _CALLER.set(caller)
            _CALL_ID.set(request_id)
            value = self.codec(input_type).parse(body) if input_type is not None else wire.decode(body)
            if spec is not None and spec.task:
                task_id = (await self.request({"t": "call", "cap": "platform.tasks.start", "ws": caller.workspace_id},
                                              wire.encode({"action": f"{self.module.id}.{spec.name}"})))["taskId"]
                asyncio.create_task(self._task(task_id, handler, value, Call(self, caller, request_id, task_id),
                                               output_type, caller, spec))
                self.send({"t": "result", "id": request_id}, wire.encode({"taskId": task_id}))
                return
            result = await handler(value, Call(self, caller, request_id))
            self._send_output({"t": "result", "id": request_id}, result, output_type, spec)
        except asyncio.CancelledError:
            self.send({"t": "error", "id": request_id, "err": ContractError(
                "cancelled", "Cancelled", execution="unknown", effects="may_have_occurred").to_dict()})
        except Exception as exc:  # noqa: BLE001 - every failure crosses the wire as the one error format
            self.send({"t": "error", "id": request_id, "err": error_value(exc, invalid_input=True)})
        finally:
            self.running.pop(request_id, None)

    def _send_output(self, envelope, result, output_type, spec):
        try:
            data = self.codec(output_type).check(result) if output_type is not None else wire.encode(result)
            self.send(envelope, data)
        except ContractError as exc:
            if spec is None or spec.effects != ("read",):
                exc.execution, exc.effects = "unknown", "may_have_occurred"
            raise

    async def _task(self, task_id, handler, value, call, output_type, caller, spec: ActionSpec) -> None:
        task = asyncio.current_task()
        self.running[task_id] = task
        if caller.workspace_id:
            activity = self.ctx.activity(caller.workspace_id, spec.title)
            activity.add_task(task)
            _TASK_ACTIVITY.set(activity)
        try:
            result = await handler(value, call)
            self._send_output({"t": "task_end", "task": task_id}, result, output_type, spec)
        except asyncio.CancelledError:
            if caller.workspace_id:
                activity.outcome = "stopped"
            self.send({"t": "task_end", "task": task_id, "err": ContractError(
                "cancelled", "Cancelled", execution="failed", effects="may_have_occurred").to_dict()})
        except Exception as exc:  # noqa: BLE001
            if caller.workspace_id:
                activity.outcome = "failed"
            self.send({"t": "task_end", "task": task_id, "err": error_value(exc)})
        finally:
            self.running.pop(task_id, None)

    async def _stream(self, envelope: dict, body: bytes) -> None:
        subscription_id = envelope["sub"]
        name = envelope["cap"]
        error = None
        try:
            spec: StreamSpec | None = None if ":" in name else self.module.streams.get(
                name.removeprefix(self.module.id + "."))
            if spec is not None:
                params = self.codec(spec.params).parse(body)
                item_adapter = self.codec(spec.item)
                handler = spec.handler
            else:
                point, _, rest = name.partition(":")
                implementation, _, method = rest.rpartition(".")
                handler = self.module.implementations[point][implementation.split(":", 1)[1]]["streams"][method]
                params, item_adapter = wire.decode(body), None
            call = Call(self, Caller.of(envelope["from"]), subscription_id)
            if spec is not None and spec.cursor:
                items = handler(params, call, envelope.get("cursor"))
            else:
                items = handler(params, call)
            async for item in items:
                cursor = None
                if spec is not None and spec.cursor:
                    item, cursor = item
                data = item_adapter.check(item) if item_adapter is not None else wire.encode(item)
                self.send({"t": "item", "sub": subscription_id, **({"cursor": cursor} if cursor is not None else {})},
                          data)
                if self.writer.transport.get_write_buffer_size() > 4 * 1024 * 1024:
                    await self.writer.drain()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            error = error_value(exc, invalid_input=True)
        finally:
            self.streams.pop(subscription_id, None)
        self.send({"t": "end", "sub": subscription_id, **({"err": error} if error else {})})

    async def _stop_workspace(self, envelope: dict) -> None:
        stopping = [a for a in list(self.activities.values()) if a.workspace_id == envelope["ws"]]
        await asyncio.gather(*(activity.stop() for activity in stopping))
        self.send({"t": "done", "id": envelope["id"]})

    async def _changes(self, envelope: dict) -> None:
        try:
            if envelope["t"] == "list_changes":
                result = await self.module.change_handlers.list(envelope["callIds"], envelope["runId"])
            else:
                result = await self.module.change_handlers.revert(envelope["callId"], envelope["direction"])
            self.send({"t": "done", "id": envelope["id"]}, wire.encode(result))
        except Exception as exc:  # noqa: BLE001 - a failed module hook answers with the platform error format
            self.send({"t": "done", "id": envelope["id"], "err": error_value(exc)})

    async def _stop_activities(self, envelope: dict) -> None:
        stopping = [self.activities[i] for i in envelope["ids"] if i in self.activities]
        await asyncio.gather(*(activity.stop() for activity in stopping))
        self.send({"t": "done", "id": envelope["id"]})

    async def _delete_workspace(self, envelope: dict) -> None:
        try:
            items = await self.module.workspace_deleter(envelope["ws"], preview=envelope["preview"])
            self.send({"t": "done", "id": envelope["id"]}, self.codec(list[str]).check(items))
        except Exception as exc:  # noqa: BLE001 - return the module's failure across its pipe
            self.send({"t": "done", "id": envelope["id"], "err": error_value(exc)})

    async def _copy_workspace(self, envelope: dict) -> None:
        try:
            for copier in self.module.copiers:
                await copier(envelope["from_ws"], envelope["ws"])
            self.send({"t": "done", "id": envelope["id"]})
        except Exception as exc:  # noqa: BLE001
            self.send({"t": "done", "id": envelope["id"], "err": error_value(exc)})


def error_value(exc: BaseException, *, invalid_input: bool = False) -> dict:
    if isinstance(exc, ContractError):
        return exc.to_dict()
    if isinstance(exc, ValidationError) and invalid_input:
        return ContractError("invalid_input", _short(exc)).to_dict()
    log.error("Handler failed\n%s", "".join(traceback.format_exception(exc)))
    return ContractError("handler_error", f"{type(exc).__name__}: {exc}"[:2000], execution="failed",
                         effects="may_have_occurred").to_dict()


async def _run_routes(module: Module, ctx: Context) -> dict | None:
    if module.routes is None:
        return None
    import socket

    # Building the app (the module's routers, the web framework's first import) and loading the server take
    # hundreds of milliseconds: a worker thread does it while the loop keeps serving.
    server, secret = await asyncio.to_thread(_route_server, module, ctx)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0.01)
    return {"port": port, "secret": secret}


def _route_server(module: Module, ctx: Context):
    import platform
    import secrets as _secrets

    import uvicorn

    # uvicorn asks for the system's name as it starts serving, on the loop; the first answer is a WMI query on Windows
    # (50-80 ms), so it is asked here, and the loop's ask finds it remembered.
    platform.system()
    app = module.routes(ctx)
    secret = _secrets.token_urlsafe(24)

    async def guarded(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope["headers"])
            if headers.get(b"x-talyx-route-secret", b"").decode() != secret:
                if scope["type"] == "http":
                    body = wire.encode({"error": ContractError(
                        "permission_denied", "Private routes are reached through the Host").to_dict()})
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                else:
                    await send({"type": "websocket.close", "code": 4403})
                return
        await app(scope, receive, send)

    # Only the Host connects, on loopback: no per-message compression (deflating a page of bars held the loop 25-70 ms).
    config = uvicorn.Config(guarded, log_level="warning", lifespan="off", ws="websockets-sansio", ws_per_message_deflate=False)
    config.load()
    return uvicorn.Server(config), secret


async def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format=f"%(asctime)s {os.environ['TALYX_MODULE']} %(levelname)s %(name)s: %(message)s")
    package = os.environ["TALYX_PACKAGE"]
    # Modules keep the loop free by doing Python work on worker threads, which hold the GIL: each time the loop wakes
    # it waits for them up to the switch interval. 1 ms instead of 5 keeps that wait under the loop's lag line
    # (measured on startup: a module's per-second p99 45 -> 28 ms).
    sys.setswitchinterval(0.001)
    entry = importlib.import_module(f"{package}.{os.environ['TALYX_ENTRY']}")
    module: Module = entry.module
    host, port = os.environ["TALYX_HOST"].rsplit(":", 1)
    reader, writer = await asyncio.open_connection(host, int(port), limit=2 ** 26)
    wire.tune(writer)
    stall.watch(module.id)
    process = ModuleProcess(module, reader, writer)
    process.send({"t": "hello", "module": module.id, "pid": os.getpid(), "token": os.environ.pop("TALYX_TOKEN")})
    serving = asyncio.create_task(process.serve())
    try:
        for starter in module.starters:
            await starter(process.ctx)
        routes = await _run_routes(module, process.ctx)
        process.send({"t": "ready"}, wire.encode({
            "deleteWorkspace": module.workspace_deleter is not None,
            "actions": sorted(module.actions), "streams": sorted(module.streams), "changes": module.change_handlers is not None,
            "implementations": {point: {key: {"actions": sorted(v["actions"]), "streams": sorted(v["streams"])}
                                        for key, v in items.items()}
                                for point, items in module.implementations.items()},
            "routes": routes}))
        process.collector = memory.settle(lambda: bool(process.running))
    except Exception as exc:  # noqa: BLE001 - a failed start is reported, then the process exits
        process.send({"t": "failed"}, wire.encode(error_value(exc)))
        await writer.drain()
        log.error("Start failed\n%s", "".join(traceback.format_exception(exc)))
        os._exit(3)
    await serving


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
