"""The real Quant on the new platform, with the test Market and without Developer: it starts in its own process, its
public actions reach the AI catalog, its private routes serve the panel, and it keeps answering while Market is down."""

from __future__ import annotations

import json
from pathlib import Path

from tests.platform.harness import real_modules, route, run

CONTRACT = json.loads((Path(__file__).resolve().parents[2] / "modules" / "quant" / "public" / "contract.json")
                      .read_text(encoding="utf-8"))


def test_quant_runs_without_developer_and_keeps_answering_while_market_is_down(tmp_path):
    async def scenario():
        async with real_modules(tmp_path, "quant") as platform:
            workspace = await platform.workspace()

            tools = {tool["name"] for tool in await platform.ai_tools("quant")}
            assert {"quant." + name for name in CONTRACT["actions"]} <= tools
            assert "quant.navigate_object" not in tools                 # opening an object is an open target now

            model = await platform.host.ai.invoke({"origin": "ai", "module": None, "workspaceId": workspace, "ai": {"client": "test"}},
                                                  "quant.create_object", {"kind": "model", "name": "Compact model"})
            assert model["objectId"] and (model["kind"],model["version"],model["name"]) == ("model",1,"Compact model")
            presets = (await route(platform, "quant", "GET", "/strategy-presets", workspace))["items"]
            strategies = [item for item in presets if item["kind"] == "strategy"]
            assert [item["id"] for item in strategies] == ["ranked", "rebalance", "signal", "breakout", "crossover"]
            created = await route(platform, "quant", "POST", "/objects", workspace,
                                  {"kind": "strategy", "preset": "ranked", "folder": "Signals"})
            assert created["preset"] is None and created["folder"] == "Signals"   # a preset makes the user's own copy
            assert created["developer"] is None                                  # no Developer: the files are on disk
            code = await route(platform, "quant", "GET", f"/objects/{created['id']}/code", workspace)
            assert code["readOnly"] is False and "class RankedPortfolio" in code["content"]
            mine = await route(platform, "quant", "POST", f"/objects/{created['id']}/copy", workspace, {"name": "Mine"})
            assert mine["name"] == "Mine" and mine["preset"] is None

            market = platform.process("market")
            exited = market.process
            market.job.terminate()
            await exited.wait()
            listed = await route(platform, "quant", "GET", "/objects?kind=strategy", workspace)
            assert {item["name"] for item in listed["items"]} == {"Top N by signal", "Mine"}
            via_action = await platform.call("quant.list_objects", {"kind": "strategy"}, workspace=workspace)
            assert {item["name"] for item in via_action["items"]} == {"Top N by signal", "Mine"}
            await platform.ready("market", seconds=20)                   # the platform restarts Market

    run(scenario())


def test_quant_restores_the_project_through_the_real_developer(tmp_path):
    async def scenario():
        async with real_modules(tmp_path, "quant", "developer") as platform:
            workspace = await platform.workspace()
            obj = await route(platform, "quant", "POST", "/objects", workspace, {"kind": "strategy", "preset": "ranked"})
            root = Path(obj["directory"])
            helper = root / "helper.py"
            helper.write_text("window = 20")
            removed = root / "lib" / "old.py"
            removed.parent.mkdir()
            removed.write_text("price = 1")
            run = await platform.call("quant.start_run", {"objectId": obj["id"]}, workspace=workspace)
            helper.write_text("window = 99")
            removed.unlink()
            (root / "extra.py").write_text("extra = True")
            receipt = await platform.call("quant.apply_run", {"runId": run["runId"]}, workspace=workspace)
            assert receipt["restore"]["extraFiles"] == ["extra.py"]
            assert helper.read_text() == "window = 20" and removed.read_text() == "price = 1"
            again = await platform.call("quant.start_run", {"objectId": obj["id"]}, workspace=workspace)
            helper.write_text("window = 50")
            (root / "extra.py").write_text("extra = False")
            restored = await platform.call("quant.apply_run", {"runId": again["runId"]}, workspace=workspace)
            assert restored["restore"]["extraFiles"] == []
            assert helper.read_text() == "window = 20" and (root / "extra.py").read_text() == "extra = True"
    run(scenario())
