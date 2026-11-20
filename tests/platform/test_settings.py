"""Standard 8: settings in two layers (rules tested directly, no platform)."""

from __future__ import annotations

import asyncio

import pytest
from talyx_sdk.errors import ContractError

from host.settings import Settings

DECLARED = {
    "color": {"title": "Color", "description": "", "globalOnly": False, "schema": {"type": "string"}, "default": "A"},
    "apiKey": {"title": "Key", "description": "", "globalOnly": True, "schema": {"type": "string"}, "default": ""},
}


def settings(tmp_path) -> Settings:
    return Settings(tmp_path / "settings.json", lambda module: (DECLARED, {}), lambda ws: ws in {"W1", "W2"})


def run(coroutine):
    return asyncio.run(coroutine)


def value(store, workspace):
    return store.read("echo", workspace, ["color"])["values"]["color"]["value"]


def test_a_workspace_follows_global_until_it_sets_its_own(tmp_path):
    store = settings(tmp_path)
    run(store.write("echo", "global", {"color": "A"}))
    run(store.write("echo", "W2", {"color": "B"}))
    assert (value(store, "W1"), value(store, "W2")) == ("A", "B")
    run(store.write("echo", "global", {"color": "C"}))
    assert (value(store, "W1"), value(store, "W2")) == ("C", "B")
    run(store.reset("echo", "W2", ["color"]))
    assert value(store, "W2") == "C"
    assert settings(tmp_path).read("echo", "W1", ["color"])["values"]["color"]["source"] == "global"  # persisted


def test_a_global_only_setting_cannot_be_set_for_a_workspace(tmp_path):
    store = settings(tmp_path)
    with pytest.raises(ContractError) as error:
        run(store.write("echo", "W1", {"apiKey": "x"}))
    assert error.value.code == "permission_denied"
    run(store.write("echo", "global", {"apiKey": "x"}))
    assert store.read("echo", "W1", ["apiKey"])["values"]["apiKey"] == {"value": "x", "source": "global",
                                                                         "globalOnly": True}


def test_a_read_names_its_workspace_or_global(tmp_path):
    store = settings(tmp_path)
    for workspace in (None, "", "W9"):
        with pytest.raises((ContractError, TypeError)):
            store.read("echo", workspace, ["color"])


def test_values_are_checked_against_the_declaration(tmp_path):
    with pytest.raises(ContractError) as error:
        run(settings(tmp_path).write("echo", "global", {"color": 3}))
    assert error.value.code == "invalid_input"


def test_the_platform_refuses_a_settings_read_without_workspace(tmp_path):
    from pydantic import ValidationError

    from host.platform.declarations import SettingsRead
    with pytest.raises(ValidationError):
        SettingsRead.model_validate({"module": "echo", "keys": ["color"]})


def test_general_settings_are_the_platforms_declared_two_layer_settings(tmp_path):
    """Market colors follow global unless a workspace sets its own; sign-in startup and keep-awake are bound to this
    machine, so only global."""
    from talyx_sdk.contract import build

    from host.platform.declarations import module

    contract = build(module)
    store = Settings(tmp_path / "settings.json", lambda owner: (contract["settings"], contract["$defs"]),
                     lambda ws: ws in {"W1", "W2"})
    run(store.write("platform", "global", {"upColor": "red-up"}))
    run(store.write("platform", "W2", {"upColor": "green-up"}))
    colors = {ws: store.read("platform", ws, ["upColor"])["values"]["upColor"]["value"] for ws in ("W1", "W2")}
    assert colors == {"W1": "red-up", "W2": "green-up"}
    for key in ("launchOnLogin", "keepAwakeWhileWatching"):
        with pytest.raises(ContractError) as error:
            run(store.write("platform", "W1", {key: True}))
        assert error.value.code == "permission_denied"
    with pytest.raises(ContractError):
        run(store.write("platform", "global", {"upColor": "blue-up"}))
