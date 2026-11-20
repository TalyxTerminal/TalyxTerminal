"""The window's bridge (standard 4 for the window): binary frames over /ws/bridge, the user's identity stamped by the
Host, and the workspace each call names - so a module's first call from a fresh window never races the binding."""

from __future__ import annotations

import asyncio
import json

import websockets

from tests.platform.harness import run, running


class Window:
    """A window over the bridge. ``answers`` maps the window's own actions (the Host asks it to open, close or list
    panels) to what this window answers; what it was asked is kept in ``asked``."""

    def __init__(self, socket, answers: dict | None = None):
        self.socket, self.ids, self.pending = socket, 0, {}
        self.answers, self.asked = answers or {}, []

    async def start(self) -> dict:
        welcome = await self.socket.recv()
        asyncio.create_task(self._read())
        return json.loads(welcome.partition(b"\n")[0])

    async def _read(self) -> None:
        async for message in self.socket:
            head, _, body = message.partition(b"\n")
            envelope = json.loads(head)
            if envelope["t"] == "call":
                self.asked.append((envelope["cap"], json.loads(body), envelope["from"]))
                await self.socket.send(json.dumps({"t": "result", "id": envelope["id"]}).encode() + b"\n"
                                       + json.dumps(self.answers[envelope["cap"]]).encode())
                continue
            future = self.pending.pop(envelope.get("id"), None)

            if future is not None:
                if envelope["t"] == "result":
                    future.set_result(json.loads(body) if body else None)
                else:
                    future.set_exception(RuntimeError(envelope["err"]["code"]))

    async def call(self, capability: str, value, **extra):
        self.ids += 1
        request_id = f"w{self.ids}"
        self.pending[request_id] = asyncio.get_running_loop().create_future()
        await self.socket.send(json.dumps({"t": "call", "id": request_id, "cap": capability, **extra}).encode()
                               + b"\n" + json.dumps(value).encode())
        return await self.pending[request_id]


def test_a_window_call_is_the_users_in_the_workspace_it_names(tmp_path):
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace("shown")
            async with websockets.connect(f"ws://127.0.0.1:{platform.port}/ws/bridge", origin=platform.host.config.ui_url.rstrip("/"), max_size=None) as socket:
                window = Window(socket)
                welcome = await window.start()
                assert welcome["t"] == "welcome" and welcome["session"]
                # Not bound to any workspace yet: the call names the one it belongs to.
                seen = await window.call("echo.whoami", {}, ws=workspace)
                assert (seen["origin"], seen["workspaceId"]) == ("user", workspace)
                assert (await window.call("echo.whoami", {}))["workspaceId"] is None
                try:
                    await window.call("echo.whoami", {}, ws="not-open")
                    raise AssertionError("a closed workspace was accepted")
                except RuntimeError as error:
                    assert str(error) == "target_gone"
                try:
                    await window.call("echo.whoami", {}, **{"from": {"origin": "ai", "module": "echo"}})
                    raise AssertionError("a window claimed another caller")
                except RuntimeError as error:
                    assert str(error) == "permission_denied"

    run(scenario())


def test_a_window_frame_cannot_name_the_module_it_acts_for(tmp_path):
    """Standard 4 for the window: every module's frontend shares one window, so the Host cannot tell which one sent a
    frame; whatever module a frame names, the call is the user's and nothing that belongs to a module's backend
    (its secrets, its notifications, its extension point's implementations, its events) can be done from it."""
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace("shown")
            async with websockets.connect(f"ws://127.0.0.1:{platform.port}/ws/bridge", origin=platform.host.config.ui_url.rstrip("/"), max_size=None) as socket:
                window = Window(socket)
                await window.start()
                seen = await window.call("echo.whoami", {}, ws=workspace, module="echo")
                assert (seen["origin"], seen["module"]) == ("user", None)
                refused = {
                    "platform.secrets.set": {"key": "apiKey", "value": "stolen"},
                    "platform.secrets.configured": {"key": "apiKey"},
                    "platform.storage.directories": {},
                    "platform.notifications.toast": {"title": "As echo", "message": "x"},
                    "consumer.point:echo:one.say": {"text": "x"},
                }
                for capability, value in refused.items():
                    try:
                        await window.call(capability, value, ws=workspace, module="consumer")
                        raise AssertionError(f"{capability} was done as a module from a window")
                    except RuntimeError as error:
                        assert str(error) == "permission_denied", (capability, error)

    run(scenario())


