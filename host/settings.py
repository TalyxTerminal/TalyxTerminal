"""Settings in two layers: global, and per workspace only what differs from global.

Every setting is declared by its owner (``Module.setting``) with a type, a default and whether it is global only
(bound to this machine or shared data, such as a data source's key or a notification channel's sign-in). A read names the
workspace it applies in, or ``"global"``; there is no implicit choice. A workspace reads its own value if it set
one, else the global value, else the default; changing the global value therefore reaches every workspace that did
not set its own.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import orjson
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from talyx_sdk.errors import ContractError
from talyx_sdk.i18n import t

from .files import finish_before_cancel, write_atomic

GLOBAL = "global"


class Settings:
    def __init__(self, path: Path, declarations: Callable[[str], tuple[dict, dict]],
                 workspace_exists: Callable[[str], bool]):
        """``declarations(module) -> (settings, $defs)`` from the module's contract."""
        self.path, self.declarations, self.workspace_exists = path, declarations, workspace_exists
        self.values: dict = orjson.loads(path.read_bytes()) if path.exists() else {"global": {}, "workspaces": {}}
        self.listeners: set[asyncio.Queue] = set()
        # The Host's own reactions to a change, ``(module, workspace_id, keys)``, run before the change is announced.
        self.observers: list[Callable[[str, str, list[str]], None]] = []
        self.saving = asyncio.Lock()

    async def _save(self) -> None:
        """The values as they are now, written off the event loop; a later save always lands after an earlier one."""
        data = orjson.dumps(self.values)
        async with self.saving:
            await finish_before_cancel(asyncio.to_thread(write_atomic, self.path, data))

    def _declared(self, module: str, keys) -> tuple[dict, dict]:
        declared, definitions = self.declarations(module)
        unknown = [key for key in (keys or []) if key not in declared]
        if unknown:
            raise ContractError("invalid_input", t("{module} declares no setting {keys}", module=module, keys=", ".join(unknown)))
        return declared, definitions

    def _scope(self, workspace_id: str) -> None:
        if workspace_id != GLOBAL and not self.workspace_exists(workspace_id):
            raise ContractError("invalid_input", t('Workspace {workspace} is not open; name an open workspace or "global"',
                                                   workspace=workspace_id))

    def read(self, module: str, workspace_id: str, keys: list[str] | None = None) -> dict:
        self._scope(workspace_id)
        declared, _ = self._declared(module, keys)
        own = self.values["global"].get(module, {})
        local = self.values["workspaces"].get(workspace_id, {}).get(module, {}) if workspace_id != GLOBAL else {}
        result = {}
        for key in keys or declared:
            spec = declared[key]
            if key in local and not spec["globalOnly"]:
                result[key] = {"value": local[key], "source": "workspace", "globalOnly": False}
            elif key in own:
                result[key] = {"value": own[key], "source": "global", "globalOnly": spec["globalOnly"]}
            else:
                result[key] = {"value": spec["default"], "source": "default", "globalOnly": spec["globalOnly"]}
        return {"module": module, "workspaceId": workspace_id, "values": result}

    async def write(self, module: str, workspace_id: str, values: dict) -> dict:
        self._scope(workspace_id)
        declared, definitions = self._declared(module, list(values))
        for key, value in values.items():
            spec = declared[key]
            if workspace_id != GLOBAL and spec["globalOnly"]:
                raise ContractError("permission_denied", t("{key} is a global setting; a workspace follows it", key=key))
            try:
                validator(orjson.dumps({**spec["schema"], "$defs": definitions})).validate(value)
            except ValidationError as exc:
                raise ContractError("invalid_input", f"{key}: {exc.message}") from exc
        if workspace_id == GLOBAL:
            self.values["global"].setdefault(module, {}).update(values)
        else:
            self.values["workspaces"].setdefault(workspace_id, {}).setdefault(module, {}).update(values)
        await self._save()
        self._changed(module, workspace_id, list(values))
        return self.read(module, workspace_id, list(values))

    async def reset(self, module: str, workspace_id: str, keys: list[str]) -> dict:
        self._scope(workspace_id)
        self._declared(module, keys)
        if workspace_id == GLOBAL:
            raise ContractError("invalid_input", t("Resetting to global applies to a workspace"))
        local = self.values["workspaces"].get(workspace_id, {}).get(module, {})
        for key in keys:
            local.pop(key, None)
        await self._save()
        self._changed(module, workspace_id, keys)
        return self.read(module, workspace_id, keys)

    async def copy_workspace(self, source: str, copy: str) -> None:
        if source in self.values["workspaces"]:
            self.values["workspaces"][copy] = orjson.loads(orjson.dumps(self.values["workspaces"][source]))
            await self._save()

    async def delete_workspace(self, workspace_id: str) -> None:
        self.values["workspaces"].pop(workspace_id, None)
        await self._save()

    def _changed(self, module: str, workspace_id: str, keys: list[str]) -> None:
        for observer in self.observers:
            observer(module, workspace_id, keys)
        for queue in self.listeners:
            queue.put_nowait({"module": module, "workspaceId": workspace_id, "keys": keys})

    async def changes(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self.listeners.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self.listeners.discard(queue)


@functools.lru_cache(maxsize=512)
def validator(schema: bytes) -> Draft202012Validator:
    """One validator per declared schema, built once."""
    return Draft202012Validator(orjson.loads(schema))
