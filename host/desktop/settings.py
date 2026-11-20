"""What two General settings do on this machine: start TALYX at sign-in (the Run key) and keep the computer awake
(a same-thread Windows execution-state request). The Host applies them for the desktop app (``host.desktop_effects``)."""

from __future__ import annotations

import asyncio
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import winreg
from concurrent.futures import Future
from pathlib import Path

from talyx_sdk import ContractError

from .instance import identity

ROOT = Path(__file__).resolve().parents[2]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CONTINUOUS = 0x80000000
SYSTEM_REQUIRED = 0x00000001


class LoginStartup:
    def __init__(self, data_root):
        self.data_root = Path(data_root).resolve()
        self.name = "TALYX-" + identity(data_root)[:16]

    def command(self):
        interpreter = Path(sys.executable).with_name("pythonw.exe")   # this environment's, without a console
        tray_args = ["--start-in-tray", "--data-root", str(self.data_root)]
        if launcher := os.environ.get("TALYX_RELEASE_LAUNCHER"):
            return subprocess.list2cmdline([interpreter, "-B", "-X", "utf8", launcher, *tray_args])
        node = shutil.which("node")
        if not interpreter.is_file() or node is None:
            raise ContractError("unavailable", "The desktop Python or Node executable is missing")
        return subprocess.list2cmdline([interpreter, "-B", ROOT / "host/desktop/login.py",
                                        *tray_args, "--node", Path(node).resolve()])

    def read(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                return winreg.QueryValueEx(key, self.name)[0]
        except FileNotFoundError:
            return None

    def configure(self, enabled):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, self.name, 0, winreg.REG_SZ, self.command())
            elif self.read() is not None:
                winreg.DeleteValue(key, self.name)


class PowerRequest:
    def __init__(self, execute=None):
        if execute is None:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.SetThreadExecutionState.argtypes = [ctypes.c_ulong]
            kernel.SetThreadExecutionState.restype = ctypes.c_ulong
            execute = kernel.SetThreadExecutionState
        self.execute = execute
        self.queue = queue.Queue()
        self.requested = False
        self.thread = threading.Thread(target=self._run, name="desktop-power")
        self.thread.start()

    def _run(self):
        while True:
            desired, future = self.queue.get()
            reply = future.set_running_or_notify_cancel()
            flags = CONTINUOUS | SYSTEM_REQUIRED if desired else CONTINUOUS
            if not self.execute(flags):
                if reply:
                    future.set_exception(OSError("Windows rejected the execution-state request"))
            else:
                self.requested = bool(desired)
                if reply:
                    future.set_result(None)
            if desired is None:
                return

    async def set(self, value):
        future = Future()
        self.queue.put((value, future))
        await asyncio.wrap_future(future)

    async def close(self):
        future = Future()
        self.queue.put((None, future))
        await asyncio.wrap_future(future)
        self.thread.join()
