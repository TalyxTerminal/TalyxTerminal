"""One actual Host per canonical data root; a separate event only raises its desktop."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
from ctypes import wintypes
from pathlib import Path

from ..files import write_json

HELD = set()


def identity(root):
    return hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode("utf-8")).hexdigest()


def api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenEventW.restype = wintypes.HANDLE
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.SetEvent.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


class HostAlreadyRunning(RuntimeError):
    pass


class HostInstance:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.name = "Local\\TALYX.Host." + identity(root)
        self.path = self.root / "host" / "instance.json"
        self.kernel = api()
        self.handle = None

    def acquire(self, address):
        if self.name in HELD:
            raise HostAlreadyRunning("A TALYX Host is already running for this data directory")
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(None, True, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            state = self.kernel.WaitForSingleObject(handle, 0)
            if state not in {0, 128}:
                self.kernel.CloseHandle(handle)
                if state == 258:
                    raise HostAlreadyRunning("A TALYX Host is already running for this data directory")
                raise ctypes.WinError(ctypes.get_last_error())
        self.handle = handle
        HELD.add(self.name)
        try:
            write_json(self.path, {"pid": os.getpid(), **address})
        except (OSError, ValueError):
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
            HELD.remove(self.name)
            raise

    def close(self):
        if self.handle is not None:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self.kernel.ReleaseMutex(self.handle)
                self.kernel.CloseHandle(self.handle)
                self.handle = None
                HELD.remove(self.name)

    @staticmethod
    def running(root):
        kernel = api()
        name = "Local\\TALYX.Host." + identity(root)
        handle = kernel.OpenMutexW(0x00100001, False, name)
        if not handle:
            if ctypes.get_last_error() == 2:
                return None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if name not in HELD:
                state = kernel.WaitForSingleObject(handle, 0)
                if state in {0, 128}:
                    kernel.ReleaseMutex(handle)
                    return None
                if state != 258:
                    raise ctypes.WinError(ctypes.get_last_error())
            path = Path(root).resolve() / "host" / "instance.json"
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise HostAlreadyRunning("TALYX Host is starting; wait for its launch to finish") from None
        finally:
            kernel.CloseHandle(handle)


class DesktopActivation:
    def __init__(self, root, callback):
        self.kernel = api()
        self.handle = self.kernel.CreateEventW(None, False, False, "Local\\TALYX.Desktop." + identity(root))
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.callback = callback
        self.closing = False
        self.thread = threading.Thread(target=self.run, name="desktop-activation")
        self.thread.start()

    def run(self):
        while not self.closing:
            result = self.kernel.WaitForSingleObject(self.handle, 1000)
            if result == 0 and not self.closing:
                self.callback()
            elif result not in {0, 258}:
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        self.closing = True
        self.kernel.SetEvent(self.handle)
        self.thread.join()
        self.kernel.CloseHandle(self.handle)

    @staticmethod
    def exists(root):
        kernel = api()
        handle = kernel.OpenEventW(0x0002, False, "Local\\TALYX.Desktop." + identity(root))
        if not handle:
            if ctypes.get_last_error() == 2:
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(handle)
        return True

    @staticmethod
    def activate(root):
        kernel = api()
        handle = kernel.OpenEventW(0x0002, False, "Local\\TALYX.Desktop." + identity(root))
        if not handle:
            if ctypes.get_last_error() == 2:
                return False
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel.SetEvent(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            return True
        finally:
            kernel.CloseHandle(handle)
