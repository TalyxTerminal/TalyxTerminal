"""Workspace copies own their project and run ids, with immutable completed data shared on disk."""
import asyncio
import json
import os

from modules.quant.backend.workspace import copy_workspace, delete_workspace
from tests.unit.quant_model_fakes import model_world, settled


def test_a_copied_model_keeps_its_completed_arrays_when_the_original_workspace_is_deleted(tmp_path, monkeypatch):
    async def scenario():
        world = model_world(tmp_path, monkeypatch, windows=2)
        records = world.shell.store.records
        try:
            model = await world.make()
            run = (await world.models.train(model))["runs"][0]["id"]
            await settled(world.models, run)
            source = world.models.directory(run)
            material = source / "output" / "pred.npy"
            invocation = source / "engine" / "invocation.json"
            invocation.parent.mkdir(exist_ok=True)
            invocation.write_text(json.dumps({"objectId":model,"runId":run,"output":str(source/"output")}), encoding="utf-8")
            copy_workspace(records, world.shell.library, "ws", "clone")
            mapping = dict(records.db.execute("SELECT original,copy FROM workspace_copies WHERE workspace='clone'"))
            copied = world.models.directory(mapping[run])
            saved = json.loads((copied/"engine"/"invocation.json").read_bytes())
            assert saved == {"objectId":mapping[model],"runId":mapping[run],"output":str(copied/"output")}
            assert records.get("qrun", mapping[run])["objectId"] == mapping[model]
            assert os.path.samefile(material, copied/"output"/"pred.npy")
            before = material.read_bytes()
            delete_workspace(records, world.shell.library, "ws", False)
            assert not material.exists() and (copied/"output"/"pred.npy").read_bytes() == before
        finally:
            await world.models.close()
            records.close()
    asyncio.run(scenario())
