"""Agent on the platform's AI service: the catalog through three tools, calls through platform.ai.call, and the
user asked first as the conversation's permission and the action's effects say."""

import asyncio
import json
import ntpath
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from talyx_sdk import ContractError

from modules.agent.backend.files import Folder
from modules.agent.backend.permissions import proposal, rememberable
from modules.agent.backend.service import AgentService
from modules.agent.backend.table_inputs import table_facts

WORKSPACE = "w1"
CATALOG = [
    {"name": "chart.read_watchlist", "description": "Read a watchlist.", "effects": ["read"], "task": False,
     "input": {"type": "object", "properties": {"watchlistId": {"type": "string"}}}},
    {"name": "chart.add_to_watchlist", "description": "Add symbols to a watchlist.", "effects": ["write"], "task": False,
     "input": {"type": "object", "properties": {"symbol": {"$ref": "#/$defs/Symbol"}}, "$defs": {"Symbol": {"type": "string"}}}},
    {"name": "chart.delete_watchlist", "description": "Delete a watchlist.", "effects": ["delete"], "task": False,
     "input": {"type": "object", "properties": {"watchlistId": {"type": "string"}}}},
    {"name": "trade.place_order", "description": "Place an order.", "effects": ["write", "funds"], "task": False,
     "input": {"type": "object", "properties": {"symbol": {"type": "string"}}}},
]


class ScriptedModel(FakeMessagesListChatModel):
    requests: list[list[Any]] = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, **kwargs):
        self.requests.append(list(messages))
        return await super()._agenerate(messages, **kwargs)


class ScriptedRuntime:
    def __init__(self, catalogue, model):
        self.catalogue, self.catalog, self.chat_model = catalogue, catalogue, model

    @asynccontextmanager
    async def model(self, model_id, **kwargs):
        async def guard(operation):
            return await operation()

        async def invoke(messages, **kwargs):
            return AIMessage(content=json.dumps({"body": "Earlier work.", "refs": []}))
        yield SimpleNamespace(model=self.chat_model, resolved=self.catalogue.resolve(model_id), guard=guard, invoke=invoke)

    def native_search_tools(self, model_id):
        return []


class Activity:
    def add_task(self, task):
        return task


class Platform:
    """What Agent asks of the platform: its AI catalog and calls, recorded."""

    def __init__(self, root):
        self.root, self.calls = root, []

    async def call(self, name, value, *, workspace=None):
        if name == "platform.notifications.toast":
            return {}
        if name == "platform.ai.catalog":
            if "action" in value:
                return {"outputFields": [{"name":"ok", "type":"boolean", "unit":None}]}
            if "module" not in value:
                return {"version": 1, "modules": [{"module": owner, "guide": None, "docs": []} for owner in ("chart", "trade")],
                        "nextCursor": None}
            return {"tools": [{**item, "examples": [], "errors": [], "effectBranches": None, "target": None}
                              for item in CATALOG if item["name"].partition(".")[0] == value["module"]],
                    "nextCursor": None}
        if name == "platform.activity.list":
            return {"workspaceId": value["workspaceId"], "activities": []}
        if name == "platform.modules.list":
            return []
        if name == "platform.ai.call":
            if value["action"] == "platform.changes.list":
                return {"result": [], "callId": "changes-list"}
            self.calls.append(value)
            return {"result": {"ok": True, "action": value["action"]}, "callId": f"host-{len(self.calls)}"}
        if name == "platform.panels.list":
            raise ContractError("ui_required", "No window")
        if name == "platform.runs.end":
            return {}
        raise AssertionError(name)

    async def secret(self, reference):
        return None

    async def run_token(self, workspace, *, conversation):
        return {"url": "http://127.0.0.1:9/api/runs/call", "token": "token", "runId": "run-1"}

    async def workspace_folder(self, workspace):
        folder = self.root / "workspace"
        folder.mkdir(exist_ok=True)
        return str(folder)


def context(platform):
    process = SimpleNamespace(send=lambda header, payload: None, codec=lambda _schema: SimpleNamespace(check=lambda value: value),
                              module=SimpleNamespace(events={"conversation_changed": SimpleNamespace(payload=None)}))
    return SimpleNamespace(module_id="agent", call=platform.call, process=process, run_token=platform.run_token,
                           platform=SimpleNamespace(secret=platform.secret, workspace_folder=platform.workspace_folder),
                           activity=lambda workspace, title, origin=None: Activity())


def call(action, arguments, identity):
    return AIMessage(content="", tool_calls=[{"name": "talyx_call_action", "args": {"name": action, "input": arguments}, "id": identity}])


@asynccontextmanager
async def agent(tmp_path: Path, responses):
    platform = Platform(tmp_path)
    service = AgentService(context(platform), {"dataDir": tmp_path / "data", "userDir": tmp_path / "user"})
    service.catalogue.put_connection({"id": "local", "name": "Local", "providerId": "ollama", "kind": "local",
                                      "protocol": "ollama", "baseUrl": "http://127.0.0.1:11434"})
    service.catalogue.put_model({"id": "model", "connectionId": "local", "model": "tool-model", "contextWindow": 32000, "tools": True})
    service.model_runtime = ScriptedRuntime(service.catalogue, ScriptedModel(responses=responses))
    await service.start()
    try:
        yield service, platform
    finally:
        for task in list(service.runners.values()):
            task.cancel()
        await asyncio.gather(*service.runners.values(), return_exceptions=True)
        await service.checkpoint_connection.close()


async def settled(service, conversation):
    async with asyncio.timeout(30):
        while conversation in service.runners:
            await asyncio.sleep(.02)
    return service.store.read(conversation, WORKSPACE)


async def start(service, text, permission="auto"):
    conversation = service.store.create(WORKSPACE, model="model")["id"]
    service.update_conversation(conversation, WORKSPACE, {"permission": permission})
    await service.send(conversation, WORKSPACE, text, include_view=False)
    return conversation, await settled(service, conversation)


