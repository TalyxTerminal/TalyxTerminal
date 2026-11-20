"""The example modules under examples/ install on the platform as they are, their generated files match their code, and
the add-on reaches the core only through what the core declares."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.platform.harness import run, running

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"


def test_the_examples_generated_files_match_their_code():
    done = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "contracts" / "generate.py"), "--check",
                           "--module-dir", str(EXAMPLES / "example_core"), "--module-dir", str(EXAMPLES / "example_addon")],
                          capture_output=True, text=True, cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_examples_install_and_the_core_answers(tmp_path):
    async def scenario():
        async with running(tmp_path, test_modules=("echo",)) as platform:
            for name in ("example_core", "example_addon"):
                state = await platform.call("platform.modules.install", {"path": str(EXAMPLES / name)})
                assert state["source"] == "installed"
                await platform.ready(name)
            assert await platform.call("example_core.text_stats", {"text": "Hello brave world"}) == {"characters": 17, "words": 3}
            definitions = {item["id"]: item for item in await platform.call("platform.modules.definitions")}
            addon = definitions["example_addon"]
            assert addon["manifest"]["requires"] == {"example_core": {}} and addon["contract"]["actions"] == {}
            assert set(addon["manifest"]["provides"]) == {"platform.settings_section", "platform.status_item"}
            sections = await platform.call("platform.catalog.implementations", {"point": "platform.settings_section"})
            assert {"moduleId": "example_addon", "id": "prefs", "descriptor": {"title": "Example add-on", "view": "settings"}} in sections
    run(scenario())
