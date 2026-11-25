"""Writing a module in Developer, on a running Host: the template makes a v5 module in the installed modules' folder; TALYX
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
            modules = platform.host.catalog.installed_root
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
            modules = platform.host.catalog.installed_root
            modules.mkdir(parents=True, exist_ok=True)
            _, directory = await templates.create_module(modules, "Signals", None)
            renamed = directory.rename(modules / "other")
            with pytest.raises(ContractError) as refused:
                await platform.call("platform.modules.install", {"path": str(renamed)})
            assert refused.value.code == "invalid_module"

    run(scenario())


def test_projects_on_modules_in_the_old_folder_open_the_copy_the_platform_made(tmp_path):
    """Developer's data format 2: a project (and a file's unsaved edits) on a module in user/modules, where format 1 kept
    installed modules, moves to the copy in the installed modules' folder; other projects stay."""
    from modules.developer.backend.data_formats import move_module_projects
    from modules.developer.backend.projects.service import ProjectService, canonical_uri

    user, data = tmp_path / "user", tmp_path / "data" / "modules" / "developer"
    old, new = user / "modules" / "signals", user / "installed-modules" / "signals"
    for root in (old, new):
        (root / "backend").mkdir(parents=True)
        (root / "talyx.module.json").write_text('{"id": "signals", "name": "Signals"}', encoding="utf-8")
        (root / "backend" / "entry.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    projects = ProjectService(data)
    moved = projects.open(str(old))["projectId"]
    kept = projects.open(str(tmp_path / "work"))["projectId"]
    (data / "unsaved-documents").mkdir()
    uri = canonical_uri(old / "backend" / "entry.py")
    record = {"documentId": "document:1", "uri": uri, "source": {"kind": "file", "uri": uri, "projectId": moved},
              "context": {"contextId": "project:" + moved, "projectId": moved, "rootUri": canonical_uri(old)},
              "content": "x = 2\n", "baseContent": "x = 1\n"}
    (data / "unsaved-documents" / "1.json").write_text(json.dumps(record), encoding="utf-8")

    move_module_projects(data, user / "modules", user / "installed-modules")
    reopened = ProjectService(data)
    assert reopened.get(moved)["root"] == str(new.resolve()) and reopened.get(moved)["status"] == "available"
    assert reopened.get(kept)["root"] == str((tmp_path / "work").resolve())
    record = json.loads((data / "unsaved-documents" / "1.json").read_text(encoding="utf-8"))
    assert record["uri"] == record["source"]["uri"] == canonical_uri(new / "backend" / "entry.py")
    assert record["context"]["rootUri"] == canonical_uri(new) and record["content"] == "x = 2\n"
    assert reopened.locate(record["uri"], moved)["path"] == "backend/entry.py"
    assert reopened.locate(uri, moved)["uri"] == record["uri"]  # tab saved before migration
