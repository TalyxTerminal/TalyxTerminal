"""A third-party data source: one US listing, ZZXT, with three daily bars from 2024-06-03 and a live bar. It answers
Market through ``market.data_source`` like any module would, and can follow Market's session of a series."""

from __future__ import annotations

import asyncio
from typing import Any

from talyx_sdk.declare import Model, Module
from talyx_sdk.process import Call

from modules.market.backend import calendars
from modules.market.backend.common import now_ns, ns_of_date

module = Module("extsource")
ASKED: list[str] = []
LISTING = {"type": "listing", "market": "US", "code": "ZZXT", "name": "Extension Test Inc", "assetClass": "equity",
           "securityType": "common_stock", "currency": "USD", "calendarId": "XNYS"}


def _bars(start: int, end: int) -> dict[str, list[str]]:
    opened, closed = calendars.bar_grid("XNYS", "1d", max(start, ns_of_date("2024-06-03")),
                                        min(end, ns_of_date("2024-06-06")))
    prices = ["10", "11", "12"][:len(opened)]
    return {"time": [str(int(t)) for t in opened], "closeTime": [str(int(t)) for t in closed], "open": prices,
            "high": prices, "low": prices, "close": prices, "volume": ["100"] * len(opened)}


async def describe(value: dict, call) -> dict:
    ASKED.append("describe")
    return {"rows": [{"scope": {"markets": ["US"]}, "kind": "bars",
                      "periods": [{"timeframe": "1d", "depth": "1990-01-02", "requestRows": 1000}],
                      "live": "realtime", "liveLimit": None, "publishDelay": None, "historyRole": "bulk",
                      "delisted": True, "pointInTime": False}],
            "concurrency": 1, "requestsPerMinute": None, "plan": None, "requestsPerDay": None,
            "subscriptionsPerConnection": None}


async def search(value: dict, call) -> dict:
    ASKED.append("search")
    return {"candidates": [LISTING] if value["query"].upper() == "ZZXT" else []}


async def history(value: dict, call) -> dict:
    ASKED.append("history")
    columns = _bars(int(value["start"]), int(value["end"]))
    return {"columns": columns if columns["time"] else {}, "next": None,
            "emptyReason": None if columns["time"] else "source_has_none", "none": []}


async def live(params: dict, call):
    ASKED.append("live")
    yield {"type": "status", "state": "subscribed", "problem": None}
    opened, closed = calendars.bar_grid("XNYS", "1d", now_ns() - 10 * 86_400 * 10**9, now_ns())
    yield {"type": "bar", "closed": False, "bar": {
        "time": str(int(opened[-1])), "closeTime": str(int(closed[-1])), "open": "13", "high": "14", "low": "12",
        "close": "13.5", "volume": "7"}}
    await asyncio.Event().wait()


module.implement("market.data_source", "extx", actions={"describe": describe, "search": search, "history": history},
                 streams={"live": live})


class Empty(Model):
    pass


class Follow(Model):
    params: dict[str, Any]


@module.action(effects=["read"])
async def asked(input: Empty, call: Call) -> list[str]:
    """Which of the source's methods Market has asked."""
    return list(ASKED)


@module.action(effects=["read"])
async def follow(input: Follow, call: Call) -> list[dict[str, Any]]:
    """Market's session of a series, up to its first item after the replace (at most 20 s)."""
    items = []
    subscription = await call.process.ctx.subscribe("market.session", input.params)
    try:
        async with asyncio.timeout(20):
            async for item in subscription:
                items.append(item)
                if item["type"] != "replace" and item["type"] != "status":
                    break
    except TimeoutError:
        pass
    finally:
        await subscription.close()
    return items
