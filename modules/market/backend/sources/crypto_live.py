"""Live crypto bars and tickers.

OKX and Binance spot use their own WebSocket protocols over shared connections
(``stream.WsPool``): many instruments per connection, the Binance 24-hour
disconnect handled by replacing the connection beforehand. Other venues use CCXT
Pro's watch methods. Every stream yields the source events Market expects:
``subscribed``, ``bar`` (row and closed flag), ``ticker`` (a Quote) and ``gap``.
"""

from __future__ import annotations

import json
from decimal import Decimal

from talyx_sdk.i18n import t

from vocabulary.identity import timeframe_nanoseconds

from ..common import MarketError, now_ns
from . import stream
from .quotes import make_quote

MS = 1_000_000

OKX_BUSINESS = "wss://ws.okx.com/ws/v5/business"
OKX_PUBLIC = "wss://ws.okx.com/ws/v5/public"
BINANCE_SPOT = "wss://data-stream.binance.vision/stream"
BINANCE_USDM = "wss://fstream.binance.com/stream"
BINANCE_COINM = "wss://dstream.binance.com/stream"
BINANCE_ROTATE = 23.5 * 3600

_OKX_BAR = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H",
            "6h": "6Hutc", "12h": "12Hutc", "1d": "1Dutc"}


def _number(value) -> float:
    return float("nan") if value in (None, "") else float(value)


# ── OKX ────────────────────────────────────────────────────────────────────


def okx_channel(timeframe: str, price_basis: str) -> str:
    bar = _OKX_BAR[timeframe]
    if price_basis == "mark":
        return f"mark-price-candle{bar}"
    if price_basis == "index":
        return f"index-candle{bar}"
    if price_basis == "last":
        return f"candle{bar}"
    raise MarketError("unsupported", t("OKX publishes no {price_basis} candles", price_basis=price_basis), source_id="okx")


def okx_route(contracts: dict[str, bool]):
    """Parse OKX pushes; ``contracts`` says whether an instId is a derivative (volume in volCcy)."""

    def route(data) -> list[tuple[str, dict]]:
        message = json.loads(data)
        if "event" in message or "data" not in message:
            return []
        arg = message["arg"]
        channel, inst = arg["channel"], arg["instId"]
        key = f"{channel}:{inst}"
        out = []
        if channel.startswith(("candle", "mark-price-candle", "index-candle")):
            step = timeframe_nanoseconds(_timeframe_of(channel))
            for item in message["data"]:
                time = int(item[0]) * MS
                if channel.startswith("candle"):
                    volume = _number(item[6] if contracts.get(inst) else item[5])
                    confirm = item[8]
                else:
                    volume, confirm = float("nan"), item[5]
                out.append((key, {"type": "bar", "closed": confirm == "1", "row": {
                    "time": time, "close_time": time + step, "open": _number(item[1]), "high": _number(item[2]),
                    "low": _number(item[3]), "close": _number(item[4]), "volume": volume}}))
        elif channel == "tickers":
            for item in message["data"]:
                opened = Decimal(item["open24h"]) if item.get("open24h") else None  # the basis is rolling_24h
                change = Decimal(item["last"]) - opened if opened else None
                out.append((key, {"type": "ticker", "quote": make_quote(
                    price=item.get("last"), last=item.get("last"), bid=item.get("bidPx"), ask=item.get("askPx"),
                    change=change, change_percent=None if change is None else change / opened * 100,
                    basis="rolling_24h", as_of=int(item["ts"]) * MS)}))
        return out

    return route


def _timeframe_of(channel: str) -> str:
    bar = channel.split("candle", 1)[1]
    for timeframe, name in _OKX_BAR.items():
        if name == bar:
            return timeframe
    raise ValueError(channel)


def okx_pool(egress, url: str, contracts: dict[str, bool]) -> stream.WsPool:
    def subscribe(keys):
        return [{"op": "subscribe", "args": [{"channel": k.split(":", 1)[0], "instId": k.split(":", 1)[1]}
                                             for k in keys]}]

    def unsubscribe(keys):
        return [{"op": "unsubscribe", "args": [{"channel": k.split(":", 1)[0], "instId": k.split(":", 1)[1]}
                                               for k in keys]}]

    pool = stream.WsPool(egress, "okx", url, route=okx_route(contracts), subscribe=subscribe,
                         unsubscribe=unsubscribe, max_streams=200, heartbeat=40.0, ping="ping",
                         ping_interval=20.0, pong="pong")
    pool.contracts = contracts
    return pool


# ── Binance ────────────────────────────────────────────────────────────────


def binance_route(data) -> list[tuple[str, dict]]:
    message = json.loads(data)
    if "stream" not in message:
        return []
    key, payload = message["stream"], message["data"]
    if payload.get("e") == "kline":
        k = payload["k"]
        return [(key, {"type": "bar", "closed": bool(k["x"]), "row": {
            "time": int(k["t"]) * MS, "close_time": (int(k["T"]) + 1) * MS, "open": float(k["o"]),
            "high": float(k["h"]), "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"])}})]
    if payload.get("e") == "24hrTicker":
        return [(key, {"type": "ticker", "quote": make_quote(
            price=payload.get("c"), last=payload.get("c"), bid=payload.get("b"), ask=payload.get("a"),
            change=payload.get("p"), change_percent=payload.get("P"), basis="rolling_24h",
            as_of=int(payload["E"]) * MS)})]
    return []


