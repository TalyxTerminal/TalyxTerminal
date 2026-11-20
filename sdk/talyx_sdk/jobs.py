"""Windows Job Objects: a process and everything it starts end together.

Every module process sits in its own job (the Host's last line), and every process a module starts through
``spawn`` sits in a job of its own, so stopping a run ends its whole tree (training workers included). On other
systems a job is a process group ended with ``killpg``.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import signal
import subprocess
import sys
from collections.abc import Sequence

if sys.platform == "win32":
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel.OpenProcess.restype = wintypes.HANDLE
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    _kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel.WaitForSingleObject.restype = wintypes.DWORD
    _kernel.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.c_void_p)

    class _Limits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("a", "b", "c", "d", "e", "f")]

    class _Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _Limits), ("IoInfo", _Counters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in ("a", "b", "c", "d")] + \
                   [(name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]


class Job:
    """One kill-on-close job holding a process tree."""

    def __init__(self, process: asyncio.subprocess.Process | subprocess.Popen, *, suspended: bool = False):
        """``suspended``: the process was created suspended (so nothing it starts escapes the job); it is resumed
        once inside."""
        self.process = process
        self.handle = None
        self.ending: list = []      # handles of processes terminated but maybe not gone yet
        if sys.platform == "win32":
            self.handle = _kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            # TERMINATE | SET_QUOTA | SUSPEND_RESUME
            process_handle = _kernel.OpenProcess(0x0001 | 0x0100 | 0x0800, False, process.pid)
            try:
                if not _kernel.AssignProcessToJobObject(self.handle, process_handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                if suspended:
                    _ntdll.NtResumeProcess(process_handle)
            finally:
                _kernel.CloseHandle(process_handle)

    def alive(self) -> bool:
        """Whether any process of the tree still runs."""
        if sys.platform == "win32" and self.handle:
            info = _Accounting()
            _kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None)
            return info.active > 0
        return self.process.returncode is None

    def terminate(self) -> None:
        """End every process of the tree (asynchronously: ``ended`` waits until they are gone)."""
        if sys.platform == "win32":
            if self.handle:
                for pid in self._members():
                    handle = _kernel.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
                    if handle:
                        self.ending.append(handle)
                _kernel.TerminateJobObject(self.handle, 1)
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def ended(self, seconds: float = 5) -> bool:
        """Wait until every process of the tree has exited; False if some still run after ``seconds``."""
        deadline = asyncio.get_running_loop().time() + seconds
        while self.alive() or self._still_ending():
            if asyncio.get_running_loop().time() > deadline:
                return False
            await asyncio.sleep(0.02)
        return True

    def _members(self) -> list[int]:
        class _List(ctypes.Structure):
            _fields_ = [("assigned", wintypes.DWORD), ("listed", wintypes.DWORD),
                        ("ids", ctypes.c_size_t * 4096)]
        info = _List()
        if not _kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(info), ctypes.sizeof(info), None):
            return []
        return list(info.ids[:info.listed])

    def _still_ending(self) -> bool:
        if sys.platform != "win32":
            return False
        running = []
        for handle in self.ending:
            if _kernel.WaitForSingleObject(handle, 0) == 0x102:   # WAIT_TIMEOUT
                running.append(handle)
            else:
                _kernel.CloseHandle(handle)
        self.ending = running
        return bool(running)

    def close(self) -> None:
        if sys.platform == "win32" and self.handle:
            _kernel.TerminateJobObject(self.handle, 1)
            _kernel.CloseHandle(self.handle)
            self.handle = None


async def spawn(args: Sequence[str], **options) -> tuple[asyncio.subprocess.Process, Job]:
    """Start a process in a job of its own (``asyncio.create_subprocess_exec`` options)."""
    if sys.platform != "win32":
        options.setdefault("start_new_session", True)
    else:
        # Created suspended and resumed inside the job: a venv's python.exe starts the real interpreter at once,
        # which would otherwise run outside the job.
        options["creationflags"] = options.get("creationflags", 0) | 0x00000004  # CREATE_SUSPENDED
    process = await asyncio.create_subprocess_exec(*args, **options)
    try:
        return process, Job(process, suspended=sys.platform == "win32")
    except OSError:
        process.kill()
        raise
