import asyncio
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend import packages as packages_module
from modules.quant.backend.notebook_routes import PIP_MESSAGE, NotebookServices, blocked_install
from tests.unit.quant_runtime_fakes import adapt


def bare(deep=None):
    """A Packages with no state: only what the environment list reads."""
    item = packages_module.UserPackages.__new__(packages_module.UserPackages)
    deep = deep if deep is not None else {"installed": False, "devices": [], "offer": {"variant": "cpu"}}
    if True:
        item.deep_status = lambda: deep

        async def deep_environment():
            return {"interpreterPath": "deep.exe"}
        item.deep_environment = deep_environment
    return item


def test_environments_listed_from_packages():
    assert [e["id"] for e in bare().kernel_environments()] == ["research", "deep"]
    listed = bare({"installed": False, "devices": [{"label": "CUDA, RTX 4090 24 GB"}], "offer": {"variant": "cuda"}}).kernel_environments()
    assert [(e["id"], e["installed"]) for e in listed] == [("research", True), ("deep", False)]
    assert listed[1]["device"] == "CUDA, RTX 4090 24 GB"
    assert bare({"installed": True, "devices": []}).kernel_environments()[1].get("device") is None


def test_deep_not_installed_is_reported_not_installed_here():
    with pytest.raises(ContractError) as caught:
        asyncio.run(bare({"installed": False, "devices": [], "offer": {"variant": "cpu"}}).kernel_environment("deep"))
    assert caught.value.code == "deep_not_installed" and caught.value.details["offer"] == {"variant": "cpu"} if hasattr(caught.value, "details") else True
    assert asyncio.run(bare({"installed": True, "devices": []}).kernel_environment("deep")) == {"interpreterPath": "deep.exe"}
    with pytest.raises(ContractError):
        asyncio.run(bare().kernel_environment("deep"))


class Records:
    def __init__(self):
        self.record = {"id": "n1", "kind": "notebook", "name": "N", "version": 1, "settings": {}}

    def read(self, _id):
        return self.record

    def save(self, record, **changes):
        self.record = {**record, **changes, "version": record["version"] + 1}
        return self.record


class Session:
    def __init__(self, busy):
        self.restarts, self.record, self.cells = 0, None, {"x": 1} if busy else {}

    def check_restart(self):
        if self.restarts:
            raise ContractError("conflict", "The kernel is already starting.")

    async def restart(self):
        self.restarts += 1


def services(deep, session):
    item = NotebookServices.__new__(NotebookServices)
    item.store, item.packages = Records(), bare(deep)
    item.kernels = SimpleNamespace(find=lambda _id: session)
    return item


def test_kernel_environment_switch_restarts(monkeypatch):
    from modules.quant.backend.notebook_routes import handle_notebook_actions
    from tests.unit.quant_public import service_actions
    session = Session(False)
    item = services({"installed": True, "devices": []}, session)
    act = service_actions(monkeypatch, handle_notebook_actions, item)
    assert asyncio.run(act("set_notebook_kernel", {"notebookId":"n1", "environment":"deep"})) == {"objectId":"n1","environment":"deep"}
    assert session.restarts == 1 and session.record["settings"]["kernel"] == {"environment": "deep"}
    with pytest.raises(ContractError, match="already starting"):
        asyncio.run(item.set_kernel("n1", "research"))
    assert item.store.record["settings"]["kernel"] == {"environment": "deep"}
    with pytest.raises(ContractError):
        asyncio.run(services(None, session).set_kernel("n1", "deep"))


def test_deep_install_goes_through_d_action():
    item = services({"installed": False, "devices": [], "offer": {"variant": "xpu"}}, Session(False))
    with pytest.raises(ContractError) as caught:
        asyncio.run(item.set_kernel("n1", "deep"))
    assert caught.value.code == "deep_not_installed" and item.kernels.find("n1").restarts == 0


def test_pip_in_cell_refused():
    for code in ("%pip install x", "!pip install x", "!uv pip install x", "x = 1\n%conda install y"):
        assert blocked_install(code) == PIP_MESSAGE
    assert blocked_install("import pip_audit") is None


def test_an_installation_that_ends_tells_the_notebooks_so_they_do_not_poll():
    """A notebook reads whether its kernel needs a restart when an installation ends (packages_changed) and when its kernel starts, not every 15 s."""
    published = []
    item = packages_module.UserPackages.__new__(packages_module.UserPackages)
    item.task, item.kernels = None, None
    item.ctx = adapt(SimpleNamespace(events=SimpleNamespace(publish=lambda name, payload: published.append((name, payload)))))
    item.status = lambda: None

    async def scenario(failures):
        async def install():
            return failures
        item.install = install
        item.start_install()
        await item.task
    asyncio.run(scenario({}))
    asyncio.run(scenario({"strategy": "uv failed"}))
    assert published == [("packages_changed", {"state": "completed"}), ("packages_changed", {"state": "failed"})]


def test_restart_discards_previous_snapshot_and_waits_for_replacement(tmp_path):
    from modules.quant.backend.kernels import KernelSession

    async def scenario():
        async def cancel(lease):
            return None

        manager = SimpleNamespace(ctx=SimpleNamespace(processes=SimpleNamespace(cancel=cancel)))
        session = KernelSession(manager, "ws", {"id": "n", "name": "N", "directory": str(tmp_path)})
        session.task = asyncio.current_task()
        session.ready.set()
        session.state, session.client = "idle", object()
        session.variables, session.loaded = ["old"], ["old"]
        await session.restart()
        assert session.variables == session.loaded == []
        waiting = asyncio.create_task(session.running_client())
        await asyncio.sleep(0)
        assert not waiting.done()
        session.client = object()
        session.ready.set()
        assert await waiting is session.client

    asyncio.run(scenario())


@pytest.mark.parametrize("started", [False, True])
def test_cancelling_deep_install_stops_the_current_preparation(monkeypatch, started):
    from unittest.mock import AsyncMock

    async def scenario():
        item = packages_module.UserPackages.__new__(packages_module.UserPackages)
        item.deep_task = None
        item.devices = lambda: {"nvidia": [], "intel": [], "cpu": {"cores": 4}}
        item.deep_status = lambda: item.deep_job
        item.ctx = SimpleNamespace(start=asyncio.create_task, environments=SimpleNamespace(cancel=AsyncMock()))
        ready = asyncio.Event()

        async def prepare(ctx, lock, origin, publish):
            publish({"preparationId": "deep-1", "state": "running"})
            ready.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(packages_module, "prepare_lock", prepare)
        item.install_deep("cpu")
        if started:
            await ready.wait()
        assert (await item.cancel_deep())["state"] == "cancelled"
        if started:
            item.ctx.environments.cancel.assert_awaited_once_with("deep-1")
        else:
            item.ctx.environments.cancel.assert_not_awaited()

    asyncio.run(scenario())


def test_cancel_research_install_settles_only_pending_environments():
    async def scenario():
        item = packages_module.UserPackages.__new__(packages_module.UserPackages)
        item.task = None
        item.ctx = SimpleNamespace(start=lambda work: asyncio.create_task(work), publish=lambda *args: None)
        item.status = lambda: item.job
        entered = asyncio.Event()

        async def install():
            item.job["environments"].update(research={"state": "ready"}, strategy={"state": "resolving"})
            entered.set()
            await asyncio.Event().wait()

        item.install = install
        item.start_install()
        await entered.wait()
        job = await item.cancel()
        assert job["state"] == "cancelled"
        assert job["environments"] == {"research": {"state": "ready"}, "strategy": {"state": "cancelled"}}

    asyncio.run(scenario())
