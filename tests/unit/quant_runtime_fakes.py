"""Quant's services hold ``runtime.Quant`` as ``ctx``. The tests' fakes were written as the Host's parts (``require``,
``actions.call``, ``platform.execution``, ``platform.python_env``, ``events``, ``track``); ``adapt`` gives such a fake
the runtime's surface on top, reading the fake's parts when called, so a test can still swap one part
(``ctx.platform.python_env = ...``) after building the fake. Nothing here starts a process.

- ``call("market.data.read", input)`` goes to ``require("market.data").read(input)`` when the fake provides that
  service, ``platform.<a>.<b>`` to ``platform.<a>.<b>``, anything else to ``actions.call``.
- ``environments`` (synchronous in the runtime) drives the fake's ``python_env`` coroutines to their end; a fake that
  really waits cannot be used there.
- ``processes`` maps to the fake's ``execution`` and ``leases``; a process started inside a workspace's background work
  joins that work as the runtime's does (stopping the work terminates it, through the fake's ``cancel``).
- ``start`` is the runtime's own: background work of the workspace being served is registered as an SDK ``Activity``
  (``fake.sdk.process.activities``), and stopping it cancels its tasks and ends its processes.
- ``hand_over``, ``keep`` and ``let_go`` are the runtime's own, in ``<root>/data`` (only a fake given a root has folders).
"""
from __future__ import annotations

import asyncio
import inspect
import itertools
from pathlib import Path
from types import MethodType, SimpleNamespace

from talyx_sdk.process import Activity

from modules.quant.backend import runtime


class ModuleProcess:
    """What an SDK ``Activity`` needs of the module's process: ids, the activities, frames sent (kept)."""

    def __init__(self):
        self.ids, self.activities, self.sent = itertools.count(1), {}, []
        self.bodies = []
        self.targets = {}

    def next_id(self) -> str:
        return f"m{next(self.ids)}"

    def send(self, envelope, body=b""):
        self.sent.append(envelope)
        self.bodies.append(body)

    def activity(self, workspace_id, title, *, origin=None, call_id=None, open_target=None):
        activity = Activity(self, workspace_id, title, origin or "user")
        self.activities[activity.id] = activity
        self.targets[activity.id] = open_target
        return activity

    async def stop_workspace(self, workspace_id):
        """What the SDK does when the platform closes the workspace."""
        await asyncio.gather(*(a.stop() for a in list(self.activities.values()) if a.workspace_id == workspace_id))


class _Job:
    """A started process's job, as an activity holds it: terminating it ends the process (the fake's ``cancel``)."""

    def __init__(self, processes, lease):
        self.processes, self.lease, self.terminated = processes, lease, False

    def alive(self):
        return not self.terminated

    def terminate(self):
        self.terminated = True
        asyncio.ensure_future(self.processes.cancel(self.lease))

    async def ended(self):
        return None


def drive(value):
    """The result of a coroutine that never really waits (``asyncio.sleep(0, x)``, an ``async def`` with no awaits)."""
    if not inspect.isawaitable(value):
        return value
    coroutine = value.__await__()
    try:
        while True:
            if coroutine.send(None) is not None:
                raise RuntimeError("This fake waits for something; the runtime's environments are synchronous")
    except StopIteration as done:
        return done.value


def _platform(fake):
    return getattr(fake, "platform", SimpleNamespace())


async def _call(fake, name, input=None, *, workspace=None):
    input = {} if input is None else input
    parts = name.split(".")
    require = getattr(fake, "require", None)
    if name in {"market.read_series","market.read_coverage","market.read_research"} and require is not None:
        group, method = {"market.read_series":("market.data","read"),"market.read_coverage":("market.data","coverage"),
                         "market.read_research":("market.research","read")}[name]
        try:
            service = require(group)
        except (KeyError, AttributeError, AssertionError):
            service = None
        if service is not None and hasattr(service,method):
            return await getattr(service,method)(input)
    if parts[0] != "platform" and len(parts) == 3 and require is not None:
        try:
            service = require(".".join(parts[:2]))
        except (KeyError, AttributeError, AssertionError):
            service = None
        if service is not None and hasattr(service, parts[2]):
            return await getattr(service, parts[2])(input)
    if parts[0] == "platform" and len(parts) == 3:
        if name == "platform.settings.read" and not hasattr(_platform(fake), "settings"):
            settings = getattr(fake, "quant_settings", {})
            return {"values": {key: {"value": settings.get(key, True)} for key in input.get("keys", [])}}
        group = getattr(_platform(fake), parts[1], None)
        if group is not None and hasattr(group, parts[2]):
            return await getattr(group, parts[2])(input)
        if parts[1] == "runs":
            return []
    actions = getattr(fake, "actions", None)
    if actions is None:
        raise AssertionError(f"This test's fake does not answer {name}")
    return await actions.call(name, input)


