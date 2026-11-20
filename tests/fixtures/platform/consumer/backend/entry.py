"""A test module that depends on echo: subscribes to its ticks and calls it (and late) from its start."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from talyx_sdk.declare import Model, Module
from talyx_sdk.process import Call, Context

module = Module("consumer")
STATE: dict[str, Any] = {"ticks": 0, "latencies": [], "late": None, "readyAt": None}
CTX: Context | None = None


class Empty(Model):
    pass


class Bench(Model):
    rows: int = 10_000
    calls: int = 60
    capability: str = "echo.page"      # what to read pages from, and with what (default: echo's page of ``rows``)
    input: dict[str, Any] | None = None


@module.on_start
async def start(ctx: Context) -> None:
    global CTX
    CTX = ctx
    asyncio.create_task(_follow(ctx))
    asyncio.create_task(_call_late(ctx))
    STATE["readyAt"] = time.time()


async def _follow(ctx: Context) -> None:
    async for tick in await ctx.subscribe("echo.ticks", {"every": 0.02}):
        STATE["ticks"] += 1
        STATE["latencies"].append(time.time() * 1000 - tick["sentAt"])
        del STATE["latencies"][:-5000]


async def _call_late(ctx: Context) -> None:
    try:
        STATE["late"] = {"ok": await ctx.call("late.ping", {}), "at": time.time()}
    except Exception as exc:  # noqa: BLE001
        STATE["late"] = {"error": getattr(exc, "code", repr(exc))}


@module.action(effects=["read"])
async def state(input: Empty, call: Call) -> dict[str, Any]:
    """What the consumer has seen."""
    return {"ticks": STATE["ticks"], "late": STATE["late"], "readyAt": STATE["readyAt"]}


@module.action(effects=["read"])
async def echo_twice(input: Empty, call: Call) -> dict[str, Any]:
    """Call echo (two hops)."""
    return await ctx_call("echo.echo", {"text": "hi"})


async def ctx_call(name, value):
    return await CTX.call(name, value)


@module.action(effects=["read"])
async def bench(input: Bench, call: Call) -> dict[str, Any]:
    """Read pages (echo's, or another module's) over two hops while ticks arrive; latencies in ms."""
    STATE["latencies"].clear()
    durations = []
    for _ in range(input.calls):
        started = time.perf_counter()
        page = await CTX.call(input.capability, input.input if input.input is not None else {"rows": input.rows})
        durations.append((time.perf_counter() - started) * 1000)
        assert len(page["columns"]["time"]) == input.rows
    return {"pages": durations, "ticks": list(STATE["latencies"])}


async def polite(input: dict, call) -> dict:
    return {"text": f"Hello {input['name']}"}


async def rude(input: dict, call) -> dict:
    return {"text": 5, "shout": True}


module.implement("echo.greeter", "polite", actions={"greet": polite})
module.implement("echo.greeter", "rude", actions={"greet": rude})
