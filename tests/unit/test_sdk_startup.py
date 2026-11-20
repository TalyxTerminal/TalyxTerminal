"""A module process's loop keeps serving while its private routes are built (standard 15)."""

from __future__ import annotations

import asyncio
import time

from talyx_sdk import process
from talyx_sdk.declare import Module


def test_private_routes_are_built_off_the_loop():
    module = Module("slow")

    @module.private_routes
    def routes(ctx):
        time.sleep(0.3)  # a factory importing its web framework and building its routers

        async def app(scope, receive, send):
            raise AssertionError("not called")
        return app

    async def main():
        lags: list[float] = []

        async def probe():
            while True:
                started = time.perf_counter()
                await asyncio.sleep(0.005)
                lags.append(time.perf_counter() - started - 0.005)

        probing = asyncio.create_task(probe())
        await asyncio.sleep(0.05)
        served = await process._run_routes(module, None)
        probing.cancel()
        return served, max(lags)

    served, longest = asyncio.run(main())
    assert served["port"] > 0 and served["secret"]
    assert longest < 0.1, f"the loop stood still {longest * 1000:.0f} ms while the routes were built"
