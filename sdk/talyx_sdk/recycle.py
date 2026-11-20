"""Windows Shell recycling with an explicit veto for permanent deletion: folders the platform and modules move to the
Recycle Bin (a deleted workspace's own folder, a project a module removes).

IFileOperation flags:
https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags
The progress sink also rejects items Windows cannot recycle before deletion.
"""
from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path
from uuid import UUID


class _GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    @classmethod
    def parse(cls, value: str):
        return cls((ctypes.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le))


_IID_UNKNOWN = _GUID.parse("00000000-0000-0000-c000-000000000046")
_IID_SINK = _GUID.parse("04b0f1a7-9490-44bc-96e1-4296a31252e2")
_IID_ITEM = _GUID.parse("43826d1e-e718-42ee-bc55-a1e261c37bfe")
_IID_OPERATION = _GUID.parse("947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8")
_CLSID_OPERATION = _GUID.parse("3ad05575-8857-4850-9277-11b85bdb8e09")
_ABORT = -2147467260  # E_ABORT


class _RecycleError(OSError):
    def __init__(self, result: int, *, events=None, safe_to_retry=False):
        self.hresult = result & 0xffffffff
        self.safe_to_retry = safe_to_retry
        message = f"Windows Recycle Bin failed (HRESULT 0x{self.hresult:08X})."
        if events is not None:
            message += f" Native callbacks: {events}"
        super().__init__(message)


def _check(result: int):
    if result < 0:
        raise _RecycleError(result)


def _method(pointer, slot, *argument_types, result_type=ctypes.c_long):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(table[slot])


def _recycle_allowed(flags: int, path: Path, expected_identity: tuple[int, int]) -> bool:
    # PreDeleteItem does not carry this bit for a permanent deletion. Vetoing
    # it is necessary even when RECYCLEONDELETE was requested (e.g. no Bin).
    if not flags & 0x80:  # TSF_DELETE_RECYCLE_IF_POSSIBLE
        return False
    try:
        stat = path.stat()
        return (stat.st_dev, stat.st_ino) == expected_identity and path.resolve() == path
    except (OSError, RuntimeError):
        return False


class _ProgressSink:
    def __init__(self, path: Path, expected_identity: tuple[int, int]):
        pointer, dword, hresult = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_long
        self.references = 1
        self.completed = False
        self.failure = None
        self.events = []
        self.callbacks = []

        def bind(function, *arguments, result=hresult):
            callback = ctypes.WINFUNCTYPE(result, pointer, *arguments)(function)
            self.callbacks.append(callback)
            return ctypes.cast(callback, pointer).value

        def query(_this, iid, output):
            output[0] = None
            if bytes(iid.contents.data) not in {bytes(_IID_UNKNOWN.data), bytes(_IID_SINK.data)}:
                return -2147467262  # E_NOINTERFACE
            output[0] = ctypes.addressof(self.instance)
            self.references += 1
            return 0

        def add_ref(_this):
            self.references += 1
            return self.references

        def release(_this):
            self.references -= 1
            return self.references

        def finish(_this, result):
            if result < 0:
                self.failure = result
            return 0

        def pre_delete(_this, flags, _item):
            allowed = _recycle_allowed(flags, path, expected_identity)
            self.events.append({"callback": "preDelete", "flags": flags, "allowed": allowed})
            return 0 if allowed else _ABORT

        def post_delete(_this, _flags, _item, result, recycled_item):
            self.events.append({
                "callback": "postDelete", "result": f"0x{result & 0xffffffff:08X}",
                "recycled": bool(recycled_item),
            })
            if result < 0:
                self.failure = result
            self.completed = result >= 0 and bool(recycled_item)
            return 0

        def noop(*_args):
            return 0

        # IUnknown then the complete IFileOperationProgressSink vtable. Keeping
        # callbacks and the instance alive is required until Shell releases it.
        addresses = [
            bind(query, ctypes.POINTER(_GUID), ctypes.POINTER(pointer)),
            bind(add_ref, result=dword),
            bind(release, result=dword),
            bind(noop),  # StartOperations
            bind(finish, hresult),
            bind(noop, dword, pointer, pointer),  # PreRenameItem
            bind(noop, dword, pointer, pointer, hresult, pointer),
            bind(noop, dword, pointer, pointer, pointer),  # PreMoveItem
            bind(noop, dword, pointer, pointer, pointer, hresult, pointer),
            bind(noop, dword, pointer, pointer, pointer),  # PreCopyItem
            bind(noop, dword, pointer, pointer, pointer, hresult, pointer),
            bind(pre_delete, dword, pointer),
            bind(post_delete, dword, pointer, hresult, pointer),
            bind(noop, dword, pointer, pointer),  # PreNewItem
            bind(noop, dword, pointer, pointer, pointer, dword, hresult, pointer),
            bind(noop, dword, dword),  # UpdateProgress
            bind(noop),  # ResetTimer
            bind(noop),  # PauseTimer
            bind(noop),  # ResumeTimer
        ]
        self.table = (pointer * len(addresses))(*addresses)

        class Instance(ctypes.Structure):
            _fields_ = [("vtable", ctypes.POINTER(pointer))]

        self.instance = Instance(self.table)

    @property
    def safe_to_retry(self):
        return (
            not self.completed
            and any(event["callback"] == "postDelete" for event in self.events)
            and not any(event.get("recycled") for event in self.events)
        )


