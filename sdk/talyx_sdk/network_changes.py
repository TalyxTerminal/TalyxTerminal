"""Process-owned Windows IP change observation; no transport or source policy."""
from __future__ import annotations

import atexit
import ctypes
import sys
import threading


class NetworkChanges:
    def __init__(self):
        self.epoch = 0
        self.observation = "unavailable"
        self._handles = []
        self._callbacks = []
        self._closed = False
        if sys.platform != "win32":
            return
        try:
            self._library = ctypes.WinDLL("iphlpapi.dll")
            callback_type = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
            for name in ("NotifyRouteChange2", "NotifyIpInterfaceChange", "NotifyUnicastIpAddressChange"):
                callback = callback_type(self._interface_changed if name == "NotifyIpInterfaceChange" else self._changed)
                register = getattr(self._library, name)
                register.argtypes = [ctypes.c_ushort, callback_type, ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(ctypes.c_void_p)]
                register.restype = ctypes.c_ulong
                handle = ctypes.c_void_p()
                if register(0, callback, None, 0, ctypes.byref(handle)) != 0:
                    raise OSError("Windows IP change registration failed")
                self._callbacks.append(callback)
                self._handles.append(handle)
            self.observation = "windows_ip_notifications"
        except (AttributeError, OSError):
            self.close()
            self.observation = "unavailable"

    def _changed(self, _context, _row, notification):
        # MibInitialNotification does not establish a network change. Never read
        # or retain the OS-owned row pointer, and never cancel from this callback.
        if not self._closed and notification != 3:
            self.epoch += 1

    def _interface_changed(self, context, row, notification):
        # Windows reports interface parameter refreshes (MibParameterNotification)
        # about once a minute; they move no route. Added or removed interfaces do.
        if notification != 0:
            self._changed(context, row, notification)

    def close(self):
        self._closed = True
        if self._handles:
            cancel = self._library.CancelMibChangeNotify2
            cancel.argtypes = [ctypes.c_void_p]
            cancel.restype = ctypes.c_ulong
            failures = [handle for handle in self._handles if cancel(handle) != 0]
            self._handles = failures
            if not failures:
                self._callbacks.clear()


_guard = threading.Lock()
_changes = None


def network_changes():
    global _changes
    with _guard:
        if _changes is None:
            _changes = NetworkChanges()
            atexit.register(_changes.close)
        return _changes


def system_route_epoch():
    changes = network_changes()
    return changes.epoch if changes.observation != "unavailable" else None


def route_observation():
    changes = network_changes()
    return {"routeEpoch": system_route_epoch(), "routeObservation": changes.observation}
