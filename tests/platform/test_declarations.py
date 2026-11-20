"""Standard 9: contracts are generated from code; a hand edit fails the check."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.platform.harness import prepare

ROOT = Path(__file__).resolve().parents[2]


def check(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/contracts/generate.py"), "--check",
                           "--module-dir", str(directory)], capture_output=True, text=True, cwd=ROOT)


def test_a_hand_edited_contract_fails_the_check(tmp_path):
    root = prepare(tmp_path / "modules", ("late",))
    subprocess.run([sys.executable, "-B", str(ROOT / "scripts/contracts/generate.py"), "--module-dir",
                    str(root / "late")], check=True, capture_output=True, cwd=ROOT)
    assert check(root / "late").returncode == 0
    path = root / "late" / "public" / "contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["actions"]["ping"]["description"] = "edited by hand"
    path.write_text(json.dumps(contract, indent=1) + "\n", encoding="utf-8")
    done = check(root / "late")
    assert done.returncode == 1
    assert "contract.json" in done.stdout


def test_the_repository_contracts_are_current():
    done = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/contracts/generate.py"), "--check"],
                          capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stdout + done.stderr


def test_generated_python_clients_compile():
    """Standard 9: every generated client is valid Python (dotted action and stream names become method names)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for path in [root / "host" / "public" / "python" / "client.py", *root.glob("modules/*/public/python/client.py")]:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_calls_to_extension_implementations_are_checked_against_the_points_declaration(tmp_path):
    """Checkup item 9: the owner of an extension point calls its implementations (its own and other modules') with
    input and output checked against the point's declared types: a wrong answer is invalid_output, wrong input never
    leaves."""
    from tests.platform.harness import run, running

    async def scenario():
        async with running(tmp_path) as platform:
            results = await platform.call("echo.greet_all")
            assert results == {"echo:own": {"text": "Hi Ann"}, "consumer:polite": {"text": "Hello Ann"},
                               "consumer:rude": {"error": "invalid_output"}, "not text": {"error": "invalid_output"}}

    run(scenario())
