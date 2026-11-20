"""Windows process facts for the performance commands (scripts/perf.py): who runs under whom, how much memory and CPU each
process uses, what environment and command line it was started with, and how busy the whole machine is."""

import ctypes
import subprocess
import time
from ctypes import wintypes

_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")

_QUERY_LIMITED, _QUERY, _VM_READ = 0x1000, 0x0400, 0x0010
_SNAPSHOT_PROCESS = 0x2


class _Entry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                ("module", wintypes.DWORD), ("threads", wintypes.DWORD), ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]


class _Memory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("page_faults", wintypes.DWORD), ("peak_working_set", ctypes.c_size_t),
                ("working_set", ctypes.c_size_t), ("peak_paged", ctypes.c_size_t), ("paged", ctypes.c_size_t),
                ("peak_non_paged", ctypes.c_size_t), ("non_paged", ctypes.c_size_t), ("pagefile", ctypes.c_size_t),
                ("peak_pagefile", ctypes.c_size_t), ("private", ctypes.c_size_t)]


class _Basic(ctypes.Structure):
    _fields_ = [("exit_status", ctypes.c_void_p), ("peb", ctypes.c_void_p), ("reserved", ctypes.c_void_p * 2),
                ("pid", ctypes.c_void_p), ("parent", ctypes.c_void_p)]


class _Time(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


_kernel.OpenProcess.restype = wintypes.HANDLE
_kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Entry)]
_kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Entry)]
_kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Memory), wintypes.DWORD]
_kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Time), ctypes.POINTER(_Time), ctypes.POINTER(_Time),
                                    ctypes.POINTER(_Time)]
_kernel.GetSystemTimes.argtypes = [ctypes.POINTER(_Time), ctypes.POINTER(_Time), ctypes.POINTER(_Time)]
_kernel.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                      ctypes.POINTER(ctypes.c_size_t)]
_ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                                             ctypes.POINTER(wintypes.ULONG)]


def _ticks(value: _Time) -> int:
    return (value.high << 32) | value.low


def processes() -> dict[int, tuple[int, str]]:
    """Every running process: pid -> (parent pid, executable name)."""
    snapshot = _kernel.CreateToolhelp32Snapshot(_SNAPSHOT_PROCESS, 0)
    entry = _Entry()
    entry.size = ctypes.sizeof(_Entry)
    found = {}
    try:
        more = _kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            found[entry.pid] = (entry.parent, entry.exe)
            more = _kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel.CloseHandle(snapshot)
    return found


def children(table: dict[int, tuple[int, str]], pid: int) -> list[int]:
    return [child for child, (parent, _) in table.items() if parent == pid and child != pid]


def tree(table: dict[int, tuple[int, str]], pid: int) -> list[int]:
    """``pid`` and everything below it."""
    found, frontier = [pid], [pid]
    while frontier:
        frontier = [child for parent in frontier for child in children(table, parent) if child not in found]
        found += frontier
    return found


def _open(pid: int, access: int):
    handle = _kernel.OpenProcess(access, False, pid)
    return handle or None


def private_bytes(pid: int) -> int | None:
    """Private (committed, not shared) memory of the process; None when it is gone."""
    handle = _open(pid, _QUERY_LIMITED)
    if handle is None:
        return None
    try:
        memory = _Memory()
        memory.cb = ctypes.sizeof(_Memory)
        return memory.private if _kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb) else None
    finally:
        _kernel.CloseHandle(handle)


def cpu_seconds(pid: int) -> float | None:
    """Kernel plus user CPU time the process has used so far; None when it is gone."""
    handle = _open(pid, _QUERY_LIMITED)
    if handle is None:
        return None
    try:
        created, exited, kernel, user = _Time(), _Time(), _Time(), _Time()
        if not _kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return (_ticks(kernel) + _ticks(user)) / 1e7
    finally:
        _kernel.CloseHandle(handle)


def _read(handle, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    done = ctypes.c_size_t()
    if not _kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(done)):
        raise OSError(f"cannot read process memory ({ctypes.get_last_error()})")
    return buffer.raw[:done.value]


def _pointer(handle, address: int) -> int:
    return int.from_bytes(_read(handle, address, 8), "little")


# Offsets in a 64-bit process: PEB.ProcessParameters, and in RTL_USER_PROCESS_PARAMETERS the command line (a UNICODE_STRING),
# the environment block and its size.
_PEB_PARAMETERS, _PARAMETERS_COMMAND_LINE, _PARAMETERS_ENVIRONMENT, _PARAMETERS_ENVIRONMENT_SIZE = 0x20, 0x70, 0x80, 0x3F0


def _parameters(pid: int, read):
    handle = _open(pid, _QUERY | _VM_READ)
    if handle is None:
        return None
    try:
        basic = _Basic()
        if _ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic), None) != 0:
            return None
        return read(handle, _pointer(handle, basic.peb + _PEB_PARAMETERS))
    except OSError:
        return None  # another user's or protected process, or one that just ended
    finally:
        _kernel.CloseHandle(handle)


def command_line(pid: int) -> str | None:
    def read(handle, parameters):
        length = int.from_bytes(_read(handle, parameters + _PARAMETERS_COMMAND_LINE, 2), "little")
        return _read(handle, _pointer(handle, parameters + _PARAMETERS_COMMAND_LINE + 8), length).decode("utf-16-le")
    return _parameters(pid, read)


def environment(pid: int) -> dict[str, str] | None:
    """The environment the process was started with (names upper-cased), or None when it cannot be read."""
    def read(handle, parameters):
        size = int.from_bytes(_read(handle, parameters + _PARAMETERS_ENVIRONMENT_SIZE, 8), "little")
        block = _read(handle, _pointer(handle, parameters + _PARAMETERS_ENVIRONMENT), size).decode("utf-16-le")
        pairs = (item.partition("=") for item in block.split("\0") if item and not item.startswith("="))
        return {name.upper(): value for name, _, value in pairs}
    return _parameters(pid, read)


def machine_times() -> tuple[int, int]:
    """(idle, total) CPU time of the whole machine so far, all cores together."""
    idle, kernel, user = _Time(), _Time(), _Time()
    _kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return _ticks(idle), _ticks(kernel) + _ticks(user)    # kernel time includes the idle time


def busy_between(first: tuple[int, int], last: tuple[int, int]) -> float:
    """How busy the whole machine was between two ``machine_times`` readings (0..1)."""
    total = last[1] - first[1]
    return 1 - (last[0] - first[0]) / total if total else 0.0


def busy_fraction(seconds: float) -> float:
    first = machine_times()
    time.sleep(seconds)
    return busy_between(first, machine_times())


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
