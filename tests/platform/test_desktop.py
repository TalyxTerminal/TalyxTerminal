"""The desktop app on the new platform: it runs the Host with ``--desktop`` and talks to it as a window does.

Quitting lists what would stop (every open workspace with its running work, from public platform actions) and then
closes the Host's standard input, which stops the Host the way Ctrl+C does."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.platform.harness import REPO, free_port, prepare

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the desktop app is Windows only")

HOST = """
import sys
from pathlib import Path
from host.app import Host, HostConfig
from host.secrets import MemorySecrets
from host.server import run
root, data, port = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
from host.files import write_json
write_json(data / "host" / "instance.json", {"desktopToken": "test-desktop"})
run(Host(HostConfig(data_dir=data, user_dir=data, port=port, ui_url="http://127.0.0.1:1/", official_root=root,
                    secrets_store=MemorySecrets(), desktop_token="test-desktop", desktop=True)), port, stop_on_stdin_close=True)
"""


def test_login_command_uses_the_declared_release_launcher_without_system_node(tmp_path, monkeypatch):
    from host.desktop.settings import LoginStartup
    from installer.launch import launch_arguments

    program = tmp_path / "Program Files" / "TALYX"
    scripts = program / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    pythonw = scripts / "pythonw.exe"
    pythonw.touch()
    launcher = program / "launch.py"
    launcher.touch()
    data = tmp_path / "User Data"
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("TALYX_RELEASE_LAUNCHER", str(launcher))
    monkeypatch.setenv("TALYX_DATA_DIR", str(tmp_path / "other"))

    args = ["--start-in-tray", "--data-root", str(data)]
    assert LoginStartup(data).command() == subprocess.list2cmdline([pythonw, "-B", "-X", "utf8", launcher, *args])
    assert launch_arguments(args) == (data, ["--start-in-tray"])
    assert launch_arguments(["--uninstall"]) == (tmp_path / "other", ["--uninstall"])


def test_source_login_command_keeps_the_development_entry_and_node(tmp_path, monkeypatch):
    from host.desktop.settings import LoginStartup

    monkeypatch.delenv("TALYX_RELEASE_LAUNCHER", raising=False)
    node = tmp_path / "node.exe"
    node.touch()
    monkeypatch.setenv("PATH", str(tmp_path))
    assert LoginStartup(tmp_path).command() == subprocess.list2cmdline([
        Path(sys.executable).with_name("pythonw.exe"), "-B", REPO / "host/desktop/login.py",
        "--start-in-tray", "--data-root", tmp_path, "--node", node,
    ])


def test_host_does_not_announce_the_desktop_url_if_listening_fails(tmp_path):
    root = prepare(tmp_path / "modules", ())
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", HOST, str(root), str(tmp_path / "data"),
                                 str(port)], cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO / "sdk")},
                                capture_output=True, text=True, encoding="utf8", timeout=60)
    assert result.returncode != 0 and "bind" in result.stderr.lower(), result.stderr
    assert "TALYX UI: " not in result.stdout


def test_quitting_lists_what_stops_then_stops_the_host(tmp_path):
    from host.desktop.app import Desktop
    from host.desktop.bridge import Bridge

    root = prepare(tmp_path / "modules", ("echo",))
    port = free_port()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(REPO), str(REPO / "sdk"), str(root)])}
    host = subprocess.Popen([sys.executable, "-B", "-X", "utf8", "-c", HOST, str(root), str(tmp_path / "data"),
                             str(port)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=env, cwd=REPO, text=True, encoding="utf8")
    try:
        for line in host.stdout:
            if line.startswith("TALYX UI: "):
                break
        else:
            raise AssertionError(f"The Host did not start: exit code {host.wait()}")
        bridge = Bridge(f"ws://127.0.0.1:{port}/ws/bridge", tmp_path / "data")
        try:
            deadline = time.monotonic() + 30
            while bridge.call("platform.modules.list")[0]["state"] != "ready":
                assert time.monotonic() < deadline, "echo did not start"
                time.sleep(0.1)
            opened = bridge.call("platform.workspace.open", {"name": "One"})
            desktop = Desktop("http://127.0.0.1:1/", port, attach=True, data_root=tmp_path / "data")
            desktop.bridge = bridge
            preview = desktop.exit_preview()
            assert [item["workspace"]["workspaceId"] for item in preview["workspaces"]] == [opened["workspaceId"]]
            assert preview == {"workspaces": [{"workspace": opened, "activities": []}]}
        finally:
            bridge.close()
        host.stdin.close()
        assert host.wait(timeout=60) == 0
    finally:
        if host.poll() is None:
            host.kill()


def test_cancelling_an_inflight_power_request_still_allows_shutdown():
    import asyncio
    import threading

    from host.desktop.settings import PowerRequest

    async def scenario():
        entered, release = threading.Event(), threading.Event()

        def execute(flags):
            entered.set()
            return release.wait(3)

        power = PowerRequest(execute)
        setting = asyncio.create_task(power.set(True))
        assert await asyncio.to_thread(entered.wait, 3)
        setting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await setting
        release.set()
        await asyncio.wait_for(power.close(), 3)
        assert not power.thread.is_alive()

    asyncio.run(scenario())
