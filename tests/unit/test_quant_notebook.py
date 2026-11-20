"""The notebook file, the package lock merge, the pip block and the confirmation rule for AI-written cells."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend.notebook_activation import CellActivation
from modules.quant.backend.notebook_file import (
    OUTPUT_LIMIT,
    TOO_LARGE,
    new_cell,
    parse,
    serialize,
)
from modules.quant.backend.notebook_routes import blocked_install, text_of
from modules.quant.backend.packages import jsonc, lock_blocks, merge_locks, requested_names
from modules.quant.native.object_templates import SOURCES
from tests.unit.quant_runtime_fakes import adapt

BASE = """# header
numpy==2.5.4 \\
    --hash=sha256:aaa
    # via -r pins
pandas==2.3.3 \\
    --hash=sha256:bbb
qlib @ file:./wheels/qlib.whl \\
    --hash=sha256:ccc
"""


def test_a_notebook_round_trips_with_cell_ids_outputs_and_metadata():
    model = {"cells": [{**new_cell("code", "print(1)\nx = 2"), "executionCount": 3,
                        "outputs": [{"output_type": "stream", "name": "stdout", "text": "1\n"},
                                    {"output_type": "display_data", "data": {"text/plain": "a\nb", "image/png": "AAAA", "application/json": {"k": 1}}, "metadata": {}}]},
                       new_cell("markdown", "# Title")],
             "metadata": {"talyx": {"note": 1}}}
    text = serialize(model)
    raw = json.loads(text)
    assert raw["nbformat"] == 4 and raw["metadata"]["kernelspec"]["name"] == "python3"
    assert raw["cells"][0]["source"] == ["print(1)\n", "x = 2"] and raw["cells"][0]["outputs"][1]["data"]["text/plain"] == ["a\n", "b"]
    assert parse(text) == {**model, "metadata": {"talyx": {"note": 1}}}
    assert parse(SOURCES["notebook"][1])["cells"][0]["source"] == ""


def test_an_output_over_two_megabytes_is_replaced_by_one_line_and_the_rest_is_kept():
    big = {"output_type": "stream", "name": "stdout", "text": "x" * (OUTPUT_LIMIT + 1)}
    small = {"output_type": "stream", "name": "stdout", "text": "ok\n"}
    saved = parse(serialize({"cells": [{**new_cell(), "outputs": [big, small]}], "metadata": {}}))
    assert [output["text"] for output in saved["cells"][0]["outputs"]] == [TOO_LARGE, "ok\n"]


def test_a_file_that_is_not_a_notebook_or_a_model_that_is_malformed_is_refused():
    with pytest.raises(ContractError) as error:
        parse("{not json")
    assert error.value.code == "invalid_input"
    with pytest.raises(ContractError):
        parse(json.dumps({"nbformat": 3, "cells": [], "metadata": {}}))
    with pytest.raises(ContractError):
        serialize({"cells": [new_cell(), {**new_cell(), "id": "dup"}, {**new_cell(), "id": "dup"}], "metadata": {}})
    with pytest.raises(ContractError):
        serialize({"cells": [{"id": "a", "type": "code", "source": 1, "outputs": []}]})


def test_the_user_packages_are_added_to_the_lock_and_a_pinned_version_never_moves():
    resolved = BASE.split("qlib")[0] + "ta==0.11.0 \\\n    --hash=sha256:ddd\n    # via -r requested.in\n"
    merged = merge_locks(BASE, resolved)
    assert set(lock_blocks(merged)) == {"numpy", "pandas", "qlib", "ta"} and merged.startswith(BASE.rstrip("\n"))
    assert merge_locks(BASE, BASE) == BASE
    with pytest.raises(ContractError) as error:
        merge_locks(BASE, "pandas==2.2.0 \\\n    --hash=sha256:eee\n")
    assert error.value.details == {"package": "pandas"}
    assert requested_names("# a comment\n\nta>=0.11  # indicators\npandas-ta\n") == ["ta>=0.11", "pandas-ta"]


def test_vs_code_settings_read_with_comments_and_trailing_commas():
    assert jsonc('{\n // note\n "a": "x//y", /* b */ "c": [1, 2,],\n}') == {"a": "x//y", "c": [1, 2]}


def test_pip_in_a_cell_is_blocked_and_pointed_at_the_package_flow():
    for code in ("%pip install ta", "x = 1\n!pip install ta", "  !python -m pip install ta", "%%pip install ta", "!uv pip install ta", "%conda install x"):
        assert "Packages page" in blocked_install(code)
    for code in ("import pip", "print('%pip')", "x = 1  # !pip", "pipeline = 2"):
        assert blocked_install(code) is None


def test_outputs_become_text_an_ai_can_read():
    assert text_of({"output_type": "stream", "name": "stdout", "text": "hi"}) == {"type": "stream", "name": "stdout", "text": "hi"}
    error = text_of({"output_type": "error", "ename": "ValueError", "evalue": "bad", "traceback": ["\x1b[31mline\x1b[0m"]})
    assert error["traceback"] == ["line"]
    image = text_of({"output_type": "display_data", "data": {"image/png": "AAAA"}, "metadata": {}})
    assert image == {"type": "display_data", "mimeTypes": ["image/png"]}


def activation(approvals):
    platform = SimpleNamespace(ai=SimpleNamespace(require_approval=lambda value: _record(approvals, ("approve", value))))
    return CellActivation(adapt(SimpleNamespace(platform=platform)))


async def _value(value):
    return value


async def _record(items, entry):
    items.append(entry)


class FakeSession:
    def __init__(self):
        self.panels, self.calls, self.object_id = {}, [], "nb"
        self.state = "idle"

    def attach(self, panel_id):
        from modules.quant.backend.kernels import Panel
        panel = self.panels[panel_id] = Panel(panel_id)
        panel.queue.put_nowait({"type": "state", "state": "idle"})
        return panel

    def detach(self, panel_id):
        self.panels.pop(panel_id)

    def send(self, panel_id, message):
        self.panels[panel_id].queue.put_nowait(message)

    def interrupt(self):
        self.calls.append(("interrupt",))

    async def restart(self):
        self.calls.append(("restart",))

    async def ask(self, panel, kind, request, code, cursor):
        self.calls.append((kind, request, code, cursor))
        self.send(panel, {"type": kind + "_reply", "id": request, "matches": ["read_csv"]})

    async def execute(self, cell, code, allow_stdin=False):
        self.calls.append(("execute", cell, code, allow_stdin))
        self.send(next(iter(self.panels)), {"type": "accepted", "cell": cell, "msgId": "m1"})


def test_the_kernel_socket_carries_the_panels_operations_and_reports_what_the_host_refuses():
    from fastapi import APIRouter, FastAPI
    from starlette.testclient import TestClient

    from modules.quant.backend.notebook_routes import NotebookServices, mount_notebooks

    session = FakeSession()
    services = NotebookServices.__new__(NotebookServices)
    services.files = None
    services.kernels = SimpleNamespace(ensure=lambda record: session, status=lambda object_id: {}, find=lambda object_id: session)
    services.packages = None
    services.store = SimpleNamespace(read=lambda object_id: {"id": object_id, "kind": "notebook"})
    router = APIRouter()
    mount_notebooks(router, services)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app).websocket_connect("/notebooks/nb/kernel/socket?panel=p1") as socket:
        assert socket.receive_json() == {"type": "state", "state": "idle"}
        socket.send_json({"op": "execute", "cell": "c1", "code": "print(1)"})
        assert socket.receive_json() == {"type": "accepted", "cell": "c1", "msgId": "m1"}
        socket.send_json({"op": "execute", "cell": "c2", "code": "%pip install ta"})
        blocked = socket.receive_json()
        assert blocked["type"] == "error" and blocked["code"] == "pip_blocked" and blocked["cell"] == "c2" and "Packages page" in blocked["message"]
        socket.send_json({"op": "complete", "id": 4, "code": "pd.read_c", "cursor": 9})
        assert socket.receive_json() == {"type": "complete_reply", "id": 4, "matches": ["read_csv"]}
        socket.send_json({"op": "interrupt"})
        socket.send_json({"op": "restart"})
        socket.send_json({"op": "nonsense"})
        error = socket.receive_json()
        assert error["type"] == "error" and error["op"] == "nonsense" and error["code"] == "invalid_input"
        socket.send_text("not json")
        assert socket.receive_json()["op"] == "message"
    assert [call[0] for call in session.calls] == ["execute", "complete", "interrupt", "restart"]


def test_every_panel_hears_when_another_joins_or_leaves_and_a_preparing_kernel_is_never_stale():
    from modules.quant.backend.kernels import Kernels, KernelSession

    session = KernelSession(SimpleNamespace(), "w1", {"id": "n1", "name": "Notebook", "directory": "."})
    first = session.attach("p1")
    first.queue.get_nowait()
    session.attach("p2")
    assert first.queue.get_nowait()["panels"] == 2
    session.detach("p2")
    assert first.queue.get_nowait()["panels"] == 1

    session.environment = {"preparation": {"state": "running", "message": None}}
    manager = Kernels.__new__(Kernels)
    manager.sessions = {("w1", "n1"): session}
    assert manager.stale("new-lock") == []


def test_a_kernel_outlives_the_call_that_started_it_and_is_reused(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from modules.quant.backend.kernels import Kernels, KernelSession

    async def scenario():
        sdk = Mock()
        def start(coroutine, *, activity):
            assert activity is sdk.activity.return_value
            return asyncio.create_task(coroutine)
        ctx = SimpleNamespace(sdk=sdk, workspace=lambda: "w1", start=start)
        manager = Kernels(ctx, SimpleNamespace(root=tmp_path), None, SimpleNamespace())
        release = asyncio.Event()
        monkeypatch.setattr(KernelSession, "run", lambda self: release.wait())
        record = {"id": "n1", "name": "Notebook", "directory": str(tmp_path)}
        session = manager.ensure(record)
        assert manager.ensure(record) is session
        sdk.activity.assert_called_once_with("w1", "Notebook kernel · Notebook", call_id=None,
                                             open_target={"module":"quant", "panelType":"workspace", "objectId":"object:n1"})
        release.set()
        await session.task

    asyncio.run(scenario())


def _ai_services(waiter):
    from types import SimpleNamespace

    from modules.quant.backend.notebook_routes import NotebookServices

    async def cell_code(*_):
        return "print(1)"

    async def approve(*_):
        return None

    async def execute(cell, code, wait=False):
        return waiter
    item = NotebookServices.__new__(NotebookServices)
    async def save_execution(object_id, cell_id, code, result):
        assert (object_id, cell_id, code, result["executionCount"]) == ("o", "c", "print(1)", 3)
    item.files = SimpleNamespace(cell_code=cell_code, save_execution=save_execution)
    item.activation = SimpleNamespace(approve_ai=approve)
    item.store = SimpleNamespace(read=lambda oid: {"id": oid, "kind": "notebook"})
    item.kernels = SimpleNamespace(ensure=lambda record: SimpleNamespace(execute=execute, panels={}))
    item.ctx = SimpleNamespace(start=lambda coroutine, title: asyncio.create_task(coroutine))
    return item


def test_an_ai_waits_120_seconds_for_a_cell_and_the_cell_keeps_running_past_that(monkeypatch):
    import modules.quant.backend.notebook_routes as routes
    from tests.unit.quant_public import service_actions
    assert routes.RUN_WAIT_SECONDS == 120

    async def scenario():
        waiter = asyncio.get_running_loop().create_future()
        item = _ai_services(waiter)
        with pytest.raises(ContractError) as caught:
            await item.ai_run_cell({"objectId": "o", "cellId": "c", "timeoutSeconds": 0.05})
        assert "still running" in caught.value.message
        # The wait gave up, the cell did not: its result is still delivered when it finishes.
        assert not waiter.cancelled() and not waiter.done()
        waiter.set_result({"status": "ok", "executionCount": 3, "outputs": []})
        act = service_actions(monkeypatch, routes.handle_notebook_actions, item)
        assert (await act("run_cell", {"objectId":"o","cellId":"c"}))["executionCount"] == 3
    asyncio.run(scenario())


def test_public_cell_receipt_follows_document_save_with_a_panel_attached():
    from unittest.mock import AsyncMock

    async def scenario():
        waiter = asyncio.get_running_loop().create_future()
        result = {"status": "ok", "executionCount": 1, "outputs": [
            {"output_type": "execute_result", "execution_count": 1, "data": {"text/plain": "42"}, "metadata": {}}]}
        waiter.set_result(result)
        item = _ai_services(waiter)
        item.files.save_execution = AsyncMock()
        session = item.kernels.ensure(None)
        session.panels["foreground"] = object()
        item.kernels.ensure = lambda _: session
        receipt = await item.ai_run_cell({"objectId": "o", "cellId": "c"})
        item.files.save_execution.assert_awaited_once_with("o", "c", "print(1)", result)
        assert receipt["executionCount"] == 1 and receipt["outputs"][0]["text"] == "42"
    asyncio.run(scenario())


def test_closing_the_panel_does_not_stop_the_cell_it_finishes_and_its_outputs_are_kept():
    from modules.quant.backend.kernels import KernelSession as Session

    async def scenario():
        session = Session.__new__(Session)
        session.panels = {"p1": object()}
        session.broadcast = lambda message: None
        session.view = lambda: {}
        waiter = asyncio.get_running_loop().create_future()
        session.cells = {"m1": {"cell": "c1", "waiter": waiter, "outputs": [], "status": None}}
        session.detach("p1")
        assert session.panels == {} and "m1" in session.cells and not waiter.cancelled()
    asyncio.run(scenario())


def test_random_panel_and_cell_lifecycles_never_lose_a_cell_or_a_waiter():
    """Panels come and go at random while cells run: every cell finishes once, every waiting AI call is answered, the kernel is idle at the end, and a panel
    that stayed from a cell's start to its end heard it accepted and heard it reply, once each."""
    import random

    from modules.quant.backend.kernels import KernelSession

    async def one(seed):
        rng = random.Random(seed)
        session = KernelSession.__new__(KernelSession)
        session.panels, session.cells, session.inputs, session.state, session.message, session.epoch = {}, {}, {}, "idle", None, "e"
        session.environment, session.manager, session.object_id = None, SimpleNamespace(), "nb"
        session.wanted_environment = lambda: "research"
        session.ask_variables = lambda: None
        session.failed, session.stopping, session.variables, session.comms, session.since = None, False, [], {}, 0
        counter = iter(range(10 ** 6))

        class Client:
            def execute(self, code, **_):
                return f"m{next(counter)}"
        client = Client()

        async def running_client():
            return client
        session.running_client = running_client
        heard, waiters, attached_since = {}, [], {}
        for step in range(60):
            action = rng.choice(["attach", "detach", "execute", "execute", "finish"])
            if action == "attach":
                name = f"p{rng.randrange(4)}"
                if name not in session.panels:
                    session.attach(name)
                    attached_since[name] = step
            elif action == "detach" and session.panels:
                name = rng.choice(sorted(session.panels))
                session.detach(name)
                attached_since.pop(name, None)
            elif action == "execute":
                cell = f"c{step}"
                waiter = await session.execute(cell, "1", wait=rng.random() < .5)
                if not isinstance(waiter, str):
                    waiters.append(waiter)
                for name in session.panels:
                    heard.setdefault(cell, {})[name] = {"accepted": 0, "reply": 0}
            elif action == "finish" and session.cells:
                msg_id = rng.choice(sorted(session.cells))
                session.cells[msg_id].update(replied=True, idle=True, status="ok", executionCount=step)
                session.settle(msg_id)
            for panel_id, panel in session.panels.items():
                while not panel.queue.empty():
                    message = panel.queue.get_nowait()
                    if message.get("type") in {"accepted", "reply"} and message["cell"] in heard and panel_id in heard[message["cell"]]:
                        heard[message["cell"]][panel_id][message["type"]] += 1
            assert session.view()["busy"] == bool(session.cells)
        for msg_id in sorted(session.cells):
            session.cells[msg_id].update(replied=True, idle=True, status="ok", executionCount=0)
            session.settle(msg_id)
        assert session.cells == {} and session.state == "idle" and all(waiter.done() for waiter in waiters)
        for panels in heard.values():
            for counts in panels.values():
                assert counts["accepted"] <= 1 and counts["reply"] <= 1
    for seed in range(40):
        asyncio.run(one(seed))


@pytest.mark.parametrize("value", [["A", "B"], [100, 101]])
def test_imported_notebook_preserves_editable_ids_attachments_and_json(value):
    from modules.quant.backend.ai_views import notebook_text
    attached = {"cash.svg": {"image/svg+xml": "<svg/>"}}
    raw = {"nbformat": 4, "nbformat_minor": 4, "cells": [
        {"cell_type": "markdown", "source": "![cash](attachment:cash.svg)", "attachments": attached},
        {"cell_type": "code", "id": "cell-0", "source": "prices", "outputs": [
            {"output_type": "display_data", "data": {"application/json": value}}]}]}
    text = json.dumps(raw)
    model = parse(text)
    identity = model["cells"][0]["id"]
    assert identity != "cell-0" and identity == parse(text)["cells"][0]["id"]
    assert f"markdown cell {identity}" in notebook_text(text)
    saved = json.loads(serialize(model))["cells"]
    assert saved[0]["attachments"] == attached and saved[0]["id"] == identity
    assert saved[1]["outputs"][0]["data"]["application/json"] == value
