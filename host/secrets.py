"""Modules' secrets (API keys) in Windows Credential Manager, one namespace per data root and module.

Values never reach logs, errors or files. The same key name under another data root is another secret.
"""

from __future__ import annotations

import ctypes
import hashlib
import sys
from pathlib import Path

from talyx_sdk.errors import ContractError

MAX_BYTES = 2560
NOT_FOUND = 1168


class MemorySecrets:
    """For tests and machines without Credential Manager: kept in memory only."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def read(self, target: str) -> str | None:
        return self.values.get(target)

    def write(self, target: str, value: str | None) -> None:
        if value is None:
            self.values.pop(target, None)
        else:
            self.values[target] = value


class CredentialSecrets:
    def __init__(self):
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                        ("UserName", wintypes.LPWSTR)]

        self.Credential = Credential
        self.api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self.api.CredWriteW.argtypes = (ctypes.POINTER(Credential), wintypes.DWORD)
        self.api.CredReadW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.POINTER(ctypes.POINTER(Credential)))
        self.api.CredDeleteW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
        self.api.CredFree.argtypes = (ctypes.c_void_p,)

    def read(self, target: str) -> str | None:
        pointer = ctypes.POINTER(self.Credential)()
        if not self.api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == NOT_FOUND:
                return None
            raise ContractError("secret_store_failed", "Windows Credential Manager could not be read")
        try:
            item = pointer.contents
            return ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize).decode("utf-8")
        finally:
            self.api.CredFree(pointer)

    def write(self, target: str, value: str | None) -> None:
        if value is None:
            if not self.api.CredDeleteW(target, 1, 0) and ctypes.get_last_error() != NOT_FOUND:
                raise ContractError("secret_store_failed", "Windows Credential Manager could not delete the key")
            return
        blob = value.encode("utf-8")
        if not blob or len(blob) > MAX_BYTES:
            raise ContractError("invalid_input", f"A secret is 1 to {MAX_BYTES} bytes")
        buffer = ctypes.create_string_buffer(blob)
        item = self.Credential()
        item.Type, item.TargetName, item.Persist, item.UserName = 1, target, 2, "TALYX"
        item.CredentialBlobSize = len(blob)
        item.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        if not self.api.CredWriteW(ctypes.byref(item), 0):
            raise ContractError("secret_store_failed", "Windows Credential Manager could not store the key")


class Secrets:
    def __init__(self, data_root: Path, store=None):
        self.prefix = "TALYX/" + hashlib.sha256(str(data_root.resolve()).lower().encode()).hexdigest()[:12]
        self.store = store if store is not None else CredentialSecrets() if sys.platform == "win32" \
            else MemorySecrets()

    def target(self, module: str, key: str) -> str:
        return f"{self.prefix}/{module}/{key}"

    def read(self, module: str, key: str) -> str | None:
        return self.store.read(self.target(module, key))

    def write(self, module: str, key: str, value: str | None) -> None:
        self.store.write(self.target(module, key), value)
