"""Malicious imports are checked by the same strict scanner used in production."""

import json
import subprocess
from pathlib import Path

import pytest

from scripts.checks.check_boundaries import check, evaluate_edges, regressions

ROOT = Path(__file__).resolve().parents[2]


def tree(root, files, *, requires=None, entries=None):
    scaffold = (
        "host/__init__.py",
        "contracts/__init__.py",
        "modules/chart/__init__.py",
        "modules/table/__init__.py",
    )
    for name, source in {**dict.fromkeys(scaffold, ""), **files}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    for owner in ("chart", "table"):
        (root / "modules" / owner / "talyx.module.json").write_text(
            json.dumps(
                {
                    "id": owner,
                    "manifestVersion": 5,
                    "requires": (requires or {}).get(owner, {}),
                    "entries": (entries or {}).get(owner, {}),
                }
            ),
            encoding="utf-8",
        )
    return check(root, include_ts=False)


@pytest.mark.parametrize(
    "source",
    [
        "import modules.table.render",
        "from modules import table",
        "from .. import table",
        "from ..table import _state",
    ],
)
def test_private_owner_cannot_hide_behind_package_or_relative_import(tmp_path, source):
    report = tree(tmp_path, {"modules/chart/engine.py": source, "modules/table/render.py": ""})
    assert "module-private-import" in report["counts"]


@pytest.mark.parametrize("source", ["import host.execution", "from host.execution import _impl"])
def test_module_must_use_sdk_for_platform_facilities(tmp_path, source):
    report = tree(tmp_path, {"modules/chart/engine.py": source, "host/execution.py": ""})
    assert "module-must-use-sdk" in report["counts"]


@pytest.mark.parametrize(
    "path",
    ["host/service.py", "kernel/service.py", "sdk/talyx_sdk/client.py", "contracts/models.py"],
)
def test_lower_layer_cannot_import_module_implementation(tmp_path, path):
    report = tree(tmp_path, {path: "import modules.chart.engine", "modules/chart/engine.py": ""})
    assert "layer-direction" in report["counts"]


@pytest.mark.parametrize("provider", ["host", "kernel"])
def test_sdk_cannot_import_its_providers(tmp_path, provider):
    files = {"sdk/talyx_sdk/client.py": f"import {provider}.service", f"{provider}/service.py": ""}
    assert "sdk-provider-import" in tree(tmp_path, files)["counts"]


@pytest.mark.parametrize(
    "source,target,rule",
    [
        ("contracts/series.py", "host/session.py", "layer-direction"),
        ("modules/chart/frontend/view.tsx", "web/src/shell/app.tsx", "layer-direction"),
        ("sdk/ui/src/index.ts", "web/src/kernel/runtime.ts", "sdk-provider-import"),
    ],
)
def test_forbidden_edge(source, target, rule):
    violations = evaluate_edges([{"source": source, "target": target, "line": 1}], {}, set())
    assert rule in {v["rule"] for v in violations}


def test_the_window_shell_only_loads_manifest_declared_entries():
    entries = {"modules/chart/frontend/index.tsx"}
    declared = {"source": "web/src/shell/modules.ts", "target": "modules/chart/frontend/index.tsx", "line": 1}
    assert evaluate_edges([declared], {}, entries) == []
    private = {**declared, "target": "modules/chart/frontend/private.tsx"}
    assert {v["rule"] for v in evaluate_edges([private], {}, entries)} == {"assembly-private-import"}
    platform = {**declared, "source": "host/app.py"}   # the Host is the platform: it imports no module
    assert {v["rule"] for v in evaluate_edges([platform], {}, entries)} == {"layer-direction"}


def test_public_import_requires_declared_dependency(tmp_path):
    files = {
        "modules/table/consumer.py": "from modules.chart.public import Data",
        "modules/chart/public/__init__.py": "Data = dict",
    }
    assert "undeclared-module-dependency" in tree(tmp_path, files)["counts"]
    assert tree(tmp_path, files, requires={"table": {"chart": {"version": "^1", "optional": False}}})["total"] == 0