def cards(snapshot, kind):
    return [card for card in snapshot["cards"] if card["type"] == kind]


def native(name, args, identity):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": identity}])


def test_workspace_personalization_reaches_a_new_chats_first_model_request(tmp_path):
    from modules.agent.backend.settings_pages import page_request

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="ok")]) as (service, _):
            rule = "End each report with QA-LOCAL-17."
            await page_request(service, "instructions", {"scope": "workspace", "text": rule}, WORKSPACE)
            await start(service, "Write a report")
            system = str(service.model_runtime.chat_model.requests[-1][0].content)
            assert rule in system
            assert "including files you write" in system
    asyncio.run(scenario())


def test_current_view_keeps_the_visible_owner_ids_in_the_model_request(tmp_path):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="Done")]) as (service, _):
            cid = service.store.create(WORKSPACE, model="model")["id"]
            view = {"summary":"Instrument · 4h","refs":[{"chartId":"visible-chart","timeframe":"4h"}],
                    "openTargets":[{"kind":"chart","key":"visible-chart","label":"Chart"}]}
            turn = service.store.new_turn(cid, WORKSPACE, "Recent volume?", view=view)
            sent = await service.input_messages(turn, service.store.conversation(cid), service.catalogue.resolve("model"))
            assert "visible-chart" in sent[-1].text and "openTargets" in sent[-1].text
    asyncio.run(scenario())


def test_framework_file_changes_restore_creation_edits_and_user_overwrites(tmp_path):
    async def scenario():
        responses = [native("write_file", {"file_path":"/workspace/report.md","content":"one"}, "write"),
                     AIMessage(content="Created"), native("edit_file", {"file_path":"/workspace/report.md",
                     "old_string":"one","new_string":"two"}, "edit"), AIMessage(content="Edited")]
        async with agent(tmp_path, responses) as (service, _):
            cid, first = await start(service, "Write a report")
            await service.send(cid, WORKSPACE, "Change it", include_view=False)
            second = await settled(service, cid)
            path = tmp_path/"workspace/report.md"
            assert path.read_text() == "two"
            path.write_text("user's later edit")
            edit, write = second["turns"][-1]["id"], first["turns"][0]["id"]
            await service.changes.revert(WORKSPACE, cid, edit, "undo")
            assert path.read_text() == "one"
            await service.changes.revert(WORKSPACE, cid, write, "undo")
            assert not path.exists()
            await service.changes.revert(WORKSPACE, cid, write, "redo")
            await service.changes.revert(WORKSPACE, cid, edit, "redo")
            assert path.read_text() == "two"
            assert service.store.db.execute("SELECT COUNT(*) FROM file_images").fetchone()[0] == 2
    asyncio.run(scenario())


def test_skill_file_tools_report_the_loader_check_and_register_a_valid_skill(tmp_path):
    async def scenario():
        responses = [
            native("write_file", {"file_path": "/skills/workspace/bad/SKILL.md", "content": "# Missing metadata"}, "bad"),
            native("write_file", {"file_path": "/skills/workspace/good/SKILL.md",
                                  "content": "---\nname: good\ndescription: " + "a"*1024 + " trigger\n---\nBody"}, "long"),
            native("edit_file", {"file_path": "/skills/workspace/good/SKILL.md", "old_string":"a"*1024 + " trigger",
                                 "new_string":"Review earnings."}, "good"),
            AIMessage(content="Finished"),
        ]
        async with agent(tmp_path, responses) as (service, _):
            await start(service, "Write two workspace skills", permission="full")
            messages = service.model_runtime.chat_model.requests[-1]
            results = {m.tool_call_id: m for m in messages if m.type == "tool"}
            assert results["long"].status == "error" and "1024" in results["long"].content
            assert results["bad"].status == "error" and "front matter" in results["bad"].content
            assert "Saved and checked skill good" in results["good"].content
            catalog = service.skills.snapshot(tmp_path / "workspace/skills")
            assert [skill["name"] for skill in catalog["items"]] == ["good"]
    asyncio.run(scenario())

@pytest.mark.parametrize("scope", ["workspace", "global"])
def test_run_python_runs_the_mounted_skill_script_with_its_sibling_and_workspace_cwd(tmp_path, scope):
    async def scenario():
        virtual = f"/skills/{scope}/sample/scripts/report.py"
        response = native("run_python", {"path": virtual}, "report")
        async with agent(tmp_path, [response, AIMessage(content="Done")]) as (service, _):
            root = service.skills.user_root if scope == "global" else tmp_path / "workspace" / "skills"
            folder = root / "sample" / "scripts"
            folder.mkdir(parents=True)
            (folder / "data.txt").write_text(f"{scope} sample")
            (folder / "report.py").write_text(
                'from pathlib import Path\n'
                'value = Path(__file__).with_name("data.txt").read_text()\n'
                'print(value)\nPath("result.txt").write_text(value)\n'
            )
            cid, snapshot = await start(service, "Run the sample", permission="full")
            output = service.root / "files" / cid / ".agent" / "runs" / "run-1" / "stdout.txt"
            assert output.read_text().strip() == f"{scope} sample"
            assert (tmp_path / "workspace" / "result.txt").read_text() == f"{scope} sample"
            [changes] = cards(snapshot, "changes")
            assert [item["objectId"] for item in changes["items"]] == ["/workspace/result.txt"]
    asyncio.run(scenario())


