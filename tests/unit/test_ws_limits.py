import asyncio

import pytest
from talyx_sdk import rest_queue, ws_limits
from talyx_sdk.rest_profiles import RequestProfile

from modules.market.backend.exchange_limits import websocket_profiles


class Socket:
    url = 'wss://fixture.test'

    def __init__(self):
        self.ready = asyncio.get_running_loop().create_future()
        self.connections, self.sent = 0, []

    def connect(self, session, delay):
        self.connections += 1
        return self.ready

    async def close(self):
        pass

    async def send(self, message):
        self.sent.append(message)


@pytest.mark.anyio
async def test_concurrent_watches_share_one_dial_and_close_releases_the_connection_slot(tmp_path, monkeypatch):
    profile = RequestProfile('gate', str(tmp_path), Socket.url, 1, 0, 1)
    monkeypatch.setattr(ws_limits, '_profiles', lambda *args: (profile,) if len(args) == 3 else ())
    socket = Socket()
    ws_limits.guard_client(socket, 'gate', None)
    one, two = socket.connect(None), socket.connect(None)
    assert one is two
    await asyncio.sleep(.02)
    assert socket.connections == 1
    socket.ready.set_result('connected')
    assert await one == 'connected'
    blocked = asyncio.create_task(rest_queue.acquire(profile))
    await asyncio.sleep(.02)
    assert not blocked.done()
    await socket.close()
    ticket = await asyncio.wait_for(blocked, .2)
    rest_queue.owner().finished(ticket)


@pytest.mark.anyio
async def test_close_while_waiting_for_a_connection_slot_cancels_the_dial(tmp_path, monkeypatch):
    profile = RequestProfile('gate', str(tmp_path), Socket.url, 1, 0, 1)
    monkeypatch.setattr(ws_limits, '_profiles', lambda *args: (profile,))
    held = await rest_queue.acquire(profile)
    socket = Socket()
    ws_limits.guard_client(socket, 'gate', None)
    pending = socket.connect(None)
    await asyncio.sleep(.02)
    await socket.close()
    rest_queue.owner().finished(held)
    await asyncio.sleep(.02)
    assert pending.cancelled() and socket.connections == 0


def test_ws_domains_never_share_rest_weight_and_leave_protocol_heartbeat_room():
    # Market's table: a WebSocket has no usage feedback, so the queue divides it among the processes using it.
    dial, = websocket_profiles('binance', 'wss://stream.binance.com', None)
    message, = websocket_profiles('binance', 'wss://stream.binance.com', None, 'socket')
    assert dial.capacity < 300 and dial.window == 300
    assert message.capacity < 5 and message.window == 1
    assert dial.scope != message.scope and not dial.feedback and not message.feedback
