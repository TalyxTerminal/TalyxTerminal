"""A new TALYX module from Developer's template: a backend with one action and a panel that calls it, in the folder
where TALYX keeps the modules the user installs, so it is enabled, reloaded and uninstalled like any of them.

The frontend is plain JavaScript (ES modules): TALYX loads it as it is, with React and ``@talyx/sdk`` from the
window, so the module needs no build step."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

PLATFORM = Path(__file__).resolve().parents[3]


def module_id(name: str) -> str:
    """A module id from its name: lowercase letters, digits and underscores, starting with a letter."""
    value = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    value = re.sub(r"^[^a-z]+", "", value)[:64]
    if not value:
        raise ContractError("invalid_input", t("Name the module with at least one letter, or give its id."))
    return value


def files(identifier: str, name: str) -> dict[str, str]:
    manifest = {"manifestVersion": 5, "id": identifier, "name": name, "version": "0.1.0",
                "description": f"{name}, written in Developer.",
                "entries": {"backend": "backend.entry", "frontend": "frontend/module.js"},
                "panels": {"main": {"title": name, "icon": "module", "launcher": True,
                                    "description": f"{name}'s panel."}}}
    backend = f'''"""{name}: what the module offers. Its contract (public/contract.json) and clients are generated from this
file each time the module is reloaded in Developer."""
from talyx_sdk.declare import Model, Module
from talyx_sdk.process import Call

module = Module("{identifier}")


class GreetInput(Model):
    name: str
    """Who to greet."""


class Greeting(Model):
    message: str


@module.action(effects=["read"])
async def greet(input: GreetInput, call: Call) -> Greeting:
    """Greet someone by name. Example: {{"name": "Ada"}}."""
    return Greeting(message=f"Hello, {{input.name}}!")
'''
    frontend = f'''// {name}'s panel. Plain JavaScript: TALYX loads this file as it is (React and @talyx/sdk come from the window).
import React, {{ useState }} from "react";
import {{ createRoot }} from "react-dom/client";
import {{ TxButton, TxInput, TxRoot }} from "@talyx/sdk";

const h = React.createElement;

function Greeting({{ ctx }}) {{
  const [name, setName] = useState("World");
  const [message, setMessage] = useState("");
  const [problem, setProblem] = useState("");
  const greet = async () => {{
    setProblem("");
    try {{ setMessage((await ctx.actions.call("{identifier}.greet", {{ name }})).message); }}
    catch (error) {{ setProblem(error.message); }}
  }};
  return h("section", {{ style: {{ padding: 16, display: "grid", gap: 8, maxWidth: 360 }} }},
    h(TxInput, {{ "aria-label": "Name", value: name, onChange: event => setName(event.target.value) }}),
    h(TxButton, {{ variant: "primary", disabled: !name.trim(), onClick: () => void greet() }}, "Greet"),
    h("p", {{ "aria-live": "polite" }}, problem || message));
}}

export function activate(ctx) {{
  ctx.panels.provide("main", (element, panel) => {{
    const root = createRoot(element);
    root.render(h(TxRoot, {{ overlay: panel.overlayRoot }}, h(Greeting, {{ ctx: panel }})));
    return {{ dispose: () => root.unmount() }};
  }});
}}
'''
    instructions = f"""# {name}

A TALYX module (manifest version 5). Select this project in Developer and click Use in TALYX for the first install.
After saving changes, click Reload. Read the TALYX SDK references in Developer's Help before changing it.

- `talyx.module.json` is written by hand: id, name, entries, the modules it calls (`requires`), its panels and views.
  `python.requirements` lists extra Python packages; TALYX then builds the module its own environment.
- `backend/entry.py` declares what the module offers with `talyx_sdk.declare.Module`: actions (typed input and
  output, a docstring people and AI read, and their effects), streams, events and settings.
- `public/` is generated from the declarations each time the module is reloaded. Do not edit it.
- `frontend/module.js` exports `activate(ctx)` and provides every panel and view the manifest declares. It is loaded
  as it is: plain JavaScript, with `react`, `react-dom/client` and `@talyx/sdk` from the window.
- Other modules are reached only through their public actions, streams and events, and only those listed in `requires`.
"""
    return {"talyx.module.json": json.dumps(manifest, indent=2) + "\n", "backend/__init__.py": "",
            "backend/entry.py": backend, "frontend/module.js": frontend, "AGENTS.md": instructions,
            ".gitignore": "__pycache__/\n"}


async def generate_contract(directory: Path) -> None:
    """Write the module's contract and clients from its declarations (what reloading it does too)."""
    done = await asyncio.create_subprocess_exec(
        sys.executable, "-B", str(PLATFORM / "scripts" / "contracts" / "generate.py"), "--module-dir", str(directory),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(PLATFORM))
    output, _ = await done.communicate()
    if done.returncode != 0:
        raise ContractError("invalid_module", "Its contract could not be generated:\n" + output.decode(errors="replace")[-3000:])


async def create_module(modules_dir: Path, name: str, identifier: str | None) -> tuple[str, Path]:
    """Write a new module into ``modules_dir/<id>``, contract included; only into a folder that does not exist."""
    name = name.strip()
    identifier = identifier or module_id(name)
    directory = Path(modules_dir) / identifier
    if directory.exists():
        raise ContractError("conflict", t("{directory} already exists. Choose another name or id.", directory=directory))
    staging = Path(modules_dir) / f".new-{uuid4().hex}"
    try:
        for relative, content in files(identifier, name).items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        staging.rename(directory)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    try:
        await generate_contract(directory)
    except ContractError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return identifier, directory
