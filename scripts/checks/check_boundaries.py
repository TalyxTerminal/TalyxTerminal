"""Dependency boundary report and optional baseline comparison. No permitted-import exceptions.

Layer names describe ownership, not exemptions. Existing violations are measured
as a multiset of rule/target pairs, so moving a source does not fabricate a new
violation. --strict rejects all violations.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY_ROOTS = ("host", "contracts", "sdk/talyx_sdk", "modules", "kernel", "vocabulary", "examples")


def ownership(path: str) -> tuple[int, str | None]:
    """Layer 1: shared vocabulary; 2: the platform; 3: a module (and which); 4: the window's assembly (shell)."""
    parts = path.split("/")
    if parts[0] in {"contracts", "vocabulary"}:
        return 1, None
    if parts[0] in {"modules", "examples"} and len(parts) > 1:
        return 3, parts[1]
    if path.startswith("web/src/") and (parts[2] == "shell" or parts[2] == "main.tsx"):
        return 4, None
    return 2, None


def python_imports(root: Path) -> dict:
    files = sorted(p for area in PY_ROOTS for p in (root / area).rglob("*.py")
                   if not {"__pycache__", "tests", "node_modules"}.intersection(p.relative_to(root).parts)
                   and not p.name.startswith("test_"))
    module_paths = {}
    for file in files:
        name = file.relative_to(root).with_suffix("").as_posix().replace("/", ".")
        if name.startswith("sdk."):
            name = name[4:]
        if name.endswith(".__init__"):
            name = name[:-9]
        module_paths[name] = file.relative_to(root).as_posix()

    def resolve(name):
        while name:
            if name in module_paths:
                return module_paths[name]
            name = name.rpartition(".")[0]
        return None

    imports, dynamic = [], []
    for file in files:
        source = file.relative_to(root).as_posix()
        package = source.removeprefix("sdk/").split("/")[:-1]
        tree = ast.parse(file.read_text(encoding="utf-8-sig"), filename=source)
        dynamic_names = {"__import__", "importlib.import_module"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "importlib":
                dynamic_names.update(alias.asname or alias.name for alias in node.names
                                     if alias.name == "import_module")
            if isinstance(node, ast.Import):
                dynamic_names.update((alias.asname or alias.name) + ".import_module"
                                     for alias in node.names if alias.name == "importlib")
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = ".".join(package[:len(package) - node.level + 1] +
                                    ([base] if base else []))
                targets = [base if alias.name == "*" else base + "." + alias.name
                           for alias in node.names]
            elif isinstance(node, ast.Call) and ast.unparse(node.func) in dynamic_names:
                argument = node.args[0] if node.args else None
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    targets = [argument.value]
                else:
                    dynamic.append({"source": source, "line": node.lineno,
                                    "expression": ast.unparse(node)})
            for name in targets:
                target = resolve(name)
                if target:
                    imports.append({"source": source, "target": target, "line": node.lineno})
                elif name.split(".")[0] in {"host", "contracts", "talyx_sdk", "modules",
                                           "vocabulary", "kernel", "app"}:
                    imports.append({"source": source, "target": name.replace(".", "/"),
                                    "line": node.lineno, "missing": True})
    return {"files": len(files), "imports": imports, "dynamic": dynamic}


def manifests(root: Path) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    dependencies, required, entries = {}, {}, set()
    for path in sorted([*(root / "modules").glob("*/talyx.module.json"), *(root / "examples").glob("*/talyx.module.json")]):
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        owner = value["id"]
        declared = value.get("requires", {})
        dependencies[owner] = set(declared)
        required[owner] = {name for name, spec in declared.items() if not spec.get("optional")}
        for side in ("frontend", "backend"):
            entry = value.get("entries", {}).get(side)
            if isinstance(entry, str):
                entry = entry.replace(".", "/") + ".py" if side == "backend" else entry
                entries.add((path.parent / entry).relative_to(root).as_posix())
    return dependencies, required, entries


def evaluate_edges(edges: list[dict], dependencies: dict, entries: set[str], *, required=None) -> list[dict]:
    violations = []
    for edge in edges:
        source, target = edge["source"], edge["target"]
        if target.startswith("web/src/design/") and source.startswith("modules/"):
            continue  # Approved visual transition: old visuals survive until module redesign.
        source_layer, owner = ownership(source)
        target_layer, other = ownership(target)
        rules = []
        if edge.get("missing"):
            rules.append("unresolved-internal-import")
        if source_layer < target_layer:
            rules.append("layer-direction")
        if owner and source_layer == 3 and target_layer == 2 and not target.startswith("sdk/"):
            rules.append("module-must-use-sdk")
        if source.startswith("sdk/") and target.startswith(("host/", "kernel/", "web/src/")):
            rules.append("sdk-provider-import")
        if owner and other and owner != other:
            if not target.startswith(f"modules/{other}/public/"):
                rules.append("module-private-import")
            if other not in dependencies.get(owner, set()):
                rules.append("undeclared-module-dependency")
        if source_layer == 4 and target_layer == 3 and target not in entries and not (
            other and target.startswith(f"modules/{other}/public/")
        ):
            rules.append("assembly-private-import")
        for rule in rules:
            violations.append({"rule": rule, **edge})
    # Only required dependencies constrain activation; all declarations grant import access.
    required = dependencies if required is None else required
    cycles = set()

    def visit(owner, trail):
        if owner in trail:
            loop = trail[trail.index(owner):]
            cycles.add(min(tuple(loop[index:] + loop[:index]) for index in range(len(loop))))
            return
        for dependency in sorted(required.get(owner, set())):
            visit(dependency, trail + [owner])

    for owner in dependencies:
        visit(owner, [])
    for cycle in sorted(cycles):
        violations.append({"rule": "module-dependency-cycle",
                           "source": f"modules/{cycle[0]}/talyx.module.json", "line": 1,
                           "target": " -> ".join((*cycle, cycle[0]))})
    return violations


def check(root: Path = ROOT, *, include_ts: bool = True) -> dict:
    python = python_imports(root)
    typescript = {"files": 0, "imports": [], "dynamic": []}
    if include_ts:
        output = subprocess.run(["node", str(ROOT / "scripts/checks/ts_imports.mjs"),
                                 str(root)], check=True, capture_output=True, encoding="utf-8")
        typescript = json.loads(output.stdout)
    dependencies, required, entries = manifests(root)
    violations = evaluate_edges(
        python["imports"] + typescript["imports"], dependencies, entries, required=required,
    )
    return {"version": 1, "pythonFiles": python["files"], "typescriptFiles": typescript["files"],
            "counts": dict(sorted(Counter(v["rule"] for v in violations).items())),
            "total": len(violations), "violations": violations,
            "computedImports": python["dynamic"] + typescript["dynamic"]}


def fingerprints(report: dict) -> Counter:
    return Counter((v["rule"], v["target"]) for v in report["violations"])


def regressions(report: dict, baseline: dict) -> Counter:
    return fingerprints(report) - fingerprints(baseline)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = check(args.root.resolve())
    failures = report["total"] if args.strict else 0
    if args.baseline:
        added = regressions(report, json.loads(args.baseline.read_text(encoding="utf-8")))
        report["newViolations"] = sum(added.values())
        failures += sum(added.values())
        for (rule, target), count in sorted(added.items()):
            print(f"NEW [{rule}] -> {target} ({count})")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("pythonFiles", "typescriptFiles", "counts",
                                                 "total")}, ensure_ascii=False))
    print(f"Computed imports requiring review: {len(report['computedImports'])}")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
