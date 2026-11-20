"""The descriptions exposed to AI must match the module's declared wire types."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from talyx_sdk.declare import EffectBranches

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = [ROOT / "host/public/contract.json", *sorted(ROOT.glob("modules/*/public/contract.json"))]


def variants(node, definitions):
    if "$ref" in node:
        yield from variants(definitions[node["$ref"].removeprefix("#/$defs/")], definitions)
    elif "oneOf" in node or "anyOf" in node:
        for choice in node.get("oneOf", node.get("anyOf")):
            yield from variants(choice, definitions)
    else:
        yield node


def fields(node, path, definitions):
    for branch in variants(node, definitions):
        if not path:
            yield branch
        elif path[0] in branch.get("properties", {}):
            yield from fields(branch["properties"][path[0]], path[1:], definitions)


def check_branches(action, definitions):
    rule = EffectBranches.model_validate(action["effectBranches"])
    *parent_path, key = rule.path.split(".")
    parents = list(fields(action["input"], parent_path, definitions))
    values = set()
    for parent in parents:
        discriminators = list(fields(parent, [key], definitions))
        assert discriminators, f"missing discriminator {rule.path}"
        for discriminator in discriminators:
            keys = [discriminator["const"]] if "const" in discriminator else discriminator["enum"]
            for value in keys:
                values.add(value)
                target = rule.branches[value].target
                if target:
                    path = target.split(".")
                    scopes = (parent, path[len(parent_path):]) if path[:len(parent_path)] == parent_path else (action["input"], path)
                    assert list(fields(*scopes, definitions)), f"{value} has no target {target}"
    assert values == rule.branches.keys()
    assert set(action["effects"]) == {effect for branch in rule.branches.values() for effect in branch.effects}


@pytest.mark.parametrize("path", CONTRACTS, ids=[path.parents[1].name for path in CONTRACTS])
def test_ai_actions_have_output_examples_and_valid_branches(path):
    contract = json.loads(path.read_text(encoding="utf-8"))
    for name, action in contract["actions"].items():
        if "callers" in action and "ai" not in action["callers"]:
            continue
        assert action["output"], f"{name} needs an output schema"
        assert action.get("examples"), f"{name} needs an executable input example"
        validator = Draft202012Validator({**action["input"], "$defs": contract["$defs"]})
        for example in action["examples"]:
            assert not (errors := list(validator.iter_errors(example))), (name, errors[0].message)
        if "effectBranches" in action:
            check_branches(action, contract["$defs"])
        if "target" in action:
            assert list(fields(action["input"], action["target"].split("."), contract["$defs"])), \
                f"{name} has no target {action['target']}"
