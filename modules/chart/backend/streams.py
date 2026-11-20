"""Outgoing private streams: an ordered queue where a newer forming update replaces an unsent
one of the same kind for the same bar time, in its place (a slow reader never piles up stale
ticks). An update for another time, or one put after any other item, takes a new place at the
end. So no indicator or comparison row for a time goes out before the bar for that time, and
no update goes out ahead of a bars append, replace or splice queued before it. A replace
larger than a bridge frame goes out as a replace and appends (``split``)."""
from __future__ import annotations

import asyncio
import json
import math
from collections import deque

FRAME_BUDGET = 1536 * 1024  # the kernel bridge carries frames up to 2 MiB, envelope included
ROW_KEYS = ("type", "instanceId", "comparisonId", "replay")  # what every piece of a split item carries


def latest_key(item: dict):
    kind = item["type"]
    if kind == "update":
        return ("update",)
    if kind in ("indicator", "comparison") and item["op"] == "update":
        return (kind, item.get("instanceId") or item.get("comparisonId"))
    return None


def bar_time(item: dict) -> int:
    return item["bar"]["time"] if item["type"] == "update" else item["time"][0]


def split(item: dict) -> list[dict]:
    """A replace too large for one bridge frame, as a replace of its first rows and appends of
    the rest, in order; per-row columns (lists, and dicts of lists like ``values``) are cut,
    everything else (meta, drawings, price format) stays on the first piece."""
    if item.get("op") != "replace" or "time" not in item:
        return [item]
    size = len(json.dumps(item, separators=(",", ":")))
    if size <= FRAME_BUDGET:
        return [item]
    count = len(item["time"])
    step = math.ceil(count / math.ceil(size * 1.25 / FRAME_BUDGET))
    pieces = []
    for start in range(0, count, step):
        piece = {}
        for key, value in item.items():
            if isinstance(value, list) and len(value) == count:
                piece[key] = value[start:start + step]
            elif key in ("values", "colors"):
                piece[key] = {name: column[start:start + step] for name, column in value.items()}
            elif not pieces or key in ROW_KEYS:
                piece[key] = value
        piece["op"] = "append" if pieces else "replace"
        if not pieces and item["type"] == "bars":
            piece["windowRows"] = count
        pieces.append(piece)
    return pieces


class Outbox:
    def __init__(self):
        self.items: deque = deque()
        self.keys: dict = {}  # key → the unsent slot a same-time update may still replace
        self.ready = asyncio.Event()

    def put(self, item: dict) -> None:
        for piece in split(item):
            self.put_one(piece)

    def put_one(self, item: dict) -> None:
        key = latest_key(item)
        if key is None:
            self.keys.clear()
        else:
            slot = self.keys.get(key)
            if slot is not None and bar_time(slot[0]) == bar_time(item):
                slot[0] = item
                return
        slot = [item]
        if key is not None:
            self.keys[key] = slot
        self.items.append((key, slot))
        self.ready.set()

    async def __aiter__(self):
        while True:
            if not self.items:
                self.ready.clear()
                await self.ready.wait()
                continue
            key, slot = self.items.popleft()
            if key is not None and self.keys.get(key) is slot:
                del self.keys[key]
            yield slot[0]