class _Environments:
    def __init__(self, fake):
        self.fake = fake

    def _env(self):
        return _platform(self.fake).python_env

    def submit(self, **intent):
        return drive(self._env().submit(intent))

    def get(self, identity):
        return drive(self._env().get({"preparation_id": identity}))

    def retry(self, identity):
        return drive(self._env().retry({"preparation_id": identity}))

    async def cancel(self, identity):
        return await self._env().cancel({"preparation_id": identity})

    def hold(self, identity, holder):
        acquire = getattr(getattr(_platform(self.fake), "python_env", None), "acquire", None)
        if acquire is not None:
            drive(acquire({"preparation_id": identity, "owner_id": holder}))

    def release(self, identity, holder):
        release = getattr(getattr(_platform(self.fake), "python_env", None), "release", None)
        if release is not None:
            drive(release({"preparation_id": identity, "owner_id": holder}))


class _Processes:
    def __init__(self, fake):
        self.fake = fake

    def _ex(self):
        return _platform(self.fake).execution

    async def start(self, launch):
        lease = await self._ex().start(launch)
        activity = runtime.ACTIVITY.get()
        if activity is not None:
            activity.add_job(_Job(self, lease))
        return lease

    async def status(self, lease):
        return await self._ex().status({"lease": lease})

    async def wait(self, lease):
        return await self._ex().wait({"lease": lease})

    async def send(self, lease, message):
        return await self._ex().send({"lease": lease, "message": message})

    async def receive(self, lease, timeout):
        return await self._ex().receive({"lease": lease, "timeout_ms": int(timeout * 1000)})

    async def calls(self, lease):
        calls = getattr(self._ex(), "channel_calls", None)
        return [] if calls is None else await calls({"lease": lease})

    async def cancel(self, lease):
        cancel = getattr(self._ex(), "cancel", None) or getattr(self._ex(), "stop", None)
        return None if cancel is None else await cancel({"lease": lease})

    async def close(self, lease):
        leases = getattr(_platform(self.fake), "leases", None)
        if leases is not None:
            await leases.close({"lease": lease})

    async def close_all(self):
        return None


def adapt(fake, root=None):
    """``fake`` with the runtime's surface added (and returned); ``root`` holds its user and data folders."""
    platform = _platform(fake)
    local = getattr(getattr(getattr(platform, "storage", None), "local_config", None), "user_dir", None)
    if root is None and local is not None:
        root = Path(local).parent
    # Without a root the fake has no folders: a test that hands over or keeps files gives it one (its tmp_path).
    root = Path(root) if root is not None else Path("no-folder-given-to-this-fake")
    fake.user_dir = Path(local) if local is not None else root / "user"
    fake.data_dir = root / "data"
    fake.files, fake.handoff = fake.data_dir / "files", fake.data_dir / "handoff"
    if root.is_absolute():
        for folder in (fake.files, fake.handoff):
            folder.mkdir(parents=True, exist_ok=True)
    fake.handlers = getattr(fake, "handlers", None) or {}
    fake.environments = _Environments(fake)
    fake.processes = _Processes(fake)

    async def call(name, input=None, *, workspace=None):
        return await _call(fake, name, input, workspace=workspace)

    def publish(event, payload):
        events = getattr(fake, "events", None)
        if events is not None:
            events.publish(event, payload)

    def workspace():
        current = getattr(getattr(_platform(fake), "workspace_activity", None), "current", None)
        return runtime.WORKSPACE.get() or (current() if current is not None else "ws")

    async def developer(name, input):
        return await call(name, input)

    async def setting(key, workspace=None):
        return getattr(fake, "quant_settings", {}).get(key, True)

    if not hasattr(fake, "sdk"):
        process = ModuleProcess()
        fake.sdk = SimpleNamespace(process=process, activity=process.activity)
    fake.call, fake.publish, fake.workspace = call, publish, workspace
    fake.start = MethodType(runtime.Quant.start, fake)
    fake.developer, fake.setting = developer, setting
    async def run_result(identity):
        return {"runId":identity}
    fake.run_result = run_result
    for name in ("hand_over", "keep", "let_go", "sweep_handoff"):
        setattr(fake, name, MethodType(getattr(runtime.Quant, name), fake))
    return fake
