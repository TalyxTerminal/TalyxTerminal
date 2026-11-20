"""The contract of a declared module (``public/contract.json``), built from its decorators and types."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from .declare import Module

CONTRACT_VERSION = 5


def build(module: Module) -> dict:
    """Every declared item with its JSON Schema; shared types appear once under ``$defs``."""
    inputs: list[tuple[tuple, str, TypeAdapter]] = []

    def add(key: tuple, annotation: Any, mode: str) -> None:
        inputs.append((key, mode, TypeAdapter(annotation)))

    for name, spec in module.actions.items():
        add(("action", name, "input"), spec.input, "validation")
        add(("action", name, "output"), spec.output, "serialization")
    for name, spec in module.streams.items():
        add(("stream", name, "params"), spec.params, "validation")
        add(("stream", name, "item"), spec.item, "serialization")
    for name, spec in module.events.items():
        add(("event", name, "payload"), spec.payload, "serialization")
    for name, spec in module.extension_points.items():
        add(("point", name, "descriptor"), spec.descriptor, "validation")
        for method, (input_type, output_type, _) in spec.actions.items():
            add(("point-action", name, method, "input"), input_type, "validation")
            add(("point-action", name, method, "output"), output_type, "validation")
        for method, (params_type, item_type, _) in spec.streams.items():
            add(("point-stream", name, method, "params"), params_type, "validation")
            add(("point-stream", name, method, "item"), item_type, "validation")
    for key, spec in module.settings.items():
        add(("setting", key), spec.type, "validation")
    schemas, definitions = TypeAdapter.json_schemas(inputs, ref_template="#/$defs/{model}") if inputs else ({}, {})
    schemas = {key: value for (key, _mode), value in schemas.items()}
    contract: dict[str, Any] = {"contractVersion": CONTRACT_VERSION, "module": module.id}
    if module.workspace_deleter is not None:
        contract["deleteWorkspace"] = True
    contract["actions"] = {name: {
        "description": spec.description,
        "effects": list(spec.effects),
        **({"effectBranches": spec.effect_branches.model_dump(exclude_none=True)} if spec.effect_branches else {}),
        **({"target": spec.target} if spec.target else {}),
        **({"callers": list(spec.callers)} if spec.callers is not None else {}),
        "task": spec.task,
        "input": schemas[("action", name, "input")],
        "output": schemas[("action", name, "output")],
        **({"examples": list(spec.examples)} if spec.examples else {}),
        **({"errors": list(spec.errors)} if spec.errors else {}),
    } for name, spec in sorted(module.actions.items())}
    contract["streams"] = {name: {
        "description": spec.description, "cursor": spec.cursor, "trigger": spec.trigger,
        "params": schemas[("stream", name, "params")], "item": schemas[("stream", name, "item")],
    } for name, spec in sorted(module.streams.items())}
    contract["events"] = {name: {"description": spec.description, "payload": schemas[("event", name, "payload")]}
                          for name, spec in sorted(module.events.items())}
    contract["extensionPoints"] = {name: {
        "description": spec.description,
        "descriptor": schemas[("point", name, "descriptor")],
        "actions": {method: {"description": text, "input": schemas[("point-action", name, method, "input")],
                             "output": schemas[("point-action", name, method, "output")]}
                    for method, (_, _, text) in sorted(spec.actions.items())},
        "streams": {method: {"description": text, "params": schemas[("point-stream", name, method, "params")],
                             "item": schemas[("point-stream", name, method, "item")]}
                    for method, (_, _, text) in sorted(spec.streams.items())},
    } for name, spec in sorted(module.extension_points.items())}
    contract["settings"] = {key: {
        "title": spec.title, "description": spec.description, "globalOnly": spec.global_only, "group": spec.group,
        "schema": schemas[("setting", key)], "default": TypeAdapter(spec.type).dump_python(spec.default, mode="json"),
    } for key, spec in module.settings.items()}
    contract["$defs"] = dict(sorted(definitions.get("$defs", {}).items()))
    if module.change_handlers is not None:
        contract["changes"] = True
    return contract


def check_ready(contract: dict, ready: dict) -> list[str]:
    """What a started module serves that its contract does not say, or the other way round."""
    problems = []
    if contract.get("changes", False) != ready.get("changes", False):
        problems.append("changes hooks differ from the contract")
    if bool(contract.get("deleteWorkspace")) != bool(ready.get("deleteWorkspace")):
        problems.append("workspace deletion handler does not match its declaration")
    for kind in ("actions", "streams"):
        declared, served = set(contract.get(kind, {})), set(ready[kind])
        problems += [f"{kind[:-1]} {name} is in the contract but has no handler" for name in sorted(declared - served)]
        problems += [f"{kind[:-1]} {name} has a handler but is not in the contract" for name in sorted(served - declared)]
    return problems
