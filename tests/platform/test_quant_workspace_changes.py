"""Quant and Developer restore a shared project, and a copied Quant panel selects the copy's object."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from tests.platform.harness import real_modules, route, run


def test_public_quant_change_restores_its_documents_and_workspace_copy_has_its_own_source(tmp_path):
    async def scenario():
        async with real_modules(tmp_path, "quant", "developer") as platform:
            ws = await platform.workspace("Research")
            other = await platform.workspace("Other")
            caller = {"origin":"module", "module":"agent", "workspaceId":ws}

            async def ai(name, value, workspace=ws):
                return await platform.call("platform.ai.call", {"workspaceId":workspace, "client":"study",
                    "conversation":"notes", "action":name, "input":value}, caller={**caller,"workspaceId":workspace})

            created = await ai("quant.create_object", {"kind":"model","name":"Value forecast"})
            key = created["result"]["objectId"]
            assert [i["name"] for i in (await ai("quant.list_objects", {"scope":"all"}))["result"]["items"]] == ["Value forecast"]
            assert not (await ai("quant.list_objects",{},other))["result"]["items"]
            assert (await ai("quant.list_objects",{"scope":"all"},other))["result"]["items"][0]["objectId"] == key
            assert (await ai("quant.read_object",{"objectId":key},other))["result"]["objectId"] == key
            await ai("quant.update_object",{"objectId":key,"folder":"Shared"},other)
            read = await platform.call("quant.read_object", {"objectId":key}, workspace=ws)
            source = Path(url2pathname(urlsplit(read["entryUri"]).path))
            source.write_bytes(b"original = True\r\n")
            original = source.read_bytes()
            generated = await ai("quant.write_as_code", {"objectId":key})
            written = source.read_bytes()
            assert written != original and generated["result"]["mode"] == "code"
            changed = (await ai("platform.changes.list", {"callIds":[generated["callId"]]}))["result"]
            assert any(i["openTarget"]["module"] == "developer" for i in changed if i["openTarget"])
            source.write_bytes(b"manually edited after the AI call\r\n")
            for direction, text, mode in (("undo",original,"standard"),("redo",written,"code")):
                await ai("platform.changes.revert", {"callId":generated["callId"], "direction":direction})
                assert source.read_bytes() == text
                detail = (await ai("quant.read_object", {"objectId":key}))["result"]
                assert detail["mode"] == mode

            await platform.call("platform.workspace.save", {"name":"Research","workspace":{"panels":[
                {"kind":"module:quant", "state":{"objectId":key}}]}}, workspace=ws)
            copied = await platform.call("platform.workspace.copy", {"workspaceId":ws})
            copy = copied["workspaceId"]
            await platform.call("platform.workspace.open", {"snapshotId":copied["snapshotId"]})
            adopted = await route(platform,"quant","POST","/workspace/adopt",copy,{"objectId":key})
            new = adopted["objectId"]
            assert key != new
            clone = await platform.call("quant.read_object", {"objectId":new}, workspace=copy)
            copied_path = Path(url2pathname(urlsplit(clone["entryUri"]).path))
            assert copied_path != source and copied_path.read_bytes() == written
            await ai("quant.update_object", {"objectId":new,"name":"Copy only"}, copy)
            assert (await ai("quant.read_object", {"objectId":key}))["result"]["name"] == "Value forecast"
            assert any("Copy only" in row for row in await platform.call("platform.workspace.delete_preview", {"workspaceId":copy}))
            await platform.call("platform.workspace.delete", {"snapshotId":copied["snapshotId"],"expectedRevision":copied["revision"]})
            assert not copied_path.exists() and source.read_bytes() == written

            specimen = await ai("quant.create_object", {"kind":"model","name":"Temporary model"})
            old = specimen["result"]["objectId"]
            for direction in ("undo","redo","redo"):
                await ai("platform.changes.revert", {"callId":specimen["callId"],"direction":direction})
            visible = (await ai("quant.list_objects", {}))["result"]["items"]
            restored = [row for row in visible if row["name"] == "Temporary model"]
            assert len(restored) == 1 and restored[0]["objectId"] != old

    run(scenario())


def test_ai_reads_every_item_in_a_large_quant_directory_across_pages(tmp_path):
    async def scenario():
        async with real_modules(tmp_path, "quant") as platform:
            ws = await platform.workspace("Research")
            for index in range(50):
                await platform.call("quant.create_object", {"kind":"model", "name":f"Model {index} · " + "动量" * 25},
                                    workspace=ws)
            args, names = {}, []
            while True:
                page = await platform.call("platform.ai.call", {"workspaceId":ws,"client":"study","conversation":"directory",
                    "action":"quant.list_objects","input":args}, caller={"origin":"module","module":"agent","workspaceId":ws})
                result = page["result"]
                assert len(json.dumps(result,ensure_ascii=False,separators=(",",":"))) <= 18000
                names.extend(item["objectId"] for item in result["items"])
                if result["nextCursor"] is None:
                    break
                args["cursor"] = result["nextCursor"]
            assert len(set(names)) == 50
    run(scenario())

def test_starting_another_workspaces_object_records_the_run_in_the_calling_workspace(tmp_path):
    from tests.platform.test_quant_market import modules_root

    async def scenario():
        async with real_modules(tmp_path, "quant", root=modules_root(tmp_path)) as platform:
            owner, caller = await platform.workspace("Owner"), await platform.workspace("Reader")
            created = await platform.call("quant.create_object", {"kind":"allocation", "name":"Cash equities", "data":{"symbols":["US:MSFT"], "start":"2024-06-03", "end":"2024-06-05", "timeframe":"1d"}}, workspace=owner)
            obj = {"objectId": created["objectId"]}
            receipt = await platform.call("quant.start_run", obj, workspace=caller)
            assert receipt["runId"]
            assert not (await platform.call("quant.list_runs", obj, workspace=owner))["items"]
            runs = await platform.call("quant.list_runs", obj, workspace=caller)
            assert [(row["runId"], row["workspaceId"]) for row in runs["items"]] == [(receipt["runId"], caller)]
    run(scenario())
