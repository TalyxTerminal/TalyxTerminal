"""Project hand-off to VS Code: open the folder, and for a TALYX module an attach configuration in launch.json."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

PREFIX = "TALYX: "


def jsonc(text: str):
    """Read VS Code's JSON with comments and trailing commas."""
    output, index, length = [], 0, len(text)
    while index < length:
        char = text[index]
        if char == '"':
            end = index + 1
            while text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            output.append(text[index:end + 1])
            index = end + 1
        elif text.startswith("//", index):
            index = text.find("\n", index) if "\n" in text[index:] else length
        elif text.startswith("/*", index):
            index = text.index("*/", index) + 2
        else:
            output.append(char)
            index += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(output)))


def write_launch(root: Path, configurations: list[dict]) -> Path:
    """Replace TALYX's own configurations by name; the author's other configurations stay."""
    path = Path(root) / ".vscode" / "launch.json"
    try:
        current = jsonc(path.read_text(encoding="utf-8")) if path.is_file() else {"version": "0.2.0", "configurations": []}
    except (ValueError, IndexError) as exc:
        raise ContractError("invalid_input", t("{path} is not valid JSON. Fix it in VS Code, then retry.", path=path)) from exc
    except OSError as exc:
        raise ContractError("unavailable", t("{path} could not be read: {strerror}. Retry once it is free.", path=path, strerror=exc.strerror)) from exc
    existing = current.get("configurations", []) if isinstance(current, dict) else None
    if not isinstance(existing, list) or not all(isinstance(item, dict) for item in existing):
        raise ContractError("invalid_input", t("{path} needs an object with a list of configurations. Fix it in VS Code, then retry.", path=path))
    names = {item["name"] for item in configurations}
    merged = [item for item in existing if item.get("name") not in names] + configurations
    if path.is_file() and merged == existing:
        return path
    current["configurations"] = merged
    temporary = path.with_name(uuid4().hex + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise ContractError("unavailable", t("{path} could not be written: {strerror}. Close what holds it, then retry.", path=path, strerror=exc.strerror)) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return path


def module_attach(root: Path, name: str, pid: int) -> dict:
    """Attach VS Code to the running interpreter reported by the module."""
    return {"name": PREFIX + name, "type": "debugpy", "request": "attach", "processId": pid,
            "justMyCode": True, "pathMappings": [{"localRoot": "${workspaceFolder}", "remoteRoot": str(root)}]}


def open_folder(root: Path):
    command = shutil.which("code")
    if command is None:
        raise ContractError("unavailable", t("VS Code's code command was not found. Install VS Code and enable 'Add to PATH', then retry."))
    subprocess.Popen([command, str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
