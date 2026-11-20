"""Writing a module in Developer, on a running Host: the template makes a v5 module in the user's modules folder; TALYX
uses it where it is; an action added to it is in AI's catalog after a reload, which restarts only that module; uninstalling
it removes the folder."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from talyx_sdk import ContractError

from modules.developer.backend import templates
from tests.platform.harness import run, running

SHOUT = '''

class Shout(Model):
    text: str


@module.action(effects=["read"])
async def shout(input: Shout, call: Call) -> Shout:
    """The same text in capitals. Example: {"text": "hi"}."""
    return Shout(text=input.text.upper())
'''


def test_the_template_is_a_v5_module_with_one_action_and_a_panel(tmp_path):
    files = templates.files("signals", "Signals")
    manifest = json.loads(files["talyx.module.json"])
    for guide in (files["AGENTS.md"], (Path(templates.__file__).parents[1] / "GUIDE.md").read_text(encoding="utf-8")):
        assert guide.index("Use in TALYX") < guide.index("Reload")
    assert manifest["manifestVersion"] == 5 and manifest["id"] == "signals"
    assert manifest["entries"] == {"backend": "backend.entry", "frontend": "frontend/module.js"}
    assert manifest["panels"]["main"]["launcher"] is True
    assert 'module = Module("signals")' in files["backend/entry.py"]
    assert 'ctx.actions.call("signals.greet"' in files["frontend/module.js"]
    assert templates.module_id(" 2 Moving Averages! ") == "moving_averages"
    with pytest.raises(ContractError):
        templates.module_id("123")


def test_a_module_written_from_the_template_is_used_reloaded_and_uninstalled(tmp_path):
    async def scenario():
        async with running(tmp_path, test_modules=("echo",)) as platform:
            modules = tmp_path / "user" / "modules"
            modules.mkdir(parents=True, exist_ok=True)
            identifier, directory = await templates.create_module(modules, "Signals", None)
            assert (identifier, directory) == ("signals", modules / "signals")
            assert (directory / "public" / "contract.json").is_file()
            with pytest.raises(ContractError) as taken:
                await templates.create_module(modules, "Signals", None)
            assert taken.value.code == "conflict"

            state = await platform.call("platform.modules.install", {"path": str(directory)})
            assert state["source"] == "installed" and directory.is_dir()  # used where it is, not copied
            await platform.ready("signals")
            assert await platform.call("signals.greet", {"name": "Ada"}) == {"message": "Hello, Ada!"}

            echo = platform.process("echo").process
            entry = directory / "backend" / "entry.py"
            entry.write_text(entry.read_text(encoding="utf-8") + SHOUT, encoding="utf-8")
            await platform.call("platform.modules.reload", {"id": "signals"})
            await platform.ready("signals")
            assert await platform.call("signals.shout", {"text": "hi"}) == {"text": "HI"}
            tools = {tool["name"] for tool in await platform.ai_tools("signals")}
            assert {"signals.greet", "signals.shout"} <= tools
            assert platform.process("echo").process is echo  # the other modules kept running

            await platform.call("platform.modules.uninstall", {"id": "signals"})
            assert "signals" not in [item["id"] for item in await platform.call("platform.modules.list")]
            assert not directory.exists()

    run(scenario())


def test_a_folder_in_the_modules_folder_installs_only_under_its_own_id(tmp_path):
    async def scenario():
        async with running(tmp_path, test_modules=("echo",)) as platform:
            modules = tmp_path / "user" / "modules"
            modules.mkdir(parents=True, exist_ok=True)
            _, directory = await templates.create_module(modules, "Signals", None)
            renamed = directory.rename(modules / "other")
            with pytest.raises(ContractError) as refused:
                await platform.call("platform.modules.install", {"path": str(renamed)})
            assert refused.value.code == "invalid_module"

    run(scenario())
