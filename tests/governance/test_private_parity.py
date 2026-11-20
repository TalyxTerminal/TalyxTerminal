"""Every private panel operation has an AI action, a user-only reason or a tracked gap."""

import ast
import json
from pathlib import Path

import pytest

from modules.chart.backend.document import COMMANDS
from modules.chart.backend.wire import ROUTES

ROOT = Path(__file__).resolve().parents[2]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "websocket", "add_api_websocket_route",
           "api_route", "add_api_route"}


def tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def user_actions_without_ai(owner, folder):
    actions = json.loads((folder / "public/contract.json").read_text(encoding="utf-8"))["actions"]
    return {owner + "." + name for name, spec in actions.items()
            if "callers" in spec and "user" in spec["callers"] and "ai" not in spec["callers"]}


def modules_with_panel_operations():
    yield "platform"
    for path in sorted(ROOT.glob("modules/*/talyx.module.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        entry = path.parent / (manifest["entries"]["backend"].replace(".", "/") + ".py")
        if user_actions_without_ai(manifest["id"], path.parent) or any(
            isinstance(node, ast.Attribute) and node.attr == "private_routes" for node in ast.walk(tree(entry))
        ):
            yield manifest["id"]


def operations_in(path, handler, variable):
    """Literal operations accepted by the dispatcher, including branches shared by several operations."""
    function, = (n for n in ast.walk(tree(path)) if isinstance(n, ast.AsyncFunctionDef) and n.name == handler)
    for node in ast.walk(function):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == variable:
            if isinstance(node.ops[0], (ast.Eq, ast.In)):
                value = ast.literal_eval(node.comparators[0])
                yield from ([value] if isinstance(value, str) else value)


def private_operations(module):
    if module == "platform":
        return user_actions_without_ai("platform", ROOT / "host")
    backend = ROOT / "modules" / module / "backend"
    ops = user_actions_without_ai(module, backend.parent)
    for path in backend.rglob("*.py"):
        nodes = list(ast.walk(tree(path)))
        routers = {n.targets[0].id: next((ast.literal_eval(k.value) for k in n.value.keywords if k.arg == "prefix"), "")
                   for n in nodes if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                   and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                   and n.value.func.id in {"APIRouter", "FastAPI"}}
        routers.setdefault("router", "")  # Quant passes its mounted APIRouter to the route builders.
        for node in nodes:
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method not in METHODS or not isinstance(node.func.value, ast.Name) or node.func.value.id not in routers:
                continue
            argument = node.args[0] if node.args else next(k.value for k in node.keywords if k.arg == "path")
            if module == "chart" and path.name == "routes.py" and isinstance(argument, ast.Name) and argument.id == "path":
                routes = ROUTES.keys()
            else:
                routes = [ast.literal_eval(argument)]
            if method in {"api_route", "add_api_route"}:
                verbs = next((ast.literal_eval(k.value) for k in node.keywords if k.arg == "methods"), ["GET"])
            else:
                verbs = ["WS" if "websocket" in method else method.upper()]
            ops.update(f"{verb} {routers[node.func.value.id]}{route}" for verb in verbs for route in routes)
    if module == "chart":
        commands = COMMANDS.json_schema()["items"]["discriminator"]["mapping"]
        ops.update(f"POST /charts/commit:{name}" for name in commands)
    if module == "market":
        ops.remove("POST /panel")
        panel, = (n for n in tree(backend / "panel.py").body if isinstance(n, ast.ClassDef) and n.name == "Panel")
        ops.update("panel:" + n.name[1:] for n in panel.body if isinstance(n, ast.AsyncFunctionDef)
                   and n.name.startswith("_"))
    if module == "agent":
        ops.remove("POST /{name}")
        entry = backend / "entry.py"
        handlers, = (n.value for n in ast.walk(tree(entry)) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "handlers" for t in n.targets))
        for key, target in zip(handlers.keys, handlers.values, strict=True):
            route = ast.literal_eval(key)
            path, function = (backend / "bots/api.py", "dispatch") if route == "bots" else (entry, target.id)
            ops.update(f"{route}:{op}" for op in operations_in(path, function, "operation"))
    if module == "quant":
        ops.update("WS /notebooks/{object_id}/kernel/socket:" + op for op in
                   operations_in(backend / "notebook_routes.py", "handle", "kind"))
    return ops


@pytest.mark.parametrize("module", tuple(modules_with_panel_operations()))
def test_private_parity(module):
    folder = ROOT / "host" if module == "platform" else ROOT / "modules" / module
    table = json.loads((folder / "parity.json").read_text(encoding="utf-8"))
    actions = set()
    for path in [ROOT / "host/public/contract.json", *ROOT.glob("modules/*/public/contract.json")]:
        owner = "platform" if path.parents[1].name == "host" else path.parents[1].name
        contract = json.loads(path.read_text(encoding="utf-8"))
        actions.update(f"{owner}.{name}" for name, spec in contract["actions"].items()
                       if "callers" not in spec or "ai" in spec["callers"])
    # A UI call to an AI-visible public action is classified by the contract itself.
    # It can also have an explicit row, for example when its accepted inputs differ from the panel's.
    public_rows = {key for key in table if key.startswith(module + ".") and key in actions}
    assert private_operations(module) == set(table) - public_rows
    for operation, classification in table.items():
        assert len(classification) == 1, operation
        kind, value = next(iter(classification.items()))
        if kind == "actions":
            assert isinstance(value, list) and value and set(value) <= actions, (operation, value)
        else:
            assert kind == "userOnly" and isinstance(value, str) and value.strip(), operation
