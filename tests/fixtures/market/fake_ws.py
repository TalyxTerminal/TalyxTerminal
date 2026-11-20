"""Local fake WebSocket services (OKX v5 and Binance combined streams) for live tests.

They speak each venue's protocol closely enough for Market's own clients: OKX
``subscribe``/``unsubscribe`` ops, ``ping``/``pong`` and candle/ticker pushes;
Binance ``SUBSCRIBE`` and ``{"stream", "data"}`` frames. Tests drive the pushes,
drop connections, refuse connections or go silent, and read what was asked.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import WSMsgType, web


class _Server:
    def __init__(self):
        self.connections: list[dict] = []
        self.accepting = True
        self.refusing: int | None = None  # answer the handshake with this HTTP status (a region block: 451)
        self.silent = False
        self.messages: list[dict] = []
        self.runner = None
        self.port = 0

    async def start(self) -> int:
        app = web.Application()
        app.router.add_get("/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        for connection in list(self.connections):
            await connection["ws"].close()
        await self.runner.cleanup()

    def url(self, path: str) -> str:
        return f"ws://127.0.0.1:{self.port}{path}"

    @property
    def open_connections(self) -> list[dict]:
        return [c for c in self.connections if not c["ws"].closed]

    async def handle(self, request):
        if not self.accepting:
            return web.Response(status=503, text="unavailable (test)")
        if self.refusing is not None:
            return web.Response(status=self.refusing, text="refused (test)")
        ws = web.WebSocketResponse(autoping=True)
        await ws.prepare(request)
        connection = {"ws": ws, "subs": set(), "path": request.path}
        self.connections.append(connection)
        self.opened(connection)
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                await self.on_text(connection, message.data)
        finally:
            connection["closed"] = True
        return ws

    def opened(self, connection: dict) -> None:
        pass

    async def send(self, key: str, payload: dict) -> int:
        sent = 0
        if self.silent:
            return 0
        for connection in self.open_connections:
            if key in connection["subs"]:
                try:
                    await connection["ws"].send_str(json.dumps(payload))
                    sent += 1
                except ConnectionError:
                    pass
        return sent

    async def drop(self) -> None:
        for connection in self.open_connections:
            await connection["ws"].close()


class FakeOkx(_Server):
    async def on_text(self, connection, text):
        if text == "ping":
            await connection["ws"].send_str("pong")
            return
        message = json.loads(text)
        self.messages.append(message)
        for arg in message.get("args", []):
            key = f"{arg['channel']}:{arg['instId']}"
            if message["op"] == "subscribe":
                connection["subs"].add(key)
            else:
                connection["subs"].discard(key)
            await connection["ws"].send_str(json.dumps({"event": message["op"], "arg": arg}))

    async def candle(self, inst: str, row: list, confirm: bool, channel: str = "candle1m") -> int:
        return await self.send(f"{channel}:{inst}", {"arg": {"channel": channel, "instId": inst},
                                                     "data": [row[:8] + ["1" if confirm else "0"]]})

    async def ticker(self, inst: str, last: str, ts_ms: int) -> int:
        return await self.send(f"tickers:{inst}", {"arg": {"channel": "tickers", "instId": inst}, "data": [{
            "instType": "SWAP", "instId": inst, "last": last, "bidPx": last, "askPx": last, "open24h": "100",
            "sodUtc0": "100", "ts": str(ts_ms)}]})

    def subscribed(self, key: str) -> int:
        """How many subscribe ops asked for ``key``."""
        return sum(1 for m in self.messages if m.get("op") == "subscribe"
                   for a in m["args"] if f"{a['channel']}:{a['instId']}" == key)


class FakeBinance(_Server):
    def __init__(self, *, lifetime: float | None = None):
        super().__init__()
        self.lifetime = lifetime

    def opened(self, connection: dict) -> None:
        if self.lifetime:
            async def expire():
                await asyncio.sleep(self.lifetime)
                await connection["ws"].close()
            connection["expiry"] = asyncio.ensure_future(expire())

    async def on_text(self, connection, text):
        message = json.loads(text)
        self.messages.append(message)
        for stream in message.get("params", []):
            if message["method"] == "SUBSCRIBE":
                connection["subs"].add(stream)
            else:
                connection["subs"].discard(stream)
        await connection["ws"].send_str(json.dumps({"result": None, "id": message["id"]}))

    async def kline(self, stream: str, symbol: str, row: list, step_ms: int, closed: bool) -> int:
        start = int(row[0])
        return await self.send(stream, {"stream": stream, "data": {"e": "kline", "E": start, "s": symbol, "k": {
            "t": start, "T": start + step_ms - 1, "s": symbol, "i": "1m", "o": row[1], "h": row[2], "l": row[3],
            "c": row[4], "v": row[6], "x": closed}}})
