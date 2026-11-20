"""Stands in for Market where a platform test needs the real Chart: what Chart calls, one instrument (OKX BTC/USDT),
and a session stream whose bars the test pushes (``push``). A restarted process starts with the same history."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from talyx_sdk.declare import Model, Module
from talyx_sdk.process import Call

from vocabulary.identity import instrument_id

module = Module("market")
Json = dict[str, Any]
BTC = {"identityVersion": 1, "venueId": "okx", "symbol": "BTC/USDT", "displaySymbol": "BTC/USDT", "marketType": "spot",
       "assetClass": "crypto", "base": "BTC", "quote": "USDT", "contractOrientation": "none", "calendarId": "24_7",
       "adjustmentEligible": False}
BTC["instrumentId"] = instrument_id(BTC)
MINUTE = 60_000
START = int(time.time() * 1000) // MINUTE * MINUTE - 50 * MINUTE   # the last hour, as Market would send it
HISTORY = [{"time": START + i * MINUTE, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}
           for i in range(50)]
SESSIONS: list[asyncio.Queue] = []


class Empty(Model):
    pass


class Bar(Model):
    close: float


def ns(ms: int) -> str:
    return str(ms * 1_000_000)


def columns(rows: list[dict]) -> dict:
    return {"time": [ns(row["time"]) for row in rows], "closeTime": [ns(row["time"] + MINUTE) for row in rows],
            **{key: [repr(row[key]) for row in rows] for key in ("open", "high", "low", "close", "volume")}}


@module.action("instruments.get", effects=["read"])
async def get(input: Json, call: Call) -> Json:
    """Match each requested symbol or ID."""
    return {"items": [
        {"instrument": BTC, "status": "found", "symbol": "okx:BTC/USDT"} if symbol in ("okx:BTC/USDT", BTC["instrumentId"])
        else {"status": "not_found", "symbol": symbol} for symbol in input["symbols"]], "nextCursor": None}


@module.action("instruments.terms", effects=["read"])
async def terms(input: Json, call: Call) -> Json:
    """Trading terms."""
    return {"items": [{"instrumentId": item, "priceIncrement": "0.1"} for item in input["symbols"]]}


@module.action("read_series", effects=["read"])
async def read(input: Json, call: Call) -> Json:
    """Older bars."""
    before = int(input["before"]) // 1_000_000
    rows = [row for row in HISTORY if row["time"] < before][-input["limit"]:]
    return {"columns": columns(rows), "hasMoreBefore": False}


@module.action("calendar.sessions", effects=["read"])
async def sessions(input: Json, call: Call) -> Json:
    """A 24/7 calendar."""
    return {"timezone": "UTC"}


@module.action("calendar.bar_times", effects=["read"])
async def bar_times(input: Json, call: Call) -> Json:
    """Minute bars around the clock."""
    step = int(input["timeframe"][:-1]) * MINUTE
    start, end = int(input["start"]) // 1_000_000, int(input["end"]) // 1_000_000
    opens = list(range(start, end, step))[:5000]
    return {"timezone": "UTC", "evidence": "verified", "coveredFrom": None, "coveredUntil": None,
            "open": [ns(time) for time in opens], "close": [ns(time + step) for time in opens], "next": None,
            "reason": None}


@module.action("read_quotes", effects=["read"])
async def quotes(input: Json, call: Call) -> Json:
    """No quotes."""
    return {"items": []}


@module.action(effects=["read"])
async def subscribers(input: Empty, call: Call) -> Json:
    """How many session streams are open in this process."""
    return {"sessions": len(SESSIONS)}


@module.action(effects=["write"])
async def push(input: Bar, call: Call) -> Json:
    """The next minute bar, closed at ``close``, to every open session."""
    last = HISTORY[-1]
    row = {"time": last["time"] + MINUTE, "open": last["close"], "high": max(last["close"], input.close),
           "low": min(last["close"], input.close), "close": input.close, "volume": 1.0}
    HISTORY.append(row)
    for queue in SESSIONS:
        queue.put_nowait({"type": "append", "columns": columns([row]), "revision": 1})
    return {"sessions": len(SESSIONS)}


@module.stream()
async def session(params: Json, call: Call) -> AsyncIterator[Json]:
    """The window, then live minute bars as they are pushed."""
    queue: asyncio.Queue = asyncio.Queue()
    SESSIONS.append(queue)
    try:
        yield {"type": "replace", "page": {
            "instrumentId": BTC["instrumentId"], "seriesId": params["series"]["seriesId"], "columns": columns(HISTORY),
            "forming": None, "range": None, "gaps": [], "hasMoreBefore": False, "nextBefore": None, "source": None,
            "fill": {"state": "done", "problem": None}, "revision": 1}}
        yield {"type": "status", "state": "live", "problem": None, "since": ns(HISTORY[-1]["time"])}
        while True:
            yield await queue.get()
    finally:
        SESSIONS.remove(queue)
