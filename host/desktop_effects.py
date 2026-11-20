"""What two General settings do on this machine, in the desktop app: start at sign-in, and keep the computer awake
while an open workspace has work running. Both are global-only platform settings; the Host applies them when it
runs for the desktop app (``--desktop``) and whenever they change."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger("talyx.desktop")


class DesktopEffects:
    def __init__(self, host, data_dir: Path):
        from .desktop.settings import LoginStartup, PowerRequest
        self.host = host
        self.startup = LoginStartup(data_dir)
        self.power = PowerRequest()
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self._follow())

    def _value(self, key: str):
        return self.host.settings.read("platform", "global", [key])["values"][key]["value"]

    def _apply_startup(self) -> None:
        try:
            self.startup.configure(self._value("launchOnLogin"))
        except OSError:
            log.exception("Windows did not take the sign-in startup setting")

    async def _follow(self) -> None:
        self._apply_startup()
        changes = self.host.settings.changes()
        next_change = asyncio.ensure_future(anext(changes))
        while True:
            busy = self._value("keepAwakeWhileWatching") and any(
                self.host.activities(workspace["workspaceId"]) for workspace in self.host.workspaces.list_open())
            if busy != self.power.requested:
                try:
                    await self.power.set(busy)
                except OSError:
                    log.exception("Windows rejected the keep-awake request")
            done, _ = await asyncio.wait({next_change}, timeout=2)
            if done:
                change = next_change.result()
                if change["module"] == "platform" and "launchOnLogin" in change["keys"]:
                    self._apply_startup()
                next_change = asyncio.ensure_future(anext(changes))

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
        await self.power.close()