def test_run_python_rejects_skill_code_and_paths_leaving_the_mount(tmp_path):
    async def scenario():
        path = "/skills/workspace/sample/report.py"
        responses = [native("run_python", {"path": path, "code": "print('changed')"}, "rewrite"),
                     native("run_python", {"path": "/workspace/skills/sample/report.py", "code": "print('changed')"}, "alias"),
                     native("run_python", {"path": "/skills/workspace/../outside.py"}, "escape"),
                     AIMessage(content="Done")]
        async with agent(tmp_path, responses) as (service, _):
            workspace = tmp_path / "workspace"
            skill = workspace / "skills" / "sample"
            skill.mkdir(parents=True)
            original = b"print('original')\n"
            (skill / "report.py").write_bytes(original)
            (workspace / "outside.py").write_text('from pathlib import Path\nPath("escaped").touch()')
            await start(service, "Check both requests", permission="full")
            results = {m.tool_call_id: m for m in service.model_runtime.chat_model.requests[-1] if m.type == "tool"}
            for key in ("rewrite", "alias"):
                assert results[key].status == "error" and "without code" in results[key].content
            assert results["escape"].status == "error"
            assert (skill / "report.py").read_bytes() == original
            assert not (workspace / "escaped").exists()
    asyncio.run(scenario())


def test_a_calculation_script_is_a_workspace_file_of_the_turn_and_undo_removes_it(tmp_path):
    async def scenario():
        script = 'from pathlib import Path\nPath("btc-volatility/result.csv").write_text("vol\\n0.5\\n")\nprint("0.5")\n'
        responses = [native("run_python", {"path": "btc-volatility/calc.py", "code": script}, "run"),
                     AIMessage(content="Volatility is 0.5")]
        async with agent(tmp_path, responses) as (service, _):
            cid, snapshot = await start(service, "Compute BTC volatility")
            folder = tmp_path / "workspace" / "btc-volatility"
            # Python ran in the workspace folder; its complete output stays in the chat's private folder.
            assert (folder / "result.csv").read_text() == "vol\n0.5\n"
            run = service.root / "files" / cid / ".agent" / "runs" / "run-1"
            assert (run / "stdout.txt").read_text().strip() == "0.5"
            assert not (tmp_path / "workspace" / ".agent").exists()
            [changes] = cards(snapshot, "changes")
            assert [item["objectId"] for item in changes["items"]] == ["/workspace/btc-volatility/calc.py",
                                                                       "/workspace/btc-volatility/result.csv"]
            await service.changes.revert(WORKSPACE, cid, snapshot["turns"][-1]["id"], "undo")
            assert not (folder / "calc.py").exists() and not (folder / "result.csv").exists()
    asyncio.run(scenario())


def test_fork_and_rewrite_continue_the_selected_checkpoint_and_preserve_memory(tmp_path):
    async def scenario():
        responses = [native("remember", {"body":"Show traded volume","frequent":True}, "remember"),
                     AIMessage(content="Saved."), AIMessage(content="Second reply."), AIMessage(content="Edited reply.")]
        async with agent(tmp_path, responses) as (service, _):
            cid, first = await start(service, "Show traded volume")
            first_answer = next(c for c in first["cards"] if c.get("role") == "assistant")
            await service.send(cid, WORKSPACE, "Discard this turn", include_view=False)
            old = await settled(service, cid)
            fork = await service.history.fork(cid, WORKSPACE, first_answer["id"])
            assert [c["text"] for c in service.store.read(fork["id"], WORKSPACE)["cards"] if c.get("role")=="assistant"] == ["Saved."]
            target = next(c for c in old["cards"] if c.get("text") == "Discard this turn")
            await service.history.rewrite(cid, WORKSPACE, target["id"], "Replacement", False)
            latest = await settled(service, cid)
            assert [c["text"] for c in latest["cards"] if c.get("role")=="assistant"] == ["Saved.", "Edited reply."]
            assert [m["body"] for m in service.memory.list(WORKSPACE)["items"]] == ["Show traded volume"]
            assert all("Discard this turn" not in m.text for m in service.model_runtime.chat_model.requests[-1])
            # The core memory rides in the system prompt, not in the conversation's history.
            system = service.model_runtime.chat_model.requests[-1][0]
            assert "Show traded volume" in system.text.split("Remembered")[-1]
    asyncio.run(scenario())


@pytest.mark.parametrize("missing", [False, True])
def test_restart_follows_an_accepted_task_without_a_card_or_a_second_dispatch(tmp_path, monkeypatch, missing):
    action = "chart.read_watchlist"
    monkeypatch.setitem(next(item for item in CATALOG if item["name"] == action), "task", True)
    previous = Platform.call
    dispatches = []
    restarted = False

    async def platform_call(self, name, value, **kwargs):
        if name == "platform.ai.call" and value["action"] == action:
            dispatches.append(action)
            return {"callId":"original-call","result":{"taskId":"original-task"}}
        if name == "platform.tasks.get":
            assert value["taskId"] == "original-task"
            if restarted and missing:
                raise ContractError("not_found", "Host no longer has the original task")
            return {"state":"completed" if restarted else "running","action":action,"taskId":"original-task",
                    "progress":None,"error":None,"result":{"items":[]}}
        return await previous(self, name, value, **kwargs)
    monkeypatch.setattr(Platform,"call",platform_call)

    async def scenario():
        nonlocal restarted
        async with agent(tmp_path,[call(action,{},"task-call")]) as (service,_):
            cid, first = await start(service,"Read my list")
            [turn] = first["turns"]
            assert turn["state"] == "waiting_business"
            observers = list(service.observers.values())
            for observer in observers:
                observer.cancel()
            await asyncio.gather(*observers,return_exceptions=True)
            with service.store.transaction():
                service.store.db.execute("DELETE FROM cards WHERE id='task_original-call'")
        restarted = True
        async with agent(tmp_path,[AIMessage(content="Reported the original outcome.")]) as (service,_):
            snapshot = await settled(service,cid)
            assert snapshot["turns"][-1]["state"] == "completed"
            assert [card["state"] for card in cards(snapshot,"result")] == ["unknown" if missing else "completed"]
            assert dispatches == [action]
    asyncio.run(scenario())