def test_unresolved_internal_import_and_declared_cycles_are_rejected(tmp_path):
    report = tree(tmp_path, {"host/service.py": "import app.retired"})
    assert "unresolved-internal-import" in report["counts"]
    report = tree(
        tmp_path, {"host/service.py": ""},
        requires={"chart": {"table": {"optional": False}}, "table": {"chart": {"optional": False}}}
    )
    assert report["counts"]["module-dependency-cycle"] == 1


def test_optional_cycle_grants_public_access_but_rejects_private_imports(tmp_path):
    requires = {"chart": {"table": {"optional": True}}, "table": {"chart": {"optional": True}}}
    files = {"modules/chart/engine.py": "from modules.table.public import Data",
             "modules/table/public/__init__.py": "Data = dict"}
    assert tree(tmp_path, files, requires=requires)["total"] == 0
    files["modules/chart/engine.py"] = "import modules.table.private"
    files["modules/table/private.py"] = ""
    assert tree(tmp_path, files, requires=requires)["counts"] == {"module-private-import": 1}


def test_intra_module_and_neutral_dependencies_remain_legal(tmp_path):
    assert (
        tree(
            tmp_path,
            {
                "modules/chart/engine.py": "import modules.chart.util\nimport json",
                "modules/chart/util.py": "",
                "contracts/models.py": "from dataclasses import dataclass",
            },
        )["total"]
        == 0
    )


def test_module_may_use_sdk():
    edge = {"source": "modules/chart/run.py", "target": "sdk/talyx_sdk/runtime.py", "line": 1}
    assert evaluate_edges([edge], {}, set()) == []


def test_literal_dynamic_imports_are_checked(tmp_path):
    source = "from importlib import import_module as load\nload('modules.table.render')\n"
    report = tree(tmp_path, {"modules/chart/engine.py": source, "modules/table/render.py": ""})
    assert "module-private-import" in report["counts"]


def test_node_dependencies_are_excluded_without_exempting_owned_runtime_source(tmp_path):
    report = tree(tmp_path, {
        "modules/chart/runtime/planner/node_modules/vendor/generate-tests.py": "print 'Python 2 vendor script'",
        "modules/chart/runtime/planner/run.py": "import modules.table.render",
        "modules/table/render.py": "",
    }, requires={"chart": {"table": {"version": "^1", "optional": True}}})
    assert report["counts"] == {"module-private-import": 1}
    assert report["violations"][0]["source"] == "modules/chart/runtime/planner/run.py"


def test_public_type_aliases_do_not_hide_runtime_imports(tmp_path):
    files = {
        "web/tsconfig.json": json.dumps(
            {"compilerOptions": {"baseUrl": ".", "paths": {"@talyx/sdk": ["../sdk/ui/index.ts"]}}}
        ),
        "sdk/ui/index.ts": "export interface LegacyFrame {}\nexport const runtimeToken = 1;\n",
        "web/src/realtime/sessionClient.ts": (
            'export type { LegacyFrame as CommandFrame } from "@talyx/sdk";\n'
            'export { runtimeToken } from "@talyx/sdk";\n'
        ),
        "web/src/chartDocument/caller.ts": (
            'import type { CommandFrame } from "../realtime/sessionClient";\n'
            'import { runtimeToken } from "../realtime/sessionClient";\n'
        ),
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    result = subprocess.run(
        ["node", str(ROOT / "scripts/checks/ts_imports.mjs"), str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    edges = [
        edge
        for edge in json.loads(result.stdout)["imports"]
        if edge["source"] == "web/src/chartDocument/caller.ts"
    ]
    assert [(edge["line"], edge["target"]) for edge in edges] == [
        (1, "sdk/ui/index.ts"),
        (2, "web/src/realtime/sessionClient.ts"),
    ]


def test_ratchet_counts_rule_and_target_without_treating_a_move_as_regression():
    old = {"rule": "layer-direction", "source": "host/old.py", "target": "modules/chart/x.py"}
    new = {**old, "source": "host/new.py"}
    assert sum(regressions({"violations": [new]}, {"violations": [old]}).values()) == 0
    changed_target = {**new, "target": "modules/developer/x.py"}
    assert sum(regressions({"violations": [changed_target]}, {"violations": [old]}).values()) == 1
    assert sum(regressions({"violations": [old, old]}, {"violations": [old]}).values()) == 1
