"""Developer's recovery, history and project operations through the running module's public contract."""
import hashlib

import pytest
from talyx_sdk import ContractError

from tests.platform.harness import real_modules, run


def test_editor_workflows_through_public_actions(tmp_path):
    async def scenario():
        folder = tmp_path / "source"
        folder.mkdir()
        file = folder / "main.py"
        original = b"\xef\xbb\xbfone\r\ntwo\r\n"
        file.write_bytes(original)
        async with real_modules(tmp_path, "developer") as platform:
            async def invoke(action, **args):
                return await platform.call("developer." + action, args)

            project = await invoke("projects.open", directory=str(folder))
            project_id = project["projectId"]
            assert (await invoke("projects.module_status", projectId=project_id)) == {"module": None, "runtime": None}
            (folder / "talyx.module.json").write_text('{"id":"developer","name":"Developer"}', encoding="utf-8")
            status = await invoke("projects.module_status", projectId=project_id)
            assert status["module"]["name"] == "Developer"
            assert status["runtime"]["enabled"] and status["runtime"]["state"] == "ready"

            help_items = (await invoke("help"))["entries"]
            assert any(item["support"] == "TALYX SDK" and item["source"]["sourceKey"] == "process" for item in help_items)
            opened = await invoke("documents.open", source={"kind": "file", "uri": file.as_uri()})
            doc_id = opened["documentId"]
            edited = await invoke("documents.edit", documentId=doc_id, expectedVersion=opened["version"], content="mine")
            assert [item["documentId"] for item in (await invoke("documents.list"))["documents"]] == [doc_id]
            saved = await invoke("documents.create_checkpoint", submissionId="one", documents=[
                {"documentId": doc_id, "expectedVersion": edited["version"]}])
            listed = await invoke("documents.read_checkpoint", checkpoint=saved["checkpoint"])
            text = await invoke("documents.read_checkpoint", checkpoint=saved["checkpoint"], sourceKey=listed["documents"][0]["sourceKey"])
            assert (text["content"], text["baseContent"]) == ("mine", original.decode("utf-8-sig"))
            removal = {"projectId": project_id, "expectedRootUri": project["rootUri"], "rootIdentity": project["rootIdentity"]}
            with pytest.raises(ContractError) as exc:
                await invoke("projects.remove", **removal, mode="forget")
            assert exc.value.code == "conflict"

            file.write_bytes(b"external")
            versions = await invoke("documents.read_versions", documentId=doc_id)
            assert (versions["content"], versions["baseContent"], versions["externalContent"]) == ("mine", original.decode("utf-8-sig"), "external")
            with pytest.raises(ContractError) as exc:
                await invoke("documents.resolve", documentId=doc_id, mode="external", expectedVersion=edited["version"],
                             expectedSourceVersion=opened["uri"])
            assert exc.value.code == "conflict"
            await invoke("documents.resolve", documentId=doc_id, mode="mine", expectedVersion=versions["version"],
                         expectedSourceVersion=versions["sourceVersion"])
            assert (await invoke("documents.save", documentId=doc_id))["status"] == "saved"
            assert file.read_bytes() == b"mine"

            file_ref = {"projectId": project_id, "path": file.name}
            revisions = (await invoke("projects.file_history", **file_ref))["revisions"]
            first = revisions[-1]["revisionId"]
            assert (await invoke("projects.file_history", **file_ref, revisionId=first))["content"] == original.decode("utf-8-sig")
            assert (await invoke("projects.restore_revision", **file_ref, revisionId=first, expectedVersion="stale"))["status"] == "conflict"
            result = await invoke("projects.restore_revision", **file_ref, revisionId=first, expectedVersion=hashlib.sha256(b"mine").hexdigest())
            assert result["status"] == "saved" and file.read_bytes() == original
            assert (await invoke("documents.read", documentId=doc_id))["content"] == original.decode("utf-8-sig")
            assert (await invoke("projects.remove", **removal, mode="forget"))["removed"]
            assert file.is_file()
            assert project_id not in [item["projectId"] for item in (await invoke("projects.list"))["projects"]]

    run(scenario())
