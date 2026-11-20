"""The notebook kernel's start: ipykernel in the research environment, with TALYX's code and the Host's interrupt event.

Windows delivers no interrupt through the kernel's messages. The Host creates a named event; this opens it and hands it to
ipykernel, whose parent poller turns each signal into KeyboardInterrupt in the main thread, so a running cell stops at once
and the variables stay.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from pathlib import Path

EVENT_MODIFY_STATE, SYNCHRONIZE = 0x0002, 0x100000


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", dest="connection", required=True)
    parser.add_argument("--interrupt-event", required=True)
    parser.add_argument("--path", action="append", default=[])
    arguments = parser.parse_args()
    # This file's directory holds adapters, never importable names of the user's code.
    here = Path(__file__).resolve().parent
    sys.path[:] = [path for path in sys.path if Path(path or os.getcwd()).resolve() != here]
    sys.path[:0] = arguments.path
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenEventW.restype = ctypes.c_void_p
    kernel32.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, arguments.interrupt_event)
    if not handle:
        raise ctypes.WinError()
    os.environ["JPY_INTERRUPT_EVENT"] = str(handle)
    from ipykernel.kernelapp import launch_new_instance
    startup = Path(__file__).resolve().parent / "kernel_startup.py"
    launch_new_instance(argv=["-f", arguments.connection, f"--IPKernelApp.exec_files=[{str(startup)!r}]"])


if __name__ == "__main__":
    main()
