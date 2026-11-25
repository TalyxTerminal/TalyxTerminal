"""An upgrade keeps installed users' data: a module's steps run before it starts; newer data stops it, untouched."""

from __future__ import annotations

import asyncio
import json

from tests.platform.harness import prepare, run, running


def test_a_module_reads_version_one_data_brought_up_to_its_format_before_it_starts(tmp_path):
    data = tmp_path / "data" / "modules" / "keeper"
    data.mkdir(parents=True)
    (data / "notes.txt").write_text("buy\nhold", encoding="utf-8")   # written by a version that kept no format

    async def scenario():
        async with running(tmp_path, ("keeper",)) as platform:
            assert (await platform.call("keeper.notes"))["notes"] == ["0: buy", "1: hold"]
    run(scenario())
    assert json.loads((data / "format.json").read_text(encoding="utf-8")) == {"version": 3}


def test_data_from_a_newer_version_stops_the_module_and_says_why(tmp_path):
    data = tmp_path / "data" / "modules" / "keeper"
    data.mkdir(parents=True)
    (data / "format.json").write_text(json.dumps({"version": 5}), encoding="utf-8")
    root = prepare(tmp_path / "modules", ("keeper",))

    async def scenario():
        async with running(tmp_path, official_root=root, wait=()) as platform:
            process = platform.process("keeper")
            for _ in range(600):
                if process.state == "failed":
                    break
                await asyncio.sleep(0.05)
            assert process.state == "failed"
            assert "newer TALYX" in process.problem
    run(scenario())
    assert json.loads((data / "format.json").read_text(encoding="utf-8")) == {"version": 5}


def test_installed_modules_of_format_one_keep_their_data_and_uninstalling_removes_only_their_code(tmp_path):
    """The installed TALYX's user folder is its data root: in format 1 a module installed there had its code in
    modules/<id>, the very folder it keeps its data in. Started on that layout, the platform copies the code to its own
    folder and leaves modules/<id> as the module's data: the module starts and reads what it wrote. Uninstalling then
    removes only the code; installed again, the module finds its data."""
    root = tmp_path / "Data"
    old = prepare(root / "modules", ("keeper",)) / "keeper"
    (old / "notes.txt").write_text("buy\nhold", encoding="utf-8")   # what the module kept, beside its code
    source = prepare(tmp_path / "source", ("keeper",)) / "keeper"

    async def scenario():
        async with running(tmp_path, (), data_dir=root, user_dir=root) as platform:
            installed = platform.host.catalog.get("keeper")
            assert installed.source == "installed" and installed.directory == root / "installed-modules" / "keeper"
            assert (await platform.call("keeper.notes"))["notes"] == ["0: buy", "1: hold"]
            assert platform.host.directories("keeper") == {
                "dataDir": str(old), "userDir": str(root / "module-files" / "keeper"),
                "environmentsDir": str(root / "python-environments"), "modulesDir": str(root / "installed-modules")}
            await platform.call("platform.modules.uninstall", {"id": "keeper"})
            assert not (root / "installed-modules" / "keeper").exists()
            assert sorted(path.name for path in (old / "lines").iterdir()) == ["0.txt", "1.txt"]
            await platform.call("platform.modules.install", {"path": str(source)})
            await platform.ready("keeper")
            assert (await platform.call("keeper.notes"))["notes"] == ["0: buy", "1: hold"]
    run(scenario())
    assert json.loads((root / "platform" / "format.json").read_text(encoding="utf-8")) == {"version": 2}
    assert (old / "talyx.module.json").is_file()   # the old copy of the code stays where it was
