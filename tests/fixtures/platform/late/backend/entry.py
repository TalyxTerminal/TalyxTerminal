"""A test module that takes two seconds to start."""

from __future__ import annotations

import asyncio
import time

from talyx_sdk.declare import Model, Module
from talyx_sdk.process import Call, Context

module = Module("late")
READY = {"at": None}


class Empty(Model):
    pass


@module.on_start
async def start(ctx: Context) -> None:
    await asyncio.sleep(2)
    READY["at"] = time.time()


@module.action(effects=["read"])
async def ping(input: Empty, call: Call) -> dict[str, float]:
    """Answer once started."""
    return {"readyAt": READY["at"]}