def test_panels_are_opened_closed_activated_and_listed_by_anyone_in_the_window_showing_the_workspace(tmp_path):
    """Checkup item 8: panels' close and activate are platform actions like open and list, so AI and modules use them
    as the user does; the platform asks the window that shows the workspace (workspace.shown), and its answer has the
    declared shape."""
    async def scenario():
        async with running(tmp_path) as platform:
            workspace = await platform.workspace("shown")
            other = await platform.workspace("not shown")
            panels = {"panels": [{"panelId": "p1", "type": "echo.main", "active": True, "visible": True,
                                  "view": {"symbol": "BTC/USDT"}}]}
            answers = {"platform.open_panel": {"panelId": "p2"}, "platform.close_panel": {},
                       "platform.activate_panel": {}, "platform.list_panels": panels}
            async with websockets.connect(f"ws://127.0.0.1:{platform.port}/ws/bridge", origin=platform.host.config.ui_url.rstrip("/"), max_size=None) as socket:
                window = Window(socket, answers)
                await window.start()
                await window.call("platform.workspace.shown", {"workspaceId": workspace,
                                                               "moduleInstances": {"echo": 1}})
                ai = {"origin": "ai", "module": None, "workspaceId": workspace, "ai": {"client": "test"}}
                module = {"origin": "module", "module": "echo", "workspaceId": workspace}
                assert await platform.call("platform.panels.list", caller=ai) == panels
                assert await platform.call("platform.panels.open", {"type": "echo.main"}, caller=module) == \
                    {"panelId": "p2"}
                assert await platform.call("platform.panels.activate", {"panelId": "p1"}, caller=ai) == {}
                assert await platform.call("platform.panels.close", {"panelId": "p1"}, caller=module) == {}
                assert [(name, value) for name, value, _ in window.asked] == [
                    ("platform.list_panels", {}), ("platform.open_panel", {"type": "echo.main"}),
                    ("platform.activate_panel", {"panelId": "p1"}), ("platform.close_panel", {"panelId": "p1"})]
                assert [caller["origin"] for _, _, caller in window.asked] == ["ai", "module", "ai", "module"]
                try:
                    await platform.call("platform.panels.close", {"panelId": "p1"}, workspace=other)
                    raise AssertionError("a workspace no window shows had its panel closed")
                except Exception as error:  # noqa: BLE001
                    assert getattr(error, "code", None) == "ui_required"
                windows = await platform.call("platform.workspace.list_open")
                assert [item["workspaceId"] for item in windows] == [workspace, other]

    run(scenario())


def test_bridge_checks_origin_before_registering_a_window(tmp_path):
    import pytest
    from websockets.exceptions import InvalidStatus

    async def scenario():
        async with running(tmp_path, test_modules=()) as platform:
            for origin in (None, "null", "http://127.0.0.1:5249"):
                with pytest.raises(InvalidStatus):
                    async with websockets.connect(f"ws://127.0.0.1:{platform.port}/ws/bridge", origin=origin):
                        pass
            assert not platform.host.sessions
    run(scenario())


def test_ai_cannot_stop_a_module_until_an_open_window_has_saved_its_input(tmp_path):
    import pytest
    from talyx_sdk.errors import ContractError

    async def scenario():
        async with running(tmp_path, test_modules=("echo",)) as platform:
            ws = await platform.workspace()
            async with websockets.connect(f"ws://127.0.0.1:{platform.port}/ws/bridge",
                                          origin=platform.host.config.ui_url.rstrip("/")) as socket:
                welcome = json.loads((await socket.recv()).partition(b"\n")[0])
                platform.host.workspaces.show(welcome["session"], ws, {})
                caller = {"origin": "ai", "workspaceId": ws, "module": None, "ai": {"client": "test"}}
                for refuse in (True, False):
                    stopping = asyncio.create_task(platform.host.ai.invoke(caller, "platform.modules.disable", {"id": "echo"}))
                    head, _, data = (await asyncio.wait_for(socket.recv(), 5)).partition(b"\n")
                    request = json.loads(head)
                    assert request["cap"] == "platform.prepare_module_stop"
                    assert json.loads(data) == {"id": "echo", "reason": "disable"}
                    assert platform.process("echo").state == "ready"
                    if refuse:
                        answer = {"t": "error", "id": request["id"], "err": ContractError("conflict", "Draft is busy").to_dict()}
                        await socket.send(json.dumps(answer).encode() + b"\n")
                        with pytest.raises(ContractError, match="Draft is busy"):
                            await stopping
                    else:
                        await socket.send(json.dumps({"t": "result", "id": request["id"]}).encode() + b"\n{}")
                        assert not (await stopping)["enabled"]
    run(scenario())
