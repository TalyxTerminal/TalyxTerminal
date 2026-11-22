"""Installing a downloaded update once the desktop has quit, then opening TALYX again.

The installer replaces the program's own Python and files (installer/setup.iss) and will not run while TALYX holds its
mutex, so neither it nor what waits for it can be part of TALYX: a hidden Windows PowerShell, started as the desktop
ends, waits for this process to exit, runs the installer silently (Inno Setup still shows its progress window), writes
the installer's exit code for the next start (``install.json``, read by host/updates.py) and opens TALYX the way it
was opened: the installed launcher, or in a checkout the same command line.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

WAIT_MS = 180_000   # how long the helper waits for the desktop to end before installing anyway (the installer then refuses)


def relaunch() -> tuple[list[str], str]:
    """The command that opens TALYX again, and its working folder."""
    launcher = os.environ.get("TALYX_RELEASE_LAUNCHER")
    if launcher:
        return [str(Path(sys.executable).with_name("pythonw.exe")), "-B", "-X", "utf8", launcher], str(Path(launcher).parent)
    return [sys.executable, *sys.orig_argv[1:]], os.getcwd()


def quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def script(pid: int, installer: Path, version: str, result: Path, command: list[str], folder: str) -> str:
    return "\n".join([
        "$ErrorActionPreference = 'Continue'",
        f"try {{ (Get-Process -Id {pid} -ErrorAction Stop).WaitForExit({WAIT_MS}) | Out-Null }} catch {{}}",
        "$code = -1",
        f"try {{ $code = (Start-Process -FilePath {quoted(str(installer))} -ArgumentList '/SILENT /SUPPRESSMSGBOXES /NORESTART' -PassThru -Wait).ExitCode }} catch {{}}",
        f"[IO.File]::WriteAllText({quoted(str(result))}, '{{\"version\": \"{version}\", \"code\": ' + $code + '}}')",
        f"Start-Process -FilePath {quoted(command[0])} -ArgumentList {quoted(subprocess.list2cmdline(command[1:]))} -WorkingDirectory {quoted(folder)}",
    ])


def start(installer: Path, version: str, folder: Path) -> None:
    """Leave the helper running; this process is about to end."""
    command, working = relaunch()
    text = script(os.getpid(), installer, version, folder / "install.json", command, working)
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    args = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
            base64.b64encode(text.encode("utf-16-le")).decode("ascii")]
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Out of any job this process is in, so closing it does not end the helper.
        subprocess.Popen(args, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError:   # a job that allows no breakaway; one that kills on close would end the helper too, as before
        subprocess.Popen(args, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