def binance_pool(egress, url: str, rotate_after: float = BINANCE_ROTATE, heartbeat: float = 60.0) -> stream.WsPool:
    counter = iter(range(1, 1 << 62))

    def subscribe(keys):
        return [{"method": "SUBSCRIBE", "params": list(keys), "id": next(counter)}]

    def unsubscribe(keys):
        return [{"method": "UNSUBSCRIBE", "params": list(keys), "id": next(counter)}]

    return stream.WsPool(egress, "binance", url, route=binance_route, subscribe=subscribe, unsubscribe=unsubscribe,
                         max_streams=1000, rotate_after=rotate_after, heartbeat=heartbeat)


# ── CCXT Pro (other venues) ────────────────────────────────────────────────


async def ccxt_bars(client, symbol: str, timeframe: str, params: dict):
    step = timeframe_nanoseconds(timeframe)
    announced = False
    while True:
        candles = await client.watch_ohlcv(symbol, timeframe, None, None, params)
        if not announced:
            announced = True
            yield {"type": "subscribed", "mode": "live"}
        now = now_ns()
        for item in candles[-3:]:
            time = int(item[0]) * MS
            yield {"type": "bar", "closed": time + step <= now, "row": {
                "time": time, "close_time": time + step, "open": _number(item[1]), "high": _number(item[2]),
                "low": _number(item[3]), "close": _number(item[4]), "volume": _number(item[5])}}


async def ccxt_ticker(client, symbol: str):
    announced = False
    while True:
        ticker = await client.watch_ticker(symbol)
        if not announced:
            announced = True
            yield {"type": "subscribed", "mode": "live"}
        yield {"type": "ticker", "quote": quote_of_ticker(ticker)}


def quote_of_ticker(ticker: dict) -> dict:
    stamp = ticker.get("timestamp")
    return make_quote(price=ticker.get("last"), last=ticker.get("last"), bid=ticker.get("bid"), ask=ticker.get("ask"),
                      mark=(ticker.get("info") or {}).get("markPrice") if isinstance(ticker.get("info"), dict)
                      else None, change=ticker.get("change"), change_percent=ticker.get("percentage"),
                      basis="rolling_24h", as_of=int(stamp) * MS if stamp else None)


# ── observations (session stream: ticker, order book, trades, funding, open interest) ──


def text(value) -> str | None:
    """A number as a decimal string (vocabulary Decimal); None stays None."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    import numpy as np
    return np.format_float_positional(number, trim="-")


def _stamp(value) -> str:
    return str(int(value) * MS) if value else str(now_ns())


def ticker_row(ticker: dict) -> dict:
    info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
    return {"time": _stamp(ticker.get("timestamp")), "last": text(ticker.get("last")),
            "change": text(ticker.get("change")), "changePercent": text(ticker.get("percentage")),
            "high": text(ticker.get("high")), "low": text(ticker.get("low")),
            "baseVolume": text(ticker.get("baseVolume")), "quoteVolume": text(ticker.get("quoteVolume")),
            "bid": text(ticker.get("bid")), "ask": text(ticker.get("ask")),
            "mark": text(ticker.get("markPrice") or info.get("markPrice") or info.get("markPx")),
            "index": text(ticker.get("indexPrice") or info.get("indexPrice") or info.get("idxPx"))}


def book_row(book: dict, depth: int) -> dict:
    limit = depth or 50
    return {"time": _stamp(book.get("timestamp")),
            "bids": [[text(level[0]), text(level[1])] for level in book.get("bids", [])[:limit]],
            "asks": [[text(level[0]), text(level[1])] for level in book.get("asks", [])[:limit]]}


def trade_row(trade: dict) -> dict:
    return {"time": _stamp(trade.get("timestamp")), "price": text(trade.get("price")),
            "amount": text(trade.get("amount")), "side": trade.get("side"),
            "id": None if trade.get("id") is None else str(trade["id"])}


def funding_row(value: dict) -> dict:
    """``nextFundingTime`` is when ``rate`` is settled: CCXT's ``fundingTimestamp`` (its ``nextFundingTimestamp``
    is the settlement after that one, for the predicted next rate)."""
    settles = value.get("fundingTimestamp")
    return {"time": _stamp(value.get("timestamp")), "rate": text(value.get("fundingRate")),
            "nextFundingTime": str(int(settles) * MS) if settles else None,
            "mark": text(value.get("markPrice")), "index": text(value.get("indexPrice"))}


def open_interest_values(value: dict, instrument: dict) -> tuple[float | None, float | None]:
    """(base units, quote value) of one CCXT open interest record. CCXT's amount counts contracts: a linear contract
    times its size is base units (Binance's and Bybit's size is 1); an inverse contract counts quote currency, so it has
    no base value here. A venue that gives neither leaves it null, never another number in its place."""
    amount, size = value.get("openInterestAmount"), instrument.get("contractSize")
    base = float(amount) * float(size) if amount is not None and size is not None \
        and instrument.get("contractOrientation") == "linear" else None
    quote = value.get("openInterestValue")
    return base, None if quote is None else float(quote)


def open_interest_row(value: dict, instrument: dict) -> dict:
    base, quote = open_interest_values(value, instrument)
    return {"time": _stamp(value.get("timestamp")), "value": text(base), "valueQuote": text(quote)}


async def ccxt_observations(client, symbol: str, kind: str, depth: int):
    """CCXT Pro watch methods as session events (incremental books are kept by CCXT)."""
    announced = False
    while True:
        if kind == "ticker":
            event = {"type": "observation", "row": ticker_row(await client.watch_ticker(symbol))}
        elif kind == "order_book":
            book = await client.watch_order_book(symbol, depth or None)
            event = {"type": "observation", "row": book_row(book, depth)}
        else:
            trades = await client.watch_trades(symbol)
            event = {"type": "trades", "rows": [trade_row(item) for item in trades[-100:]]}
        if not announced:
            announced = True
            yield {"type": "subscribed", "mode": "live"}
        yield event