def test_child_approval_is_durable_and_charged_to_its_own_run(tmp_path):
    async def scenario():
        dispatch = native("task", {"description":"Read and edit the analysis list", "subagent_type":"general-purpose"}, "delegate")
        responses = [dispatch, call("chart.add_to_watchlist", {"symbol":"BTC"}, "child-edit")]
        async with agent(tmp_path, responses) as (service, _):
            cid, snap = await start(service, "Delegate this list edit", permission="read_only")
            [approval] = cards(snap, "approval")
            [child] = cards(snap, "child_run")
            assert child["id"] == approval["childRunId"]
        async with agent(tmp_path, [AIMessage(content="Child done."), AIMessage(content="All done.")]) as (service, platform):
            await service.decide(approval["id"], WORKSPACE, approval["version"], "allow")
            result = await settled(service, cid)
            assert result["turns"][-1]["state"] == "completed"
            assert len(platform.calls) == 1
            assert cards(result,"child_run")[0]["state"] == "completed"
    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["completed", "stopped", "unknown"])
def test_activity_reconciles_after_restart_and_reports_completed_or_unknown_results(tmp_path, outcome):
    async def scenario():
        action = "chart.add_to_watchlist"
        target = {"module":"chart","panelType":"chart","objectId":"W"}
        activity = {"id":"job-before","module":"chart","title":"Build report","origin":"ai","callId":"host-1",
                    "openTarget":target,"progress":0}
        async with agent(tmp_path, [call(action, {"symbol":"BTC"}, "launch")]) as (service, platform):
            async def with_job(name, args, **kw):
                if name == "platform.activity.list":
                    return {"activities":[activity]}
                return await platform.call(name, args, **kw)
            service.ctx.call = with_job
            cid, first = await start(service, "Start this report")
            turn = first["turns"][-1]
            assert turn["state"] == "waiting_business"
        async with agent(tmp_path, [AIMessage(content="Observed the finished report.")]) as (service, platform):
            queue = asyncio.Queue()
            progress_queue = asyncio.Queue()
            async def events(name):
                selected = {"platform.activity.changes": progress_queue,
                            "platform.modules.changes": asyncio.Queue()}.get(name, queue)
                async def stream():
                    while True:
                        yield await selected.get()
                return stream()
            async def resumed(name,args,**kw):
                if name == "platform.activity.list":
                    return {"activities":[] if outcome == "unknown" else [{**activity,"id":"job-after"}]}
                return await platform.call(name,args,**kw)
            service.ctx.subscribe, service.ctx.call = events, resumed
            await service.activities.start()
            try:
                if outcome != "unknown":
                    activity["progress"] = {"stage":"Calculating","progress":0.5}
                    await progress_queue.put({})
                    async with asyncio.timeout(3):
                        while not any(card.get("task", {}).get("progress") == activity["progress"] for card in cards(service.store.read(cid, WORKSPACE), "task")):
                            await asyncio.sleep(0.01)
                    await queue.put({"workspaceId":WORKSPACE,"module":"chart","activityId":"job-after",
                                     "callId":"host-1","openTarget":target,"result":outcome,
                                     "summary":{"tested":{"end":"2025-01-03"},"numbers":{"trades":13}}})
                    await asyncio.sleep(.03)
                result = await settled(service,cid)
                [done] = result["turns"]
                assert done["id"] == turn["id"]
                assert done["state"] == {"completed":"completed","stopped":"cancelled","unknown":"completed"}[outcome]
                assert len([c for c in result["cards"] if c.get("role")=="assistant"]) == (outcome!="stopped")
                if outcome != "stopped":
                    received = str(service.model_runtime.chat_model.requests[-1])
                    if outcome == "unknown":
                        assert "unknown" in received
                    else:
                        assert "tested" in received and "numbers" in received and "2025-01-03" in received
                assert not platform.calls
            finally:
                service.activities.watcher.cancel()
                await asyncio.gather(service.activities.watcher,return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("resumed", [False, True])
def test_a_module_restart_resumes_waiting_turn_with_known_or_unknown_result(tmp_path, resumed):
    async def scenario():
        target = {"module":"chart","panelType":"chart","objectId":"W"}
        activity = {"id":"job-before","module":"chart","title":"Build report","origin":"ai","callId":"host-1",
                    "openTarget":target,"progress":None}
        platform_state = {"chart": "ready", "activities": [activity]}
        responses = [call("chart.add_to_watchlist", {"symbol":"BTC"}, "launch"), AIMessage(content="Observed the report.")]
        async with agent(tmp_path, responses) as (service, platform):
            ended, modules = asyncio.Queue(), asyncio.Queue()
            async def events(name):
                selected = {"platform.activity.ended": ended, "platform.modules.changes": modules}.get(name, asyncio.Queue())
                async def stream():
                    while True:
                        yield await selected.get()
                return stream()
            async def calls(name, args, **kw):
                if name == "platform.activity.list":
                    return {"activities": platform_state["activities"]}
                if name == "platform.modules.list":
                    return [{"id": "chart", "state": platform_state["chart"]}]
                return await platform.call(name, args, **kw)
            service.ctx.subscribe, service.ctx.call = events, calls
            await service.activities.start()
            try:
                cid, first = await start(service, "Start this report")
                assert first["turns"][-1]["state"] == "waiting_business"
                # The module's process ended: its activities are gone while it starts again, and that alone ends nothing.
                platform_state.update(chart="starting", activities=[])
                await modules.put({"version": 2})
                await asyncio.sleep(.03)
                assert service.store.read(cid, WORKSPACE)["turns"][-1]["state"] == "waiting_business"
                platform_state.update(chart="ready", activities=[{**activity, "id": "job-after"}] if resumed else [])
                await modules.put({"version": 3})
                await asyncio.sleep(.03)
                if resumed:
                    assert service.store.read(cid, WORKSPACE)["turns"][-1]["state"] == "waiting_business"
                    await ended.put({"workspaceId":WORKSPACE,"module":"chart","activityId":"job-after",
                                     "callId":"host-1","openTarget":target,"result":"completed"})
                    await asyncio.sleep(.03)
                result = await settled(service, cid)
                [done] = result["turns"]
                assert done["state"] == "completed"
                [job] = [card for card in result["cards"] if card["id"] == "activity_host-1_job-before"]
                assert job["state"] == ("completed" if resumed else "unknown")
            finally:
                service.activities.watcher.cancel()
                await asyncio.gather(service.activities.watcher, return_exceptions=True)
    asyncio.run(scenario())


def test_read_only_asks_before_a_change_and_runs_it_only_once_allowed(tmp_path):
    async def scenario():
        responses = [call("chart.read_watchlist", {"watchlistId": "W1"}, "c1"),
                     call("chart.add_to_watchlist", {"symbol": "BTC"}, "c2"), AIMessage(content="Added BTC.")]
        async with agent(tmp_path, responses) as (service, platform):
            conversation, snapshot = await start(service, "Add BTC to my watchlist", permission="read_only")
            # Reading ran without asking; the change waits for the user.
            assert [item["action"] for item in platform.calls] == ["chart.read_watchlist"]
            assert platform.calls[0]["workspaceId"] == WORKSPACE and platform.calls[0]["conversation"] == conversation
            [approval] = cards(snapshot, "approval")
            assert approval["state"] == "waiting" and approval["proposal"]["risks"] == ["write"]
            await service.decide(approval["id"], WORKSPACE, approval["version"], "allow")
            snapshot = await settled(service, conversation)
            assert [item["action"] for item in platform.calls] == ["chart.read_watchlist", "chart.add_to_watchlist"]
            assert platform.calls[1]["input"] == {"symbol": "BTC"}
            assert snapshot["turns"][-1]["state"] == "completed"
    asyncio.run(scenario())


def test_a_denied_action_never_runs_and_the_model_is_told(tmp_path):
    async def scenario():
        responses = [call("chart.delete_watchlist", {"watchlistId": "W1"}, "c1"), AIMessage(content="I did not delete it.")]
        async with agent(tmp_path, responses) as (service, platform):
            conversation, snapshot = await start(service, "Delete W1")
            [approval] = cards(snapshot, "approval")
            assert approval["proposal"]["risks"] == ["delete"]
            await service.decide(approval["id"], WORKSPACE, approval["version"], "deny", "Keep it")
            snapshot = await settled(service, conversation)
            assert platform.calls == []
            told = [message for message in service.model_runtime.chat_model.requests[-1] if message.type == "tool"]
            receipt = json.loads(told[-1].content)["approval"]
            assert receipt["decision"] == "deny" and receipt["reason"] == "Keep it"
            assert "user declined this call" in receipt["whatHappened"]
            assert snapshot["turns"][-1]["state"] == "completed"
    asyncio.run(scenario())


def test_each_waiting_call_can_resume_on_its_own_after_reopening_the_databases(tmp_path):
    async def scenario():
        batch = AIMessage(content="", tool_calls=[
            call("chart.delete_watchlist", {"watchlistId": name}, name).tool_calls[0] for name in ("A", "B", "C")])
        async with agent(tmp_path, [batch]) as (service, platform):
            conversation, snapshot = await start(service, "Delete the three lists")
            waiting = {a["input"]["watchlistId"]: a for a in cards(snapshot, "approval")}
            assert waiting.keys() == {"A", "B", "C"} and not platform.calls
        async with agent(tmp_path, [AIMessage(content="Done.")]) as (service, platform):
            for index, name in enumerate(("B", "A", "C"), start=1):
                card = waiting[name]
                await service.decide(card["id"], WORKSPACE, card["version"], "allow")
                snapshot = await settled(service, conversation)
                assert [item["input"]["watchlistId"] for item in platform.calls] == ["B", "A", "C"][:index]
                assert sum(a["state"] == "waiting" for a in cards(snapshot, "approval")) == 3 - index
                with pytest.raises(ContractError) as duplicate:
                    await service.decide(card["id"], WORKSPACE, card["version"], "allow")
                assert duplicate.value.code == "conflict"
            assert snapshot["turns"][-1]["state"] == "completed"
    asyncio.run(scenario())


def test_background_wake_has_a_run_id_and_does_not_impersonate_a_user_message(tmp_path):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="Morning report")]) as (service, _):
            line = service.store.create(WORKSPACE, model="model", title="Morning desk")["id"]
            cause = {"kind": "schedule", "routineId": "morning"}
            turn = await service.wake(line, WORKSPACE, "Review the morning data", cause=cause, request_id="occurrence")
            snapshot = await settled(service, line)
            assert service.usage_context(turn).work_run_id == turn["id"]
            assert turn["unattended"] and turn["wake"] == cause and snapshot["turns"][-1]["state"] == "completed"
            assert [item["role"] for item in cards(snapshot, "answer")] == ["assistant"]
            again = await service.wake(line, WORKSPACE, "Review the morning data", cause=cause, request_id="occurrence")
            assert again["id"] == turn["id"]
    asyncio.run(scenario())


