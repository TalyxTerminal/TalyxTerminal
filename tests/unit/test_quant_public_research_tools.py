"""Call the research tools through their generated SDK input and output codecs."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend.kernels import KernelSession
from tests.unit.quant_public import public_actions
from tests.unit.test_quant_factor_runs import services


def test_notebook_document_extraction_and_kernel_operations(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        act = public_actions(ctx, shell, monkeypatch)
        obj = await act("create_object", {"kind": "notebook"})
        identity = obj["objectId"]
        read = {"objectId": identity, "part": "notebook"}
        doc = await act("read_object", read)
        cell = doc["notebook"]["cells"][0]
        cell["source"] = "price = 123"
        doc["notebook"]["metadata"] = {"unit": "USD"}
        await act("save_notebook", {"objectId": identity, "expectedVersion": doc["version"], "notebook": doc["notebook"]})
        assert (await act("read_object", read))["notebook"] == doc["notebook"]
        with pytest.raises(ContractError, match="changed"):
            await act("save_notebook", {"objectId": identity, "expectedVersion": "stale", "notebook": doc["notebook"]})
        draft = await act("preview_notebook_extract", {"notebookId": identity, "cells": [cell["id"]], "kind": "strategy"})
        assert "price = 123" in draft["code"]
        kernels = shell.notebooks.kernels
        control = {"objectId": identity}
        assert (await act("control_notebook_kernel", {**control, "op": "status"}))["state"] == "none"
        session = KernelSession(kernels, "ws", shell.store.read(identity))
        kernels.sessions[kernels.key(identity)] = session
        session.state = "idle"
        session.ready.set()
        session.client = Mock()
        session.client.execute.return_value = "query"
        session.event = Mock()
        session.restart = AsyncMock()
        table = asyncio.create_task(act("read_notebook_variables", {"notebookId": identity, "name": "prices"}))
        await asyncio.sleep(0)
        session.read_table("query", {"user_expressions": {"v": {"data": {"text/plain": repr('{"rows":[[123]]}')}}}})
        assert (await table)["table"]["rows"] == [[123]]
        await act("control_notebook_kernel", {**control, "op": "interrupt"})
        session.event.set.assert_called_once()
        await act("control_notebook_kernel", {**control, "op": "restart"})
        session.restart.assert_awaited_once()
        assert (await act("control_notebook_kernel", {**control, "op": "stop"}))["state"] == "none"

    asyncio.run(scenario())


def test_frozen_library_materials_and_version_notebook(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        act = public_actions(ctx, shell, monkeypatch)
        library = shell.library
        library.store.commit_versions([{"name": "Signal", "kind": "factor", "versionId": "v1",
            "meta": {"fields": ["value"], "formula": "$close"}, "rows": [], "checks": {},
            "objectId": "o", "runId": "r", "workspaceId": "ws", "state": "updating", "cursor": None}])
        root = library.version_directory("v1") / "report"
        root.mkdir(parents=True, exist_ok=True)
        index = {"artifacts": [{"name": "rows.parquet"}]}
        (root / "result-index.json").write_text(json.dumps(index))
        pd.DataFrame({"value": [12, 20]}).to_parquet(root / "rows.parquet")
        async def material(part, **args):
            return await act("read_library_material", {"versionId": "v1", "part": part, **args})
        assert await material("report") == index
        large = {**index, "notes": "full report " * 3000}
        (root / "result-index.json").write_text(json.dumps(large))
        handed = (await material("report"))["file"]
        assert json.loads(Path(handed["path"]).read_text(encoding="utf8")) == large
        assert await material("source") == {"files": ["formula"]}
        assert (await material("source", name="formula"))["content"] == "$close"
        assert (await material("table", query={"name": "rows.parquet", "offset": 1}))["rows"] == [{"value": 20}]
        exported = (await material("file", name="rows.parquet"))["file"]
        assert Path(exported["path"]).read_bytes() == (root / "rows.parquet").read_bytes()
        with pytest.raises(ContractError):
            await material("file", name="../private")
        made = await act("create_object", {"kind": "notebook", "fromVersion": "v1"})
        doc = await act("read_object", {"objectId": made["objectId"], "part": "notebook"})
        assert "q.library.read('Signal', version=1)" in doc["notebook"]["cells"][0]["source"]

    asyncio.run(scenario())


def test_model_object_and_preparation_tools(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        act = public_actions(ctx, shell, monkeypatch)
        presets = await act("list_presets")
        assert {row["kind"] for row in presets["items"]} == {"factor", "model", "strategy"}
        (shell.common.root / "helpers.py").write_text("n = 1")
        assert (await act("list_common_code"))["files"] == ["helpers.py"]
        obj = await act("create_object", {"kind": "model"})
        identity = obj["objectId"]
        assert (await act("check_parameters", {"objectId": identity, "code": "broken("}))["errors"]
        shell.store.save(shell.store.read(identity), settings={**shell.store.read(identity)["settings"], "mode": "code"})
        assert (await act("reset_model", {"objectId": identity}))["mode"] == "standard"
        monkeypatch.setattr(shell, "data_defaults", AsyncMock(return_value={"account": {"currency": "USD"}}))
        assert (await act("read_object", {"objectId": identity, "part": "dataDefaults"}))["options"]["account"]["currency"] == "USD"
        shell.models.compare_figure = Mock(return_value={"data": [{"name": "1"}]})
        shell.models.figures = Mock(return_value={"figures": {"ic": {}}})
        assert (await act("read_model_figures", {"runIds": ["a", "b"]}))["data"][0]["name"] == "1"
        assert "ic" in (await act("read_model_figures", {"runId": "a", "keys": ["ic"], "compareTo": "b"}))["figures"]
        shell.models.figures.assert_called_once_with("a", ["ic"], "b")
        shell.models.run_next = AsyncMock(return_value={"id": "a", "state": "queued"})
        assert await act("promote_model_run", {"runId": "a"}) == {"runId": "a", "state": "queued"}
        for op, env, method in [("install", "research", "start_install"), ("cancel", "research", "cancel"), ("cancel", "deep", "cancel_deep")]:
            fake = Mock(return_value={"done": True}) if op == "install" else AsyncMock(return_value={"done": True})
            monkeypatch.setattr(shell.packages, method, fake)
            assert await act("manage_package_install", {"op": op, "environment": env}) == {"done": True}
            fake.assert_called_once_with()
        shell.packages.status = lambda: {"requirements": ["pandas"]}
        assert (await act("read_environments", {"part": "packages"}))["requirements"] == ["pandas"]

    asyncio.run(scenario())


def test_run_snapshots_resume_selection_and_estimates(tmp_path, monkeypatch):
    async def scenario():
        ctx, shell, _ = services(tmp_path, monkeypatch, [])
        act = public_actions(ctx, shell, monkeypatch)
        for identity, kind, part, expected in [
            ("a", "allocation", "code", "files"), ("f", "factor", "fetch", "rows"),
            ("m", "model", "valuesProgress", "stage"), ("s", "strategy", "inputVersions", "library")
        ]:
            shell.runs.records.put("qrun", {"id": identity, "objectKind": kind, "runKind": "run", "allocation": kind == "allocation",
                "data": {"library": [{"name": "Prices", "versionId": "v1"}]}})
            if kind == "allocation":
                source = shell.allocation.directory(identity) / "source"
                source.mkdir(parents=True)
                (source / "allocation_standard.py").write_text("capital = 10")
            result = (await act("read_run", {"runId": identity, "part": part}))["data"]
            assert expected in result
            if kind == "allocation":
                assert result["files"][0]["text"] == "capital = 10"
            if kind == "strategy":
                assert result["library"] == [{"name": "Prices", "versionId": "v1"}]
        shell.factors.retry = AsyncMock(return_value={"id": "f", "state": "queued"})
        assert (await act("resume_run", {"runId": "f"}))["runId"] == "f"
        shell.factors.retry.assert_awaited_once_with("f")
        shell.sweeps.select = Mock(return_value={"selected": 3})
        assert await act("select_sweep_cell", {"runId": "s", "cell": 3}) == {"runId": "s", "selected": 3}
        shell.sweeps.estimate = AsyncMock(return_value={"combinations": 2})
        axes = [{"name": "slow", "values": [50, 100]}]
        for options, kind in [({}, "sweep"), ({"kind": "walk", "pickOn": 9, "trade": 3}, "walk")]:
            assert await act("estimate_sweep", {"objectId": "s", "axes": axes, **options}) == {"combinations": 2}
            assert shell.sweeps.estimate.await_args.args[-1] == kind

    asyncio.run(scenario())
