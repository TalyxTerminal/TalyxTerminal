"""Standard 13: a third-party module installs from a zip or a folder, is enabled, disabled and uninstalled, gets its
own environment for extra packages (or says what is missing), and uses the same platform as official modules."""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from talyx_sdk import ContractError

from tests.platform.harness import FIXTURES, prepare, run, running, write_contract

PACKAGE = "talyx_extra_fixture"


def wheel(folder: Path) -> Path:
    """A minimal wheel built by hand, so installing it needs no network."""
    path = folder / f"{PACKAGE}-1.0-py3-none-any.whl"
    info = f"{PACKAGE}-1.0.dist-info"
    files = {f"{PACKAGE}/__init__.py": "VALUE = 41\n",
             f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {PACKAGE}\nVersion: 1.0\n",
             f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    files[f"{info}/RECORD"] = "".join(f"{name},,\n" for name in files) + f"{info}/RECORD,,\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return path


def third_party(folder: Path, requirement: str | None) -> Path:
    import sys
    module = folder / "thirdparty"
    sys.path.insert(0, str(folder))
    shutil.copytree(FIXTURES / "late", module)
    manifest = json.loads((module / "talyx.module.json").read_text())
    manifest.update(id="thirdparty", name="Third party")
    if requirement:
        manifest["python"] = {"requirements": [requirement]}
    (module / "talyx.module.json").write_text(json.dumps(manifest))
    entry = module / "backend" / "entry.py"
    text = entry.read_text().replace('Module("late")', 'Module("thirdparty")')
    text += '''

@module.action(effects=["read"])
async def extra(input: Empty, call: Call) -> dict:
    """Uses an extra package and the platform's storage, like any module."""
    import os
    directories = await call.process.ctx.platform.storage()
    value = None
    if os.environ.get("THIRDPARTY_EXTRA"):
        import talyx_extra_fixture
        value = talyx_extra_fixture.VALUE
    return {"value": value, "dataDir": directories["dataDir"]}
'''
    entry.write_text(text)
    write_contract(module)
    return module


def user():
    return {"origin": "user", "module": None, "workspaceId": None, "session": None}


@pytest.mark.parametrize("packed", [False, True], ids=["folder", "zip"])
def test_install_enable_disable_uninstall(tmp_path, packed):
    async def scenario():
        source = third_party(tmp_path / "source", None)
        if packed:
            archive = tmp_path / "thirdparty.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for path in source.rglob("*"):
                    if "__pycache__" not in path.parts:
                        output.write(path, Path("thirdparty") / path.relative_to(source))
            source = archive
        async with running(tmp_path, test_modules=("echo",)) as platform:
            caller = {"origin": "ai", "module": None, "workspaceId": await platform.workspace(),
                      "ai": {"client": "test"}}

            async def invoke(action, value):
                return await platform.host.ai.invoke(caller, action, value)

            with pytest.raises(ContractError, match="cannot be uninstalled"):
                await invoke("platform.modules.uninstall", {"id": "echo"})
            assert platform.process("echo").state == "ready"
            state = await invoke("platform.modules.install", {"path": str(source)})
            assert state["source"] == "installed"
            await platform.ready("thirdparty")
            answer = await platform.call("thirdparty.extra")
            assert Path(answer["dataDir"]).parts[-2:] == ("modules", "thirdparty")
            assert (await invoke("platform.modules.disable", {"id": "thirdparty"}))["enabled"] is False
            with pytest.raises(ContractError):
                await platform.call("thirdparty.ping")
            refreshed = await invoke("platform.modules.reload", {"id": "thirdparty"})
            assert not refreshed["enabled"] and refreshed["state"] == "stopped"
            await invoke("platform.modules.enable", {"id": "thirdparty"})
            await platform.ready("thirdparty")
            await invoke("platform.modules.reload", {"id": "thirdparty"})
            await platform.ready("thirdparty")
            await invoke("platform.modules.uninstall", {"id": "thirdparty"})
            assert "thirdparty" not in [m["id"] for m in await platform.call("platform.modules.list")]
            assert not (tmp_path / "user" / "modules" / "thirdparty").exists()

    run(scenario())


def test_a_zip_that_writes_outside_its_folder_is_refused_before_anything_is_written(tmp_path):
    from host.catalog import Catalog

    archive = tmp_path / "escape.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("thirdparty/talyx.module.json", "{}")
        output.writestr("../escaped.txt", "outside")
    installed = tmp_path / "user" / "modules"
    catalog = Catalog(tmp_path / "official", installed, tmp_path / "state.json", tmp_path / "environments")
    with pytest.raises(ContractError) as refused:
        catalog.install(archive)
    assert refused.value.code == "invalid_module" and "../escaped.txt" in refused.value.message
    assert not (installed / "escaped.txt").exists() and not (tmp_path / "user" / "escaped.txt").exists()
    assert list(installed.iterdir()) == []


def test_extra_packages_get_their_own_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRDPARTY_EXTRA", "1")

    async def scenario():
        source = third_party(tmp_path / "source", str(wheel(tmp_path)))
        async with running(tmp_path, test_modules=("echo",)) as platform:
            await platform.call("platform.modules.install", {"path": str(source)})
            await platform.ready("thirdparty", seconds=120)
            assert (await platform.call("thirdparty.extra"))["value"] == 41
            assert "environments" in platform.process("thirdparty").python

    run(scenario())


def test_an_environment_that_cannot_be_built_says_what_is_missing(tmp_path):
    async def scenario():
        source = third_party(tmp_path / "source", "talyx-no-such-package-anywhere==9.9")
        async with running(tmp_path, test_modules=("echo",)) as platform:
            state = await platform.call("platform.modules.install", {"path": str(source)})
            assert state["state"] == "failed"
            assert "talyx-no-such-package-anywhere" in state["problem"]

    run(scenario())


__all__ = ["prepare"]


def test_install_and_reload_validate_authoring_docs_before_switching(tmp_path):
    async def scenario():
        source = third_party(tmp_path / "source", None)
        manifest = source / "talyx.module.json"
        value = json.loads(manifest.read_bytes())
        value["ai"] = {"guide": "guide.md", "docs": [{"topic": "usage", "title": "Usage", "file": "usage.md"}]}
        manifest.write_text(json.dumps(value))
        (source / "guide.md").write_text("---\nname: Example\ndescription: Example module\n---\nGuide")
        async with running(tmp_path, test_modules=()) as platform:
            with pytest.raises(ContractError, match="usage.md"):
                await platform.call("platform.modules.install", {"path": str(source)})
            assert not (platform.host.catalog.installed_root / "thirdparty").exists()
            (source / "usage.md").write_text("Use this module.")
            await platform.call("platform.modules.install", {"path": str(source)})
            await platform.ready("thirdparty")
            original = platform.process("thirdparty")
            (platform.host.catalog.get("thirdparty").directory / "usage.md").unlink()
            with pytest.raises(ContractError, match="usage.md"):
                await platform.call("platform.modules.reload", {"id": "thirdparty"})
            assert platform.process("thirdparty") is original
            await platform.call("thirdparty.ping")
    run(scenario())
