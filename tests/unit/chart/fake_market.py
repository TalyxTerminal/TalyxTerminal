"""An in-process stand-in for Market's public services and session stream (Chart tests)."""
from __future__ import annotations

import asyncio

from talyx_sdk import ContractError

from vocabulary.identity import instrument_id
from vocabulary.instruments import MarketInstrument, ohlcv_series

BTC = {"identityVersion": 1, "venueId": "okx", "symbol": "BTC/USDT",
       "displaySymbol": "BTC/USDT", "marketType": "spot", "assetClass": "crypto", "base": "BTC", "quote": "USDT",
       "contractOrientation": "none", "calendarId": "24_7", "adjustmentEligible": False}
ETH = {**BTC, "symbol": "ETH/USDT", "displaySymbol": "ETH/USDT", "base": "ETH"}
BTC["instrumentId"], ETH["instrumentId"] = instrument_id(BTC), instrument_id(ETH)


def ns(ms: int) -> str:
    return str(ms * 1_000_000)


def market_columns(rows: list[dict]) -> dict:
    return {"time": [ns(row["time"]) for row in rows], "closeTime": [ns(row["time"] + 60_000) for row in rows],
            **{key: [repr(row[key]) for row in rows] for key in ("open", "high", "low", "close")},
            "volume": [repr(row["volume"]) for row in rows]}


def market_row(row: dict) -> dict:
    return {"time": ns(row["time"]), "closeTime": ns(row["time"] + 60_000),
            **{key: repr(row[key]) for key in ("open", "high", "low", "close", "volume")}}


def points_columns(columns: dict) -> dict:
    return {key: [ns(value) if key == "time" else repr(value) for value in values] for key, values in columns.items()}


class Session:
    def __init__(self, market: FakeMarket, params: dict):
        self.market, self.params = market, params
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self.sequence = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is None:
            raise StopAsyncIteration
        self.sequence += 1
        return item

    async def close(self):
        self.closed = True
        self.queue.put_nowait(None)

    async def drained(self):
        """Until Chart has handled every item sent so far: a feed takes each item at once (no
        waiting), and a notification it starts gets its first step."""
        while not self.queue.empty():
            await asyncio.sleep(0)
        for _ in range(2):
            await asyncio.sleep(0)

    # what Market sends
    def replace(self, rows: list[dict], forming: dict | None = None, has_more_before: bool = False, fill: str = "done"):
        self.queue.put_nowait({"type": "replace", "page": {
            "instrumentId": self.params["instrument"]["instrumentId"], "seriesId": self.params["series"]["seriesId"],
            "columns": market_columns(rows), "forming": market_row(forming) if forming else None, "range": None,
            "gaps": [], "hasMoreBefore": has_more_before, "nextBefore": None, "source": None,
            "fill": {"state": fill, "problem": None}, "revision": 1}})

    def append(self, rows: list[dict]):
        self.queue.put_nowait({"type": "append", "columns": market_columns(rows), "revision": 1})

    def update(self, row: dict):
        self.queue.put_nowait({"type": "update", "row": market_row(row), "receivedAt": "0"})

    # a point series (open interest, funding): stored points, then the latest live observation
    def replace_points(self, columns: dict):
        self.queue.put_nowait({"type": "replace", "page": {
            "instrumentId": self.params["instrument"]["instrumentId"], "seriesId": self.params["series"]["seriesId"],
            "columns": points_columns(columns), "forming": None, "range": None, "gaps": [], "hasMoreBefore": False,
            "nextBefore": None, "source": None, "fill": {"state": "done", "problem": None}, "revision": 1}})

    def append_points(self, columns: dict):
        self.queue.put_nowait({"type": "append", "columns": points_columns(columns), "revision": 1})

    def observe(self, row: dict):
        self.queue.put_nowait({"type": "update", "row": {key: ns(value) if key == "time" else repr(value)
                                                         for key, value in row.items()}, "receivedAt": "0"})

    def status(self, state: str, problem: dict | None = None):
        self.queue.put_nowait({"type": "status", "state": state, "problem": problem, "since": "1000000"})