def _recycle_directory_once(
    path: Path, *, expected_identity: tuple[int, int] | None = None,
) -> None:
    if os.name != "nt":
        raise OSError("Moving folders to the Recycle Bin requires Windows.")
    if path.resolve(strict=True) != path:
        raise OSError("The directory changed before it could be recycled.")
    stat = path.stat()
    current_identity = (stat.st_dev, stat.st_ino)
    if expected_identity is not None and current_identity != expected_identity:
        raise OSError("The directory was replaced before it could be recycled.")
    sink = _ProgressSink(path, current_identity)
    pointer, guid_pointer = ctypes.c_void_p, ctypes.POINTER(_GUID)
    ole = ctypes.WinDLL("ole32")
    shell = ctypes.WinDLL("shell32")
    ole.CoInitializeEx.argtypes = [pointer, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoCreateInstance.argtypes = [
        guid_pointer, pointer, ctypes.c_uint32, guid_pointer, ctypes.POINTER(pointer),
    ]
    ole.CoCreateInstance.restype = ctypes.c_long
    shell.SHCreateItemFromParsingName.argtypes = [
        ctypes.c_wchar_p, pointer, guid_pointer, ctypes.POINTER(pointer),
    ]
    shell.SHCreateItemFromParsingName.restype = ctypes.c_long
    operation, item = pointer(), pointer()
    _check(ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED
    try:
        _check(ole.CoCreateInstance(
            ctypes.byref(_CLSID_OPERATION), None, 1,
            ctypes.byref(_IID_OPERATION), ctypes.byref(operation),
        ))
        _check(shell.SHCreateItemFromParsingName(
            str(path), None, ctypes.byref(_IID_ITEM), ctypes.byref(item),
        ))
        # Silent UI, stop on the first error, force recycling, record Explorer
        # undo. No branch falls back to filesystem deletion or permanent delete.
        flags = 0x0004 | 0x0010 | 0x0400 | 0x00080000 | 0x00100000 | 0x20000000
        _check(_method(operation, 5, ctypes.c_uint32)(operation, flags))
        _check(_method(operation, 18, pointer, pointer)(
            operation, item, ctypes.byref(sink.instance),
        ))
        try:
            _check(_method(operation, 21)(operation))
        except _RecycleError as exc:
            raise _RecycleError(
                exc.hresult, events=sink.events,
                safe_to_retry=sink.safe_to_retry,
            ) from exc
        aborted = ctypes.c_int()
        _check(_method(operation, 22, ctypes.POINTER(ctypes.c_int))(
            operation, ctypes.byref(aborted),
        ))
        if sink.failure is not None:
            raise _RecycleError(
                sink.failure, events=sink.events,
                safe_to_retry=sink.safe_to_retry,
            )
        if aborted.value or not sink.completed or path.exists():
            raise OSError("Windows did not confirm moving the folder to the Recycle Bin.")
    finally:
        if item.value:
            _method(item, 2, result_type=ctypes.c_uint32)(item)
        if operation.value:
            _method(operation, 2, result_type=ctypes.c_uint32)(operation)
        ole.CoUninitialize()


def recycle_directory(path: Path, *, expected_identity: tuple[int, int] | None = None) -> None:
    if os.name != "nt":
        raise OSError("Moving folders to the Recycle Bin requires Windows.")
    if expected_identity is None:
        stat = path.stat()
        expected_identity = (stat.st_dev, stat.st_ino)
    # An editor closing this folder releases its file watchers asynchronously. Shell
    # may briefly report source access/sharing failure during that release.
    # The first Shell call can itself wait for seconds. Allow one retry after
    # its source failure; each attempt revalidates the same registered root.
    for attempt in range(2):
        try:
            _recycle_directory_once(path, expected_identity=expected_identity)
            return
        except _RecycleError as exc:
            if (
                not exc.safe_to_retry
                or exc.hresult not in {0x80270021, 0x80270027}
                or attempt == 1
            ):
                raise
            time.sleep(0.1)
