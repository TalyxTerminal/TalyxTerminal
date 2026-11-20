"""TALYX keeps only its own named configurations in a project's VS Code launch.json."""
import json

import pytest
from talyx_sdk import ContractError

from modules.developer.backend import vscode
from modules.developer.backend.vscode import module_attach, write_launch


def test_attach_configuration_merges_into_a_commented_launch_file(tmp_path):
    launch = tmp_path / ".vscode" / "launch.json"
    launch.parent.mkdir()
    launch.write_text('{\n  // Use IntelliSense\n  "version": "0.2.0",\n  "configurations": [\n'
                      '    {"name": "Mine", "type": "node", "url": "http://host//path", /* kept */},\n  ],\n}\n', encoding="utf-8")
    write_launch(tmp_path, [module_attach(tmp_path, "Signals", 101)])
    written = launch.stat().st_mtime_ns
    write_launch(tmp_path, [module_attach(tmp_path, "Signals", 101)])
    assert launch.stat().st_mtime_ns == written
    write_launch(tmp_path, [module_attach(tmp_path / "moved", "Signals", 202)])
    configurations = json.loads(launch.read_text(encoding="utf-8"))["configurations"]
    assert configurations[0] == {"name": "Mine", "type": "node", "url": "http://host//path"}
    assert [item["name"] for item in configurations] == ["Mine", "TALYX: Signals"]
    attach = configurations[1]
    assert attach["type"] == "debugpy" and attach["request"] == "attach" and attach["processId"] == 202
    assert attach["pathMappings"] == [{"localRoot": "${workspaceFolder}", "remoteRoot": str(tmp_path / "moved")}]


@pytest.mark.parametrize("content", ['{"configurations": [', '[]', '{"configurations": {}}', '{"configurations": ["Mine"]}'])
def test_a_launch_file_talyx_cannot_merge_into_is_reported_and_left_alone(tmp_path, content):
    launch = tmp_path / ".vscode" / "launch.json"
    launch.parent.mkdir()
    launch.write_text(content, encoding="utf-8")
    with pytest.raises(ContractError) as raised:
        write_launch(tmp_path, [module_attach(tmp_path, "Signals", 101)])
    assert raised.value.code == "invalid_input"
    assert launch.read_text(encoding="utf-8") == content


def test_a_launch_file_held_by_another_program_is_reported(tmp_path, monkeypatch):
    def denied(source, target):
        raise PermissionError(13, "Access is denied", str(target))
    monkeypatch.setattr(vscode.os, "replace", denied)
    with pytest.raises(ContractError) as raised:
        write_launch(tmp_path, [module_attach(tmp_path, "Signals", 101)])
    assert raised.value.code == "unavailable"
    assert list((tmp_path / ".vscode").iterdir()) == []


def test_debug_route_reads_the_selected_modules_current_pid_on_each_click(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from fastapi.testclient import TestClient

    from modules.developer.backend.app import Developer

    call = AsyncMock(side_effect=[
        [{"id": "other", "pid": 91}, {"id": "signals", "pid": 1234}],
        [{"id": "signals", "pid": 5678}], [{"id": "signals", "pid": None}], [],
    ])
    developer = Developer(SimpleNamespace(call=call), {
        "dataDir": str(tmp_path / "data/modules/developer"), "userDir": str(tmp_path / "user/developer"),
        "modulesDir": str(tmp_path / "modules")})
    root = tmp_path / "signals"
    root.mkdir()
    (root / "talyx.module.json").write_text('{"id":"signals","name":"Signals"}')
    project = developer.projects.open(str(root))
    url = f"/projects/{project['projectId']}/debug"
    opener = Mock()
    monkeypatch.setattr(vscode, "open_folder", opener)
    launch = root / ".vscode/launch.json"
    with TestClient(developer.routes()) as client:
        for pid in (1234, 5678):
            assert client.post(url).status_code == 200
            config, = json.loads(launch.read_bytes())["configurations"]
            assert config["processId"] == pid
            opener.assert_called_with(root)
        previous = launch.read_bytes()
        for _ in range(2):
            response = client.post(url)
            assert response.status_code == 503 and response.json()["error"]["code"] == "unavailable"
        assert launch.read_bytes() == previous
        assert opener.call_count == 2
