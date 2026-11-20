"""Market's real ``read_series`` (its handler, its types) in a process of its own, on data prepared beforehand and
without the network: the page standard 2 measures."""

from __future__ import annotations

from pathlib import Path

from talyx_sdk.declare import Module
from talyx_sdk.process import Context

from modules.market.backend import entry as market

module = Module("marketpage")
module.action("read_series", effects=["read"])(market.read_series)


@module.on_start
async def start(ctx: Context) -> None:
    from modules.market.backend.app import Market

    directories = await ctx.platform.storage()
    market.market = Market(None, Path(directories["dataDir"]))
    await market.market.start(load_catalogs=False)