def test_auto_and_full_run_writes_without_approval(tmp_path):
    async def scenario():
        for permission in ("auto", "full"):
            responses = [call("chart.add_to_watchlist", {"symbol": "BTC"}, "c1"), AIMessage(content="Added.")]
            async with agent(tmp_path / permission, responses) as (service, platform):
                _, snapshot = await start(service, "Add BTC", permission=permission)
                assert [item["action"] for item in platform.calls] == ["chart.add_to_watchlist"]
                assert not cards(snapshot, "approval")
    asyncio.run(scenario())


def test_allowing_a_whole_chat_never_covers_deleting_or_moving_money(tmp_path):
    async def scenario():
        responses = [call("chart.add_to_watchlist", {"symbol": "BTC"}, "c1"), AIMessage(content="Added."),
                     call("chart.add_to_watchlist", {"symbol": "BTC"}, "c2"), AIMessage(content="Added again.")]
        async with agent(tmp_path, responses) as (service, platform):
            conversation, snapshot = await start(service, "Add BTC", permission="read_only")
            [approval] = cards(snapshot, "approval")
            await service.decide(approval["id"], WORKSPACE, approval["version"], "allow_conversation")
            await settled(service, conversation)
            tool_result = next(m for m in reversed(service.model_runtime.chat_model.requests[-1]) if m.type == "tool")
            receipt = json.loads(tool_result.content)["approval"]
            assert receipt["decision"] == "allow_conversation"
            assert "user approved this call before it ran" in receipt["whatHappened"]
            await service.send(conversation, WORKSPACE, "Again", include_view=False)
            snapshot = await settled(service, conversation)
            assert len(platform.calls) == 2 and len(cards(snapshot, "approval")) == 1
    asyncio.run(scenario())
    for effects in (["delete"], ["write", "funds"]):
        assert not rememberable(proposal(effects))
    assert rememberable(proposal(["read", "write"]))


