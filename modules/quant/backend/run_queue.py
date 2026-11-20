"""One queue for Quant's heavy work: what a person waits for (a factor's Work run, a trial allocation) goes first; background work (sweeps,
batches of training, library updates) runs as CPU and memory allow, inside one total memory budget.

The budget is set here once: half the machine's memory, and never so much that less than 4 GB is left for everything else. Interactive
work may go past the CPU slots but not past the budget; background work waits for both. The queue is visible (`view`).
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import os
import time
from itertools import count

GB = 1024 ** 3


def _memory_status():
    """(total, available) in bytes from Windows' GlobalMemoryStatusEx, or None elsewhere."""
    if os.name != "nt":
        return None
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                    ("totalPage", ctypes.c_ulonglong), ("freePage", ctypes.c_ulonglong), ("totalVirtual", ctypes.c_ulonglong),
                    ("freeVirtual", ctypes.c_ulonglong), ("freeExtended", ctypes.c_ulonglong)]
    status = Status()
    status.length = ctypes.sizeof(Status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise ctypes.WinError()
    return int(status.total), int(status.free)


def physical_memory():
    """The machine's memory in bytes (Windows' GlobalMemoryStatusEx; elsewhere the page count)."""
    found = _memory_status()
    # A machine whose memory cannot be read stops Quant from starting, rather than guessing a budget runs then exceed.
    return found[0] if found else os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def available_memory():
    """The memory free now, in bytes (Windows; elsewhere the free pages, or half the machine)."""
    found = _memory_status()
    return found[1] if found else os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")


def _card_name(card):
    return " ".join(part for part in str((card or {}).get("name", "GPU")).split() if part.lower() not in ("nvidia", "geforce"))


def budget_of(total):
    return int(max(GB, min(total * 0.5, total - 4 * GB)))


class RunQueue:
    def __init__(self, *, memory=None, cpus=None, devices=None):
        # each NVIDIA card's memory budget: 90% of what it has; `devices` is a function or a list (`detect_devices()["nvidia"]`)
        self.devices = devices
        self.total = physical_memory() if memory is None else memory
        self.budget = budget_of(self.total)
        self.cpus = max(1, (os.cpu_count() or 2) // 2) if cpus is None else cpus
        self.running = {}
        self.waiting = []
        self.changed = asyncio.Condition()
        self.order = count()

    def used(self):
        return sum(item["memory"] for item in self.running.values())

    def cards(self):
        found = self.devices() if callable(self.devices) else self.devices
        return {int(card["index"]): card for card in found or []}

    def gpu_budget(self, index):
        card = self.cards().get(int(index))
        return int(0.9 * float(card["memoryTotal"])) if card else None

    def gpu_used(self, index):
        return sum(item["gpu"]["memory"] for item in self.running.values() if item.get("gpu") and item["gpu"]["index"] == index)

    def gpu_fits(self, entry):
        """Whether the card the work names has room for it beside what already runs on it (a job alone on its card always starts)."""
        gpu = entry.get("gpu")
        if not gpu:
            return True
        budget = self.gpu_budget(gpu["index"])
        used = self.gpu_used(gpu["index"])
        return budget is None or used == 0 or used + gpu["memory"] <= budget

    def _fits(self, entry):
        if not self.gpu_fits(entry):
            return False
        if self.used() + entry["memory"] > self.budget and self.running:
            return False
        if entry["interactive"]:
            return True
        background = sum(1 for item in self.running.values() if not item["interactive"])
        if background >= self.cpus:
            return False
        # Background work waits behind every interactive job that is waiting.
        return not any(item["interactive"] for item in self.waiting if item is not entry)

    def _next(self, entry):
        """Whether `entry` is the one to start now: interactive before background, then first come."""
        ready = sorted(self.waiting, key=lambda item: (not item["interactive"], item["order"]))
        for item in ready:
            if self._fits(item):
                return item is entry
        return False

    @contextlib.asynccontextmanager
    async def slot(self, *, owner, label, interactive, memory, on_wait=None, gpu=None):
        """Hold a place while the work runs; `memory` is its expected peak in bytes. `gpu` ({index, memory}) is the card it trains on and the graphics
        memory it needs there: it starts only when that fits beside what runs on the card. `on_wait(position)` is told the place while waiting."""
        entry = {"owner": owner, "label": label, "interactive": bool(interactive), "memory": int(min(memory, self.budget)),
                 "order": next(self.order), "since": int(time.time() * 1000)}
        if gpu:
            entry["gpu"] = {"index": int(gpu["index"]), "memory": int(gpu["memory"])}
        async with self.changed:
            self.waiting.append(entry)
            try:
                while not self._next(entry):
                    if on_wait is not None:
                        on_wait(self.position(entry))
                    await self.changed.wait()
            except BaseException:
                self.waiting.remove(entry)
                self.changed.notify_all()
                raise
            self.waiting.remove(entry)
            entry["started"] = int(time.time() * 1000)
            self.running[owner] = entry
            self.changed.notify_all()
        try:
            yield entry
        finally:
            async with self.changed:
                self.running.pop(owner, None)
                self.changed.notify_all()

    def free(self):
        """What a new job may use: the budget less what runs now, and never more than the machine has free."""
        return int(max(0, min(self.budget - self.used(), available_memory())))

    async def promote(self, owner):
        """Move a waiting job ahead of the other waiting background jobs ("run next")."""
        async with self.changed:
            found = next((item for item in self.waiting if item["owner"] == owner), None)
            if found is None:
                return False
            found["order"] = min([item["order"] for item in self.waiting] + [found["order"]]) - 1
            self.changed.notify_all()
            return True

    def waiting_owners(self):
        return [item["owner"] for item in sorted(self.waiting, key=lambda entry: (not entry["interactive"], entry["order"]))]

    def position(self, entry):
        ready = sorted(self.waiting, key=lambda item: (not item["interactive"], item["order"]))
        return ready.index(entry) + 1 if entry in ready else 0

    def view(self):
        def item(entry, state):
            gpu = entry.get("gpu")
            card = self.cards().get(gpu["index"]) if gpu else None
            waits = f"waits for {_card_name(card)} memory" if state == "waiting" and gpu and not self.gpu_fits(entry) else None
            return {"owner": entry["owner"], "label": entry["label"], "interactive": entry["interactive"], "memory": entry["memory"],
                    "since": entry["since"], "state": state, **({"started": entry["started"]} if "started" in entry else {}),
                    **({"gpu": {**gpu, "name": _card_name(card) if card else f"GPU {gpu['index']}"}} if gpu else {}), **({"waitsFor": waits} if waits else {})}
        waiting = sorted(self.waiting, key=lambda entry: (not entry["interactive"], entry["order"]))
        return {"budget": self.budget, "memory": self.total, "used": self.used(), "cpuSlots": self.cpus,
                "running": [item(entry, "running") for entry in self.running.values()],
                "waiting": [item(entry, "waiting") for entry in waiting]}
