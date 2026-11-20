"""The development stall probe: every process watches its own event loop.

Each process (the Host and every module) owns its loop, so each reports its own stalls: when the loop does not come
back for ``THRESHOLD`` seconds the log names the code it is stuck in, and once a minute it logs how late the loop was
(p50, p99, longest). On only when ``TALYX_DEV`` is set.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback

INTERVAL = 0.005
THRESHOLD = 0.2
REPORT_SECONDS = float(os.environ.get("TALYX_DEV_REPORT_SECONDS", 60))

log = logging.getLogger("talyx.stall")


def enabled() -> bool:
    return bool(os.environ.get("TALYX_DEV"))


class LoopWatch:
    def __init__(self, name: str):
        self.name = name
        self.lags: list[float] = []
        self.beat = time.perf_counter()
        self.thread_id = threading.get_ident()
        self.reported_at = time.monotonic()

    def start(self) -> asyncio.Task:
        """Call from the loop's own thread."""
        self.thread_id = threading.get_ident()
        threading.Thread(target=self._watch, name="stall-probe", daemon=True).start()
        return asyncio.get_running_loop().create_task(self._probe())

    async def _probe(self) -> None:
        while True:
            started = time.perf_counter()
            await asyncio.sleep(INTERVAL)
            self.beat = now = time.perf_counter()
            self.lags.append((now - started - INTERVAL) * 1000)
            if time.monotonic() - self.reported_at >= REPORT_SECONDS:
                self.report()

    def report(self) -> dict:
        ordered, self.lags = sorted(self.lags), []
        self.reported_at = time.monotonic()
        if not ordered:
            return {}
        summary = {"p50": ordered[len(ordered) // 2], "p99": ordered[max(0, int(len(ordered) * 0.99) - 1)],
                   "max": ordered[-1], "samples": len(ordered)}
        log.info("%s loop lag over %d samples: p50 %.1f ms, p99 %.1f ms, longest %.1f ms", self.name,
                 summary["samples"], summary["p50"], summary["p99"], summary["max"])
        return summary

    def _watch(self) -> None:
        reported = None
        while True:
            time.sleep(THRESHOLD / 2)
            beat = self.beat
            if time.perf_counter() - beat > THRESHOLD and reported != beat:
                reported = beat
                frame = sys._current_frames().get(self.thread_id)
                stack = "".join(traceback.format_stack(frame)[-12:]) if frame is not None else "(no stack)"
                log.warning("%s event loop stuck for more than %.0f ms in:\n%s", self.name, THRESHOLD * 1000, stack)


def watch(name: str) -> LoopWatch | None:
    """Start the probe on the running loop when ``TALYX_DEV`` is set."""
    if not enabled():
        return None
    probe = LoopWatch(name)
    probe.start()
    return probe