def test_permission_tiers_use_declared_effects_and_creation_history(tmp_path):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="unused")]) as (service, _):
            for tier, expected in (("read_only", ["run", "ask", "ask", "ask", "ask"]),
                                   ("auto", ["run", "run", "ask", "run", "run"]),
                                   ("full", ["run"] * 5)):
                conversation = service.store.create(WORKSPACE, model="model")["id"]
                turn = {"id": "turn", "conversationId": conversation, "workspaceId": WORKSPACE}
                service.update_conversation(conversation, WORKSPACE, {"permission": tier})
                effects = ["read", "write", "delete", "code", "external"]
                assert [service.decision(turn, "owner.update", [effect], target="user") for effect in effects] == expected
                service.changes.add(turn, "owner", {"callId": "make", "objectId": "new", "kind": "created"})
                assert service.decision(turn, "owner.update", ["delete"], target="new") == ("ask" if tier == "read_only" else "run")
                service.update_conversation(conversation, WORKSPACE, {"planMode": True})
                assert [service.decision(turn, "owner.update", [effect]) for effect in effects] == ["run"] + ["refuse"] * 4
    asyncio.run(scenario())


def test_each_model_remembers_its_last_explicit_level(tmp_path):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="unused")]) as (service, _):
            for identity in ("first", "second"):
                service.catalogue.put_model({"id": identity, "connectionId": "local", "model": identity + ":8b",
                                             "contextWindow": 32000, "tools": True, "thinking": True})
            conversation = service.store.create(WORKSPACE, model="first")["id"]

            def update(changes):
                return service.update_conversation(conversation, WORKSPACE, changes)
            assert update({"thinking": {"effort": "off"}})["thinking"] == {"effort": "off"}
            assert update({"modelId": "second"})["thinking"] is None  # Nothing remembered: the model's default.
            update({"thinking": {"effort": "on"}})
            assert update({"modelId": "first", "thinking": None})["thinking"] == {"effort": "off"}
            assert update({"modelId": "second"})["thinking"] == {"effort": "on"}
            with pytest.raises(ContractError, match="thinking levels"):
                update({"thinking": {"effort": "max"}})
            assert service.store.defaults()["modelThinking"] == {"first": "off", "second": "on"}
    asyncio.run(scenario())


def test_a_call_to_an_unknown_action_comes_back_to_the_model_instead_of_failing_the_turn(tmp_path):
    async def scenario():
        responses = [call("chart.no_such_action", {}, "c1"), AIMessage(content="Corrected.")]
        async with agent(tmp_path, responses) as (service, platform):
            conversation, _ = await start(service, "Do it", permission="read_only")
            snapshot = await settled(service, conversation)
            assert platform.calls == [] and not cards(snapshot, "approval")
            told = [message for message in service.model_runtime.chat_model.requests[-1] if message.type == "tool"]
            assert "no_such_action" in told[-1].content
            assert snapshot["turns"][-1]["state"] == "completed"
    asyncio.run(scenario())


