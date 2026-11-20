from tests.platform.harness import real_modules, run
from tests.platform.test_processes import restart


def test_restoring_a_developer_save_after_restart_replaces_later_disk_edits(tmp_path):
    async def scenario():
        path = tmp_path / "project" / "model.py"
        path.parent.mkdir()
        original = b"\xef\xbb\xbfcoefficient = 1\r\n"
        path.write_bytes(original)
        async with real_modules(tmp_path, "developer") as platform:
            ws = await platform.workspace("Editor")
            caller = {"module":"agent","origin":"module","workspaceId":ws}
            async def ai(name, args):
                return await platform.call("platform.ai.call", {"workspaceId":ws,"client":"editor","conversation":"saved",
                    "action":name,"input":args}, caller=caller)
            doc = (await ai("developer.documents.open", {"source":{"kind":"file","uri":path.as_uri()}}))["result"]
            edited = (await ai("developer.documents.edit", {"documentId":doc["documentId"],"expectedVersion":doc["version"],
                "content":"coefficient = 2\n"}))["result"]
            saved = await ai("developer.documents.save", {"documentId":doc["documentId"],"expectedVersion":edited["version"]})
            assert saved["result"]["status"] == "saved"
            written = path.read_bytes()
            path.write_bytes(b"coefficient = 3\n")
            await restart(platform, "developer")
            for direction, expected in (("undo",original),("redo",written)):
                await ai("platform.changes.revert", {"callId":saved["callId"],"direction":direction})
                assert path.read_bytes() == expected
    run(scenario())


def test_agent_file_extension_dispatches_the_existing_file_to_the_developer_panel(tmp_path, monkeypatch):
    async def scenario():
        path = tmp_path / "analysis.py"
        path.write_text("answer = 42\n", encoding="utf-8")
        async with real_modules(tmp_path, "agent", "developer") as platform:
            ws = await platform.workspace("Research")
            opened = []
            async def window(caller, operation, value):
                opened.append((caller["workspaceId"], operation, value))
                return {"panelId":"editor"}
            monkeypatch.setattr(platform.host, "window_call", window)
            result = await platform.call("agent.open_file:developer:editor.open", {"path":str(path)},
                                         caller={"origin":"module","module":"agent","workspaceId":ws})
            assert result == {} and opened == [(ws, "open_target", {
                "module":"developer","panelType":"main","objectId":"file:" + path.as_uri()})]
    run(scenario())
