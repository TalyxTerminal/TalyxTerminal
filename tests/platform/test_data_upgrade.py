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
