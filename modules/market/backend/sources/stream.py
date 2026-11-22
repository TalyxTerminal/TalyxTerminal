"""Shared WebSocket connections for official live sources.

One pool per venue endpoint multiplexes many streams over few connections (a
connection carries up to ``max_streams``). Each stream has subscribers (queues)
that receive source events:

- ``{"type": "subscribed"}`` once a stream delivers data on a fresh connection;
- the venue's data events, already parsed by the venue's ``route`` function;
- ``{"type": "gap", "problem": ...}`` when the connection carrying it was lost
  (data may be missing until the next ``subscribed``).

A connection is replaced before the venue's planned disconnect (Binance closes
every connection after 24 hours): the new one is opened and subscribed first and
the old one closed once the new one delivers, so consumers see no gap. A
connection that delivers nothing for ``heartbeat`` seconds is treated as dead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from typing import Any

from talyx_sdk.i18n import t

from ..common import MarketError

BACKOFF = (0.5, 1, 2, 5, 10, 30)


class _Connection:
    def __init__(self, pool: WsPool, streams: set[str]):
        self.pool = pool
        self.streams = set(streams)
        self.ws = None
        self.reader: asyncio.Task | None = None
        self.opened = 0.0
        self.delivered: set[str] = set()
        self.first_message = asyncio.Event()
        self.retiring = False

    async def open(self) -> None:
        self.ws = await self.pool.egress.websocket(self.pool.source_id, self.pool.url)
        self.opened = time.monotonic()
        if self.streams:
            await self.send_subscribe(sorted(self.streams))
        self.reader = asyncio.ensure_future(self.read())

    async def send_subscribe(self, streams: list[str]) -> None:
        for message in self.pool.subscribe_messages(streams):
            await self.ws.send_str(message if isinstance(message, str) else json.dumps(message))

    async def send_unsubscribe(self, streams: list[str]) -> None:
        if self.ws is None or self.ws.closed:
            return
        for message in self.pool.unsubscribe_messages(streams):
            with contextlib.suppress(Exception):
                await self.ws.send_str(message if isinstance(message, str) else json.dumps(message))

    async def read(self) -> None:

        pinger = asyncio.ensure_future(self.ping()) if self.pool.ping else None
        problem = None
        try:
            while True:
                try:
                    message = await asyncio.wait_for(self.ws.receive(), self.pool.heartbeat)
                except TimeoutError:
                    problem = {"code": "source_unavailable", "message": t("{source_id} sent nothing for {heartbeat} s", source_id=self.pool.source_id, heartbeat=f'{self.pool.heartbeat:g}'), "sourceId": self.pool.source_id, "status": None,
                               "retryAt": None}
                    break
                kind = getattr(message.type, "name", str(message.type))
                if kind in ("CLOSE", "CLOSING", "CLOSED", "ERROR"):
                    break
                if kind not in ("TEXT", "BINARY"):
                    continue
                data = message.data
                if self.pool.is_pong(data):
                    continue
                try:
                    events = self.pool.route(data)
                except Exception:  # noqa: BLE001 - one bad frame does not end the stream
                    continue
                self.first_message.set()
                for stream, event in events:
                    if stream not in self.streams:
                        continue
                    if stream not in self.delivered:
                        self.delivered.add(stream)
                        self.pool.deliver(stream, {"type": "subscribed", "mode": "live"})
                    self.pool.deliver(stream, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport failure ends this connection
            problem = {"code": "source_unavailable", "message": f"{type(exc).__name__}: {exc}"[:300],
                       "sourceId": self.pool.source_id, "status": None, "retryAt": None}
        finally:
            if pinger:
                pinger.cancel()
        if not self.retiring:
            await self.pool.lost(self, problem or {"code": "source_unavailable",
                                                   "message": t("{source_id} closed the connection", source_id=self.pool.source_id),
                                                   "sourceId": self.pool.source_id, "status": None,
                                                   "retryAt": None})

    async def ping(self) -> None:
        while True:
            await asyncio.sleep(self.pool.ping_interval)
            with contextlib.suppress(Exception):
                await self.ws.send_str(self.pool.ping)

    async def close(self) -> None:
        self.retiring = True
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(BaseException):
                await self.reader
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()


class WsPool:
    def __init__(self, egress, source_id: str, url: str, *, route: Callable[[Any], list[tuple[str, dict]]],
                 subscribe: Callable[[list[str]], list], unsubscribe: Callable[[list[str]], list],
                 max_streams: int = 200, rotate_after: float | None = None, heartbeat: float = 60.0,
                 ping: str | None = None, ping_interval: float = 20.0, pong: str | None = None):
        self.egress, self.source_id, self.url = egress, source_id, url
        self.route, self.subscribe_messages, self.unsubscribe_messages = route, subscribe, unsubscribe
        self.max_streams, self.rotate_after, self.heartbeat = max_streams, rotate_after, heartbeat
        self.ping, self.ping_interval, self.pong = ping, ping_interval, pong
        self.subscribers: dict[str, set[asyncio.Queue]] = {}
        self.connections: list[_Connection] = []
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self.opened_connections = 0

    def is_pong(self, data) -> bool:
        return self.pong is not None and data == self.pong

    def deliver(self, stream: str, event: dict) -> None:
        for queue in tuple(self.subscribers.get(stream, ())):
            queue.put_nowait(event)

    def _spawn(self, coroutine) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def subscribe(self, stream: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            listeners = self.subscribers.setdefault(stream, set())
            listeners.add(queue)
            if len(listeners) > 1:
                carrier = next((c for c in self.connections if stream in c.streams), None)
                if carrier is not None and stream in carrier.delivered:
                    queue.put_nowait({"type": "subscribed", "mode": "live"})
                return queue
            connection = next((c for c in self.connections if not c.retiring
                               and len(c.streams) < self.max_streams and c.ws is not None), None)
            if connection is None:
                connection = _Connection(self, {stream})
                self.connections.append(connection)
                try:
                    await connection.open()
                except MarketError as exc:
                    self.connections.remove(connection)
                    listeners.discard(queue)
                    if not listeners:
                        self.subscribers.pop(stream, None)
                    raise exc
                self.opened_connections += 1
                if self.rotate_after:
                    self._spawn(self._rotate_later(connection))
            else:
                connection.streams.add(stream)
                await connection.send_subscribe([stream])
        return queue

    async def unsubscribe(self, stream: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            listeners = self.subscribers.get(stream)
            if not listeners:
                return
            listeners.discard(queue)
            if listeners:
                return
            del self.subscribers[stream]
            for connection in list(self.connections):
                if stream in connection.streams:
                    connection.streams.discard(stream)
                    connection.delivered.discard(stream)
                    if connection.streams:
                        await connection.send_unsubscribe([stream])
                    else:
                        self.connections.remove(connection)
                        await connection.close()

    async def lost(self, connection: _Connection, problem: dict) -> None:
        """A connection died: report a gap, then reconnect it with backoff."""
        for stream in connection.streams:
            self.deliver(stream, {"type": "gap", "problem": problem})
        connection.delivered.clear()
        self._spawn(self._reconnect(connection))

    async def _reconnect(self, connection: _Connection) -> None:
        attempt = 0
        while connection in self.connections and connection.streams:
            await asyncio.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
            attempt += 1
            if connection not in self.connections or not connection.streams:
                return
            try:
                fresh = _Connection(self, connection.streams)
                await fresh.open()
            except MarketError as exc:
                for stream in connection.streams:
                    self.deliver(stream, {"type": "gap", "problem": exc.problem()})
                continue
            async with self._lock:
                if connection not in self.connections:
                    await fresh.close()
                    return
                fresh.streams = set(connection.streams)
                self.connections[self.connections.index(connection)] = fresh
                self.opened_connections += 1
            await connection.close()
            if self.rotate_after:
                self._spawn(self._rotate_later(fresh))
            return

    async def _rotate_later(self, connection: _Connection) -> None:
        """Replace a connection before the venue's planned disconnect, without a gap."""
        await asyncio.sleep(self.rotate_after)
        while connection in self.connections:
            fresh = _Connection(self, connection.streams)
            # No new "subscribed" for streams that never stopped; a stream the old connection lost does get one.
            fresh.delivered = set(connection.delivered)
            try:
                await fresh.open()
                await asyncio.wait_for(fresh.first_message.wait(), self.heartbeat)
            except (MarketError, TimeoutError):
                await fresh.close()
                await asyncio.sleep(5)
                continue
            async with self._lock:
                if connection not in self.connections:
                    await fresh.close()
                    return
                fresh.streams = set(connection.streams)
                self.connections[self.connections.index(connection)] = fresh
                self.opened_connections += 1
            await connection.close()
            self._spawn(self._rotate_later(fresh))
            return

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for connection in list(self.connections):
            await connection.close()
        self.connections.clear()
        self.subscribers.clear()


async def events(pool: WsPool, stream: str):
    """Async iterator of one stream's events; unsubscribes when the consumer stops."""
    queue = await pool.subscribe(stream)
    try:
        while True:
            yield await queue.get()
    finally:
        await pool.unsubscribe(stream, queue)