class FakeMarket:
    """The ``ctx`` Chart's marketdata.py calls Market through; ``sessions`` lists every subscription made."""

    def __init__(self, instruments=(BTC, ETH), increment: str | None = "0.1"):
        self.known = {item["instrumentId"]: item for item in instruments}
        self.increment = increment
        self.terms_gate: asyncio.Event | None = None  # set to hold terms back (a source request)
        self.sessions: list[Session] = []
        self.older: list[dict] = []
        self.histories: dict[str, list[dict]] = {}  # per instrument, where a test gives one (screens)
        self.read_gate: asyncio.Event | None = None  # set to hold older-history reads back
        self.redirects: dict[str, str] = {}
        self.settings = {"alertPopup": True, "alertSound": True}  # Chart's, as the platform answers them

    async def subscribe(self, name, params=None, *, workspace=None, cursor=None):
        assert name == "market.session"
        session = Session(self, params)
        self.sessions.append(session)
        return session

    async def call(self, name, value, *, workspace=None):
        handler = {"market.instruments.get": self.get, "market.instruments.terms": self.terms,
                   "market.read_series": self.read,
                   "market.calendar.sessions": self.calendar_sessions, "market.calendar.bar_times": self.bar_times,
                   "market.read_quotes": self.quotes, "platform.settings.read": self.read_settings}[name]
        return await handler(value)

    async def read_settings(self, value):
        return {"module": "chart", "workspaceId": value["workspaceId"],
                "values": {key: {"value": self.settings[key], "source": "default", "globalOnly": False}
                           for key in value["keys"]}}

    async def quotes(self, value):
        return {"items": []}

    async def get(self, value):
        items = []
        for symbol in value["symbols"]:
            target = self.redirects.get(symbol, symbol)
            item = self.known.get(target) or next((r for r in self.known.values() if f"{r['venueId']}:{r['symbol']}" == symbol), None)
            items.append({"symbol": symbol, "instrument": item, "previousId": symbol if target != symbol else None,
                          "status": "redirect" if target != symbol else "found" if item else "not_found"})
        return {"items": items, "nextCursor": None}

    async def terms(self, value):
        if self.terms_gate is not None:
            await self.terms_gate.wait()
        return {"items": [{"instrumentId": item, "priceIncrement": self.increment}
                          for item in value["symbols"]]}

    async def resolve(self, value):
        """The exact texts Chart types (the default chart, the default watchlist); Market's own
        resolve is in test_default_chart."""
        known = {f"okx:{item['symbol']}": item for item in self.known.values() if item["venueId"] == "okx"}
        if value["text"] not in known:
            raise ContractError("not_found", f"No instrument {value['text']}")
        return {"instrument": known[value["text"]], "matched": "contract"}

    async def read(self, value):
        if self.read_gate is not None:
            await self.read_gate.wait()
        before = int(value["before"]) // 1_000_000
        history = self.histories.get(value["instrumentId"], self.older)
        rows = [row for row in history if row["time"] < before][-value["limit"]:]
        return {"columns": market_columns(rows), "hasMoreBefore": bool(rows) and rows[0]["time"] > history[0]["time"]}

    async def bar_times(self, value):
        """A 24/7 calendar of minute bars (1m, 5m, ...), opening from the start asked for."""
        assert value["timeframe"].endswith("m")
        step = int(value["timeframe"][:-1]) * 60_000
        start, end = int(value["start"]) // 1_000_000, (int(value["end"]) + 1) // 1_000_000
        opens = list(range(start, end, step))  # the stand-in bars sit off the minute grid
        return {"timezone": "UTC", "evidence": "verified", "coveredFrom": None, "coveredUntil": None,
                "open": [ns(time) for time in opens], "close": [ns(time + step) for time in opens], "next": None,
                "reason": None}

    async def calendar_sessions(self, value):
        return {"timezone": "UTC"}

    async def session(self, wanted: str) -> Session:
        """The open subscription of an instrument, once Chart has made it."""
        for _ in range(500):
            open_ = [item for item in self.sessions if item.params["instrument"]["instrumentId"] == wanted
                     and not item.closed]
            if open_:
                return open_[-1]
            await asyncio.sleep(0.01)
        raise AssertionError(f"Chart never subscribed to {wanted}")


def series_of(instrument: dict, timeframe: str = "1m") -> dict:
    return ohlcv_series(MarketInstrument.from_wire(instrument), timeframe).to_dict()
