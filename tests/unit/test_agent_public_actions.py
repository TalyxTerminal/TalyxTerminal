"""Exercise the declared operations against the same services as the panel."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from talyx_sdk import ContractError

from modules.agent.backend import entry
from tests.unit.test_agent_runtime import WORKSPACE, agent, native, settled


def public(name, workspace=WORKSPACE):
    action = entry.module.actions[name]

    async def invoke(op, **fields):
        value = action.input.model_validate({"operation": {"op": op, **fields}})
        return (await action.handler(value, SimpleNamespace(workspace_id=workspace)))["result"]
    return invoke


def test_chat_public_lifecycle_files_children_and_revert(tmp_path, monkeypatch):
    async def scenario():
        responses = [native("write_file", {"file_path": "/workspace/note.txt", "content": "first"}, "write"),
                     AIMessage(content="Written")]
        async with agent(tmp_path, responses) as (service, platform):
            monkeypatch.setattr(entry, "service", service)
            chat = public("chat")
            cid = (await chat("create", title="Research", modelId="model"))["id"]
            assert (await chat("list"))["items"][0]["id"] == cid
            await chat("update", conversationId=cid, changes={"pinned": True})
            await service.send(cid, WORKSPACE, "Write a note", include_view=False)
            await settled(service, cid)
            snapshot = await chat("read", conversationId=cid)
            turn = snapshot["turns"][0]
            assert snapshot["conversation"]["pinned"] and await chat("search", query="Write a note") == [cid]
            assert await chat("permission_rules", conversationId=cid) == []
            assert (await chat("find_files", query="note"))[0]["path"] == "/workspace/note.txt"
            ref = service.references.save(turn, "source", {"url": "https://example.org"}, "source")
            assert (await chat("reference", referenceId=ref["id"]))["target"] == ref["target"]
            note = Path(await platform.workspace_folder(WORKSPACE)) / "note.txt"
            await chat("revert", conversationId=cid, turnId=turn["id"], direction="undo")
            assert not note.exists()
            await chat("revert", conversationId=cid, turnId=turn["id"], direction="redo")
            assert note.read_text() == "first"
            saved = await chat("export", conversationId=cid)
            assert "Written" in (note.parent / saved["name"]).read_text(encoding="utf8")
            answer = next(c for c in snapshot["cards"] if c.get("role") == "assistant")
            fork = await chat("fork", conversationId=cid, itemId=answer["id"])
            await chat("delete", conversationId=fork["id"])
            child = service.children.save({"id":"child", "turnId":turn["id"], "conversationId":cid,
                                          "workspaceId":WORKSPACE, "state":"running", "result":None})
            assert (await chat("child_read", conversationId=cid, childRunId=child["id"]))["child"]["state"] == "running"
            assert (await chat("child_stop", conversationId=cid, childRunId=child["id"]))["state"] == "cancelled"
            with pytest.raises(ContractError, match="workspace"):
                await public("chat", "elsewhere")("read", conversationId=cid)
    asyncio.run(scenario())


def test_bot_actions_and_replacing_a_deleted_default_model(tmp_path, monkeypatch):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="Received")]) as (service, _):
            monkeypatch.setattr(entry, "service", service)
            service.ctx.process.module.events["settings_changed"] = SimpleNamespace(payload=None)
            bot, configure = public("bot"), public("configure")
            await configure("set_defaults", scope="global", kind="chat", changes={"modelId": "model"})
            await configure("set_defaults", scope="workspace", kind="bot", changes={"modelId":"model", "childModelId":"model"})
            await configure("delete_model", id="model")
            assert service.store.defaults()["modelId"] is None
            assert "modelId" not in service.store.overrides(WORKSPACE, "bot")
            await configure("put_model", id="readded", connectionId="local", model="tool-model", contextWindow=32000, tools=True)
            one = (await bot("create", name="First", skills=["review"]))["id"]
            two = (await bot("create", name="Second"))["id"]
            assert (await bot("inspect", id=one))["model"] == "readded"
            assert (await bot("inspect", id=one))["skills"] == ["review"]
            group = (await bot("create_group", members=[one,two]))["id"]
            await bot("section", name="Desk")
            await bot("patch", id=group, changes={"section":"Desk", "name":"Reports"})
            assert (await bot("list"))["groups"][0]["name"] == "Reports"
            await bot("upload", id=one, name="report.txt", data="b2s=")
            assert (await bot("inspect", id=one))["knowledge"] == ["/workspace/report.txt"]
            recipients = await bot("recipients", id=one, text="Read the report")
            with pytest.raises(ContractError, match="through their bot or group"):
                await public("chat")("delete", conversationId=recipients[one])
            attachments = await public("chat")("attach_paths", conversationId=recipients[one], paths=["/workspace/report.txt"])
            await bot("send", id=one, text="Read the report", attachments={one:attachments})
            await settled(service, recipients[one])
            assert service.store.db.execute("SELECT COUNT(*) FROM attachments WHERE conversation=?", (recipients[one],)).fetchone()[0] == 1
            thread = await bot("read", id=one)
            assert any(b.get("snapshot") for b in thread["blocks"])
            routine = await bot("save_routine", botId=one, name="Daily", instruction="Check", when="daily",
                                zone="UTC", rrule="FREQ=DAILY;BYHOUR=9")
            await bot("toggle_routine", id=routine["id"], active=False)
            assert (await bot("inspect", id=routine["id"]))["active"] is False
            await bot("delete_routine", id=routine["id"])
            await bot("delete", id=group)
            assert (await bot("list"))["groups"] == []
    asyncio.run(scenario())


def test_public_settings_scope_credentials_memories_and_usage(tmp_path, monkeypatch):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="OK")]) as (service, platform):
            monkeypatch.setattr(entry, "service", service)
            service.ctx.process.module.events["settings_changed"] = SimpleNamespace(payload=None)
            configure = public("configure")
            config = await configure("read")
            assert config["models"][0]["id"] == "model" and config["connections"][0]["credential"] == {"configured":False}
            fields = dict(id="relay", name="Relay", protocol="openai", providerId="openai", kind="relay",
                          baseUrl="https://relay.example/v1")
            first = await configure("put_connection", **fields)
            same = await configure("put_connection", **{**fields, "name":"Renamed"})
            changed = await configure("put_connection", **{**fields, "baseUrl":"https://new.example/v1"})
            assert first["secretRef"] == same["secretRef"] != changed["secretRef"]
            with pytest.raises(ValidationError):
                await configure("put_connection", **fields, secretRef="another.account")
            with pytest.raises(ValidationError):
                await configure("credential", reference="test", value="secret")
            await configure("delete_connection", id="relay")
            long_rule = "Formatting rule. " * 2000
            await configure("write_instructions", text=long_rule)
            spec = entry.module.actions["configure"]
            request = spec.input.model_validate({"operation":{"op":"read_instructions"}})
            snapshot = (await spec.handler(request, SimpleNamespace(workspace_id=WORKSPACE)))["file"]
            await configure("write_instructions", text="Workspace rule")
            data = Path(snapshot["path"]).read_bytes()
            assert json.loads(data) == long_rule
            assert hashlib.sha256(data).hexdigest() == snapshot["sha256"] and len(data) == snapshot["bytes"]
            assert await configure("read_instructions") == "Workspace rule"
            assert await configure("read_instructions", scope="global") == ""
            assert (await configure("folders"))["workspaceFolder"] == await platform.workspace_folder(WORKSPACE)
            cid = service.store.create(WORKSPACE)["id"]
            mem = service.memory.write(WORKSPACE, cid, "First", scope="workspace")["memory"]
            [listed] = (await configure("memory_list"))["items"]
            assert listed["id"] == mem["id"]
            updated = (await configure("memory_edit", memoryId=mem["id"], expectedRevision=listed["revision"], body="Next"))["memory"]
            assert updated["sourceKind"] == "inference"
            with pytest.raises(ContractError, match="changed"):
                await configure("memory_delete", memoryId=mem["id"], expectedRevision=listed["revision"])
            await configure("memory_delete", memoryId=mem["id"], expectedRevision=updated["revision"])
            assert (await configure("memory_list", status="deleted"))["items"][0]["id"] == mem["id"]
            await configure("usage", timezone="UTC", monthlyLimit="10", currency="CNY")
            assert (await configure("read_usage"))["settings"]["monthlyLimit"] == "10"
            assert (await configure("spending"))["spent"] == "0"
    asyncio.run(scenario())
