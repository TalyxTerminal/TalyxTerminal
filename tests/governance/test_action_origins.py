"""Business action behavior is independent of whether the caller is AI."""

import ast
from pathlib import Path


def test_module_backends_do_not_select_ai_behavior():
    root = Path(__file__).resolve().parents[2]
    found = []
    for path in (root / "modules").glob("*/backend/**/*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "ai"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "caller"
                and path.relative_to(root / "modules").parts[0] != "agent"
            ):
                found.append(f"{path.relative_to(root)}:{node.lineno}")
            if not isinstance(node, ast.Compare):
                continue
            expressions = (node.left, *node.comparators)
            if not any(
                isinstance(n, ast.Constant) and n.value == "ai"
                for expr in expressions
                for n in ast.walk(expr)
            ):
                continue
            if any(
                isinstance(n, ast.Attribute)
                and n.attr == "origin"
                or isinstance(n, ast.Name)
                and n.id in {"origin", "caller_origin"}
                or isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get"
                and n.args
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "origin"
                or isinstance(n, ast.Subscript)
                and isinstance(n.slice, ast.Constant)
                and n.slice.value == "origin"
                for expr in expressions
                for n in ast.walk(expr)
            ):
                found.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not found, found
