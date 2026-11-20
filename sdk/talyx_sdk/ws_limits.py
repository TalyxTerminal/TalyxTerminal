"""WebSocket dial, connection and message limits, separate from REST weight."""
import asyncio
from contextlib import asynccontextmanager

from . import rest_queue
from .rest_profiles import profiles


def _profiles(api, url, proxy, connection=None):
    return profiles().websocket(api, url, proxy, connection)


async def _admit(items):
    tickets = []
    token = rest_queue.PRIORITY.set(rest_queue.INTERACTIVE)
    try:
        for profile in items:
            tickets.append(await rest_queue.acquire(profile))
        return tickets
    finally:
        rest_queue.PRIORITY.reset(token)


@asynccontextmanager
async def socket_budget(api, url, proxy=None):
    tickets = await _admit(_profiles(api, url, proxy))
    try:
        yield
    finally:
        for ticket in tickets:
            rest_queue.owner().finished(ticket)


async def message_budget(api, url, proxy, connection):
    for ticket in await _admit(_profiles(api, url, proxy, connection)):
        rest_queue.owner().finished(ticket)


def guard_client(client, api, proxy):
    """Keep the client's own WebSocket lifecycle and throttler, adding the shared dial limit."""
    connect, close, send = client.connect, client.close, client.send
    pending, tickets = None, []

    def release():
        for ticket in tickets:
            rest_queue.owner().finished(ticket)
        tickets.clear()

    async def connect_once(session, delay):
        tickets.extend(await _admit(_profiles(api, client.url, proxy)))
        try:
            return await connect(session, delay)
        except (asyncio.CancelledError, OSError, profiles().network):
            release()
            raise

    def guarded_connect(session, backoff_delay=0):
        nonlocal pending
        if pending is None:
            pending = asyncio.create_task(connect_once(session, backoff_delay))
        return pending

    async def guarded_close(*args, **kwargs):
        try:
            if pending is not None and not pending.done() and pending is not asyncio.current_task():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            return await close(*args, **kwargs)
        finally:
            release()

    async def guarded_send(message):
        await message_budget(api, client.url, proxy, id(client))
        return await send(message)

    client.connect, client.close, client.send = guarded_connect, guarded_close, guarded_send
