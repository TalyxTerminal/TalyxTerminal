"""What a Quant process knows about its own memory (the model process and the strategy process share it): the peak it reached, and what the
machine has free now. Both are reports, not requirements: they say 0 where the platform gives no answer. psutil answers where the environment has it
(the research one); the strategy environment has none, so Windows is asked directly."""
from __future__ import annotations

import os


def _windows_peak():
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if ctypes.windll.kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return int(counters.PeakPagefileUsage)
    return 0


def peak_memory():
    """The process's peak private memory so far, in bytes: on Windows the peak commit (PeakPagefileUsage), which is what the machine had to
    give it; the peak working set leaves out what was paged out and read about 14% low on a 559-name backtest."""
    try:
        import psutil
        info = psutil.Process().memory_info()
        return int(getattr(info, "peak_pagefile", 0) or getattr(info, "rss", 0))
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - the number is a report, not a requirement
        return 0
    try:
        if os.name == "nt":
            return _windows_peak()
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
    except Exception:  # noqa: BLE001
        return 0


def available_memory():
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001
        return 0