def test_two_writes_into_a_new_folder_both_succeed_while_the_folder_appears_mid_resolve(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    backend = Folder(root_dir=root, virtual_mode=True)
    real, calls = ntpath._getfinalpathname, {"n": 0}

    def racing(path):
        # The other write creates the folder while this one resolves: CPython then keeps the \\?\ prefix.
        calls["n"] += 1
        if calls["n"] == 2:
            (root / "review").mkdir(exist_ok=True)
        return real(path)
    monkeypatch.setattr(ntpath, "_getfinalpathname", racing)
    first = backend.write("/review/analyze.py", "print(1)")
    monkeypatch.setattr(ntpath, "_getfinalpathname", real)
    second = backend.write("/review/bars.csv", "close\n1\n")
    assert first.error is None and second.error is None
    assert (root / "review" / "analyze.py").read_text() == "print(1)"
    assert (root / "review" / "bars.csv").read_text() == "close\n1\n"


def test_an_unexpected_tool_failure_goes_back_to_the_model_and_the_turn_goes_on(tmp_path, monkeypatch, caplog):
    previous = Platform.call

    async def failing(self, name, value, **kwargs):
        if name == "platform.ai.call" and value["action"] == "chart.read_watchlist":
            raise RuntimeError("socket closed")
        return await previous(self, name, value, **kwargs)
    monkeypatch.setattr(Platform, "call", failing)

    async def scenario():
        responses = [call("chart.read_watchlist", {"watchlistId": "W1"}, "c1"), AIMessage(content="Answered without it.")]
        async with agent(tmp_path, responses) as (service, _):
            conversation, snapshot = await start(service, "Read W1")
            told = [message for message in service.model_runtime.chat_model.requests[-1] if message.type == "tool"]
            assert "socket closed" in told[-1].content and told[-1].status == "error"
            assert snapshot["turns"][-1]["state"] == "completed"
            [process] = [card for card in cards(snapshot, "process_group") if card["id"] == "process_" + snapshot["turns"][-1]["id"]]
            assert [step["state"] for step in process["steps"]] == ["failed"]
            assert [card["text"] for card in cards(snapshot, "answer") if card["role"] == "assistant"] == ["Answered without it."]
    with caplog.at_level("ERROR", logger="modules.agent.backend.tools"):
        asyncio.run(scenario())
    [record] = [r for r in caplog.records if r.name == "modules.agent.backend.tools"]
    assert record.exc_info[0] is RuntimeError

def test_changes_made_while_a_turn_starts_are_kept_after_it_ends(tmp_path, monkeypatch):
    previous = Platform.workspace_folder
    changed = {}

    async def folder(self, workspace):
        # The user changes the chat while the turn is still being set up.
        if changed.get("conversation") and not changed.get("done"):
            changed["done"] = True
            changed["service"].update_conversation(changed["conversation"], WORKSPACE,
                                                   {"permission": "read_only", "planMode": True, "title": "Renamed"})
        return await previous(self, workspace)
    monkeypatch.setattr(Platform, "workspace_folder", folder)

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="Plan: read it.")]) as (service, _):
            conversation = service.store.create(WORKSPACE, model="model")["id"]
            changed.update(service=service, conversation=conversation)
            await service.send(conversation, WORKSPACE, "Read W1", include_view=False)
            snapshot = await settled(service, conversation)
            assert changed["done"] and snapshot["turns"][-1]["state"] == "completed"
            after = service.store.conversation(conversation, WORKSPACE)
            assert (after["permission"], after["planMode"], after["title"]) == ("read_only", True, "Renamed")
    asyncio.run(scenario())

def test_files_a_python_script_creates_changes_and_deletes_are_the_turns_changes_and_undo_and_redo(tmp_path, monkeypatch):
    async def scenario():
        folder = tmp_path / "workspace"
        folder.mkdir()
        (folder / "prices.csv").write_text("close\n1\n")
        (folder / "old.csv").write_text("stale\n")
        (folder / "history.bin").write_bytes(b"x" * 2_000_000)  # large, and the script leaves it alone
        script = ('from pathlib import Path\nPath("result.csv").write_text("vol\\n0.5\\n")\n'
                  'Path("prices.csv").write_text("close\\n2\\n")\nPath("old.csv").unlink(missing_ok=True)\n')
        (folder / "calc.py").write_text(script)
        responses = [native("run_python", {"path": "/workspace/calc.py"}, "run"), AIMessage(content="Done"),
                     native("run_python", {"path": "/workspace/calc.py"}, "again"), AIMessage(content="Again")]
        async with agent(tmp_path, responses) as (service, _):
            cid, snapshot = await start(service, "Compute it")
            [changes] = cards(snapshot, "changes")
            assert sorted((item["objectId"], item["kind"]) for item in changes["items"]) == [
                ("/workspace/old.csv", "deleted"), ("/workspace/prices.csv", "updated"), ("/workspace/result.csv", "created")]
            turn = snapshot["turns"][-1]["id"]
            await service.changes.revert(WORKSPACE, cid, turn, "undo")
            assert not (folder / "result.csv").exists()
            assert (folder / "prices.csv").read_text() == "close\n1\n" and (folder / "old.csv").read_text() == "stale\n"
            await service.changes.revert(WORKSPACE, cid, turn, "redo")
            assert (folder / "result.csv").read_text() == "vol\n0.5\n"
            assert (folder / "prices.csv").read_text() == "close\n2\n" and not (folder / "old.csv").exists()
            read, original = [], Path.read_bytes
            runs = iter(range(2, 10))
            monkeypatch.setattr(service.ctx, "run_token", lambda workspace, conversation: asyncio.sleep(0, {
                "url": "http://127.0.0.1:9/api/runs/call", "token": "token", "runId": f"run-{next(runs)}"}))
            monkeypatch.setattr(Path, "read_bytes", lambda path: read.append(path.name) or original(path))
            await service.send(cid, WORKSPACE, "Run it again", include_view=False)
            await settled(service, cid)
            # The unchanged large file is neither read again nor kept twice.
            assert "result.csv" in read and "history.bin" not in read
            stored = service.store.db.execute("SELECT COUNT(*) FROM file_images WHERE length(content)=2000000").fetchone()[0]
            assert stored == 1
    asyncio.run(scenario())

def test_plan_mode_offers_no_writing_tools_and_approving_sends_one_short_message(tmp_path):
    offered = []

    class Recording(ScriptedModel):
        def bind_tools(self, tools, **kwargs):
            offered.append({tool["function"]["name"] if isinstance(tool, dict) else tool.name for tool in tools})
            return self

    async def scenario():
        plan = "1. Read the last 90 days.\n2. Write volatility.csv."
        async with agent(tmp_path, [AIMessage(content=plan), AIMessage(content="Done.")]) as (service, _):
            service.model_runtime.chat_model = Recording(responses=[AIMessage(content=plan), AIMessage(content="Done.")])
            conversation = service.store.create(WORKSPACE, model="model")["id"]
            service.update_conversation(conversation, WORKSPACE, {"planMode": True})
            await service.send(conversation, WORKSPACE, "Compute BTC volatility and save a CSV", include_view=False)
            snapshot = await settled(service, conversation)
            assert offered and all(not {"write_file", "edit_file", "delete", "run_python", "bot_manage", "bot_delete", "remember"} & names
                                   for names in offered)
            assert all({"read_file", "talyx_call_action"} <= names for names in offered)
            [card] = cards(snapshot, "plan")
            await service.card_decision("plan_decision", {"conversationId": conversation, "cardId": card["id"], "accepted": True}, WORKSPACE)
            snapshot = await settled(service, conversation)
            users = [c["text"] for c in cards(snapshot, "answer") if c["role"] == "user"]
            assert users[-1] == "Implement the plan." and "volatility.csv" not in users[-1]
            assert "run_python" in offered[-1] and not service.store.conversation(conversation)["planMode"]
    asyncio.run(scenario())

