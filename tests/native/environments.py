"""The fixed Quant environments for the native checks, installed from the bundled locks with uv into %LOCALAPPDATA%\\TALYX\\native-smoke (reused while the lock is unchanged)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from modules.quant.backend.native_environment import bundled_wheels

QUANT = Path(__file__).resolve().parents[2] / "modules" / "quant"


def environment(lock_name, python):
    override = os.environ.get("TALYX_DEEP_PYTHON")
    if override and lock_name.startswith("deep-"):
        return Path(override)  # an environment installed from this lock elsewhere (a drive with room)
    lock = bundled_wheels((QUANT / "runtime" / lock_name).read_text(encoding="utf-8"), QUANT / "runtime")
    key = hashlib.sha256((python + lock).encode()).hexdigest()[:16]
    target = Path(os.environ["LOCALAPPDATA"]) / "TALYX" / "native-smoke" / f"{Path(lock_name).stem}-{key}"
    interpreter = target / "Scripts" / "python.exe"
    if not (target / "ready").is_file():
        shutil.rmtree(target, ignore_errors=True)
        uv = shutil.which("uv")
        subprocess.run([uv, "venv", "--python", python, str(target)], check=True)
        requirements = target / "requirements.lock"
        requirements.write_text(lock, encoding="utf-8")
        subprocess.run([uv, "pip", "install", "--python", str(interpreter), "-r", str(requirements)], check=True)
        (target / "ready").write_text("", encoding="utf-8")
    return interpreter
