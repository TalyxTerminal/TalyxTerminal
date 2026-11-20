"""The ``chart.workspace`` stream: each workspace's subscribers get its object changes."""
from __future__ import annotations

from collections.abc import Callable

from .streams import Outbox


class Workspaces:
    def __init__(self, snapshot: Callable[[str], dict]):
        self.snapshot = snapshot
        self.outboxes: dict[str, list[Outbox]] = {}

    def publish(self, workspace: str, item: dict) -> None:
        for outbox in self.outboxes.get(workspace, ()):
            outbox.put(item)

    def publish_all(self, item: dict) -> None:
        for outboxes in self.outboxes.values():
            for outbox in outboxes:
                outbox.put(item)

    def subscribe(self, workspace: str):
        outbox = Outbox()
        outbox.put({"type": "snapshot", **self.snapshot(workspace)})
        self.outboxes.setdefault(workspace, []).append(outbox)

        async def items():
            try:
                async for item in outbox:
                    yield item
            finally:
                self.outboxes[workspace].remove(outbox)
        return items()