def test_a_file_written_twice_in_a_turn_is_one_line_on_its_change_card(tmp_path):
    async def scenario():
        responses = [native("write_file", {"file_path": "/workspace/valuation.py", "content": "one"}, "w1"),
                     native("edit_file", {"file_path": "/workspace/valuation.py", "old_string": "one", "new_string": "two"}, "w2"),
                     native("write_file", {"file_path": "/workspace/scratch.txt", "content": "x"}, "w3"),
                     native("delete", {"file_path": "/workspace/scratch.txt"}, "w4"), AIMessage(content="Done")]
        async with agent(tmp_path, responses) as (service, _):
            cid, snapshot = await start(service, "Value the portfolio")
            [changes] = cards(snapshot, "changes")
            assert [(item["objectId"], item["kind"]) for item in changes["items"]] == [("/workspace/valuation.py", "created")]
            await service.changes.revert(WORKSPACE, cid, snapshot["turns"][-1]["id"], "undo")
            assert not (tmp_path / "workspace" / "valuation.py").exists()
    asyncio.run(scenario())
def test_each_request_has_the_guide_and_only_a_named_action_has_output_fields(tmp_path):
    async def scenario():
        responses = [native("talyx_find_actions", {"module": "chart"}, "find"),
                     native("talyx_read_action", {"name": "chart.read_watchlist"}, "read"),
                     AIMessage(content="Ready")]
        async with agent(tmp_path, responses) as (service, platform):
            async def catalog(name, args, **kwargs):
                if name == "platform.ai.guide":
                    return {"text": "Full fixture guide with a second paragraph.\n\nExact instrument conventions.", "nextCursor": None}
                result = await platform.call(name, args, **kwargs)
                if name == "platform.ai.catalog" and not args:
                    result["modules"].append({"module": "platform", "guide": None, "docs": []})
                    result["modules"][0]["guide"] = {"name": "Charts", "description": "Charts"}
                    result["modules"][0]["docs"] = [{"topic": "indicators", "title": "Write a chart indicator"}]
                if name == "platform.ai.catalog" and args.get("module") == "platform":
                    result["tools"] = [{"name": "platform.panels.open", "description": "sample.desk: Inspect samples.",
                                        "input": {"type": "object"}, "effects": ["write"], "task": False,
                                        "examples": [], "errors": [], "effectBranches": None, "target": None}]
                return result
            service.ctx.call = catalog
            await start(service, "Look up the chart action")
            calls = service.model_runtime.chat_model.requests
            assert len(calls) == 3
            for messages in calls:
                system = str(messages[0].content)
                assert "Calculate derived numbers" in system and "using run_python" in system
                assert "each comparison on its own basis" in system and "including cross-checks and file descriptions" in system
                assert "numerator and denominator" in system and "event records for event details" in system
                assert "check the timestamps across that period in the stated time zone" in system
                assert "read them back" in system and "final saved object or run snapshot" in system
                assert "Exact instrument conventions." in system
                assert system.count("sample.desk: Inspect samples.") == 1
                assert '(read first: platform.ai.guide {module:"chart", topic}):\n- indicators: Write a chart indicator' in system
                assert "<workspace_instructions>" not in system
                assert "<available_skills>" not in system
            found = json.loads(next(m.content for m in calls[1] if m.type == "tool" and m.tool_call_id == "find"))
            assert all(set(entry) == {"name","summary"} for entry in found["actions"])
            detail = json.loads(next(m.content for m in calls[2] if m.type == "tool" and m.tool_call_id == "read"))
            assert detail["outputFields"] == [{"name":"ok","type":"boolean","unit":None}]
    asyncio.run(scenario())


def test_csv_counts_records_and_reading_stdin_uses_the_original_user_input(tmp_path):
    source = '检查行数。\ntime,symbol,note\n2023-09-13,A,"line one\nline two"\n2023-11-09,B,x\n2023-11-09,C,y\n'
    facts = table_facts(source)
    assert facts["dataRows"] == 3 and facts["lastValue"] == "2023-11-09" and facts["rowsWithLastValue"] == 2
    assert table_facts("a,b\n1,2\n3") is None
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="ok")]) as (service, _):
            cid = service.store.create(WORKSPACE, model="model")["id"]
            turn = service.store.new_turn(cid, WORKSPACE, source)
            attachment = service.attach(cid, WORKSPACE, "input.csv", "text/csv", source.encode())
            service.model_runtime.chat_model.responses = [
                native("run_python", {"code":"import sys\nprint(sys.stdin.read())",
                                      "stdin_source":"message:"+turn["messageId"]}, "pasted"),
                native("run_python", {"code":"import sys\nprint(sys.stdin.read())","stdin_source":"attachment:"+attachment["id"]}, "attached"),
                AIMessage(content="done")]
            next_run = 0

            async def token(*args, **kwargs):
                nonlocal next_run
                next_run += 1
                return {"url":"http://127.0.0.1:9", "token":"fixture", "runId":f"stdin-{next_run}"}
            service.ctx.run_token = token
            service.schedule(cid)
            await settled(service, cid)
            results = service.model_runtime.chat_model.requests[-1]
            assert '"dataRows":3' in str(results) and "rowsWithLastValue" in str(results)
            tool_results = [m for m in results if m.type == "tool"]
            assert not list((tmp_path/"workspace").rglob("*.py"))
            assert len(tool_results) == 2
            for item in tool_results:
                assert json.loads(item.content)["stdout"].strip() == source.strip()
    asyncio.run(scenario())
