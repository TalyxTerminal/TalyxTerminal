import asyncio
import concurrent.futures
import time
from dataclasses import replace
from email.utils import formatdate
from threading import Event
from types import SimpleNamespace

import ccxt
import pytest
from talyx_sdk import rest_queue
from talyx_sdk.errors import ContractError
from talyx_sdk.rest_profiles import RequestProfile
from talyx_sdk.rest_queue import BULK, WRITE, Domain, Ticket

from modules.market.backend.exchange_limits import capture_limits, order_profiles, request_profile


def profile(**changes):
    return replace(RequestProfile('binance', 'domain', 'https://api.binance.com/api/v3',
                                  1000, 60, 10, True, 1, 'x-mbx-used-weight-1m'), **changes)


def headers(moment, used):
    return {'date': formatdate(moment, usegmt=True), 'x-mbx-used-weight-1m': str(used)}


def test_ip_feedback_is_a_high_watermark_and_counts_unreflected_weight():
    domain = Domain(1000, 60)
    first, second = Ticket(profile(), 1, 6001), Ticket(profile(), 2, 6001)
    domain.requests = [first, second]
    domain.observe(second, headers(6002, 500), 6002)
    assert domain.server_used == 500
    # A delayed older response cannot erase another process's 500-weight use.
    domain.observe(first, headers(6001, 100), 6003)
    assert domain.server_used == 500
    assert domain.delay(profile(cost=10), BULK, 6003) == 0
    domain.server_used = 595
    assert domain.delay(profile(), BULK, 6003) > 0
    assert domain.delay(profile(), WRITE, 6003) == 0


def test_missing_feedback_and_cancelled_sent_requests_keep_a_bounded_reservation():
    domain = Domain(1000, 60)
    item = Ticket(profile(), 1, 6001, received=True)
    domain.requests = [item]
    assert domain.delay(profile(), BULK, 6002) > 0
    # With real feedback, up to 6% (one process alone) may remain unreflected; loss doesn't refund it.
    domain.server_window, domain.server_used = 100, 10
    domain.requests = [Ticket(profile(cost=55), 1, 6001, received=True)]
    assert domain.delay(profile(), WRITE, 6002) > 0
    assert domain.delay(profile(), BULK, 6062) == 0


def test_server_windows_ignore_out_of_order_responses_and_bootstrap_the_next_window():
    domain = Domain(1000, 60)
    older = Ticket(profile(), 1, 6059)
    newer = Ticket(profile(), 2, 6061)
    domain.requests = [older, newer]
    domain.observe(newer, headers(6061, 50), 6061)
    domain.observe(older, headers(6059, 900), 6062)
    assert domain.server_window == 101 and domain.server_used == 50
    assert not older.reflected
    assert domain.delay(profile(), BULK, 6121) == 0
    domain.requests.append(Ticket(profile(), 3, 6121))
    assert domain.delay(profile(), WRITE, 6121) > 0


def test_two_process_ledgers_respect_shared_ip_feedback_with_delayed_responses():
    left, right = Domain(1000, 60), Domain(1000, 60)
    left.share = right.share = 0.5          # both registered on the domain (rest_queue.process_share)
    used, pending = 0, []
    for sequence in range(100):
        current = left if sequence % 2 else right
        if current.delay(profile(), BULK, 6001) == 0:
            item = Ticket(profile(), sequence, 6001)
            current.requests.append(item)
            used += item.profile.cost
            pending.append((current, item, used))
        if len(pending) >= 2:
            current, item, observed = pending.pop(-1 if sequence % 3 else 0)
            current.observe(item, headers(6001, observed), 6001)
        assert used <= 1000
    assert used >= 500


def test_binance_spot_and_futures_and_gate_public_private_are_distinct():
    spot, linear, gate = ccxt.binance(), ccxt.binanceusdm(), ccxt.gate()
    a = request_profile(spot, 'time', 'public', 'GET', {}, {'cost': .2})
    b = request_profile(linear, 'time', 'fapiPublic', 'GET', {}, {'cost': 1})
    assert a.scope != b.scope and a.scale == 5
    public = request_profile(gate, 'orders', ['public', 'spot'], 'GET', {}, {'cost': 1})
    private = request_profile(gate, 'orders', ['private', 'spot'], 'POST', {}, {'cost': 1})
    amend = request_profile(gate, 'orders/{order_id}', ['private', 'spot'], 'PATCH', {}, {'cost': 1})
    cancel = request_profile(gate, 'orders/{order_id}', ['private', 'spot'], 'DELETE', {}, {'cost': 1})
    assert public.scope != private.scope == amend.scope != cancel.scope
    gate.uid = 'same-user'
    one = request_profile(gate, 'orders', ['private', 'spot'], 'POST', {}, {'cost': 1})
    gate.apiKey = 'another-key-for-same-user'
    assert request_profile(gate, 'orders', ['private', 'spot'], 'POST', {}, {'cost': 1}).scope == one.scope


def test_order_limits_come_from_exchange_info_and_use_the_account_counter():
    client = ccxt.binance({'apiKey': 'key'})
    base = request_profile(client, 'order', 'private', 'POST', {}, {'cost': .2})
    capture_limits(base, '{"rateLimits":[{"rateLimitType":"ORDERS","interval":"SECOND","intervalNum":10,"limit":100}]}')
    order, = order_profiles(client, base, 'orderList/otoco', 'private', 'POST', {})
    assert order.capacity == 100 and order.window == 10 and order.cost == 3
    assert order.used_header == 'x-mbx-order-count-10s' and order.scope != base.scope


@pytest.mark.anyio
async def test_queue_wait_is_separate_from_network_timeout_and_cancellation_removes_waiter(tmp_path):
    from threading import Event

    from talyx_sdk.rest_fuse import NETWORK_TIMEOUT

    p = profile(scope=str(tmp_path), capacity=10, window=.1, cost=6, feedback=False)
    priority = rest_queue.PRIORITY.set(BULK)
    timeout = NETWORK_TIMEOUT.set(.01)
    try:
        first = await rest_queue.acquire(p)
        rest_queue.owner().finished(first)
        started = time.monotonic()
        await rest_queue.acquire(p)
        assert time.monotonic() - started >= .08
        stop = Event()
        token = rest_queue.STOP_REQUEST.set(stop)
        try:
            waiter = asyncio.create_task(rest_queue.acquire(p))
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        finally:
            rest_queue.STOP_REQUEST.reset(token)
    finally:
        NETWORK_TIMEOUT.reset(timeout)
        rest_queue.PRIORITY.reset(priority)


@pytest.mark.anyio
async def test_cancelling_a_waiting_request_removes_it_from_the_shared_queue(tmp_path):
    p = profile(scope=str(tmp_path), capacity=10, window=10, cost=6, feedback=False)
    priority = rest_queue.PRIORITY.set(BULK)
    try:
        await rest_queue.acquire(p)
        waiting = asyncio.create_task(rest_queue.acquire(p))
        await asyncio.sleep(.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, .3)
        assert len(rest_queue.owner().domains[p.scope].requests) == 1
    finally:
        rest_queue.PRIORITY.reset(priority)


def test_each_native_binance_page_uses_ccxt_actual_weight_without_an_export_weight_copy():
    client = ccxt.binanceusdm()
    config = client.api['fapiPublic']['get']['klines']
    assert [request_profile(client, 'klines', 'fapiPublic', 'GET', {'limit': limit}, config).cost
            for limit in (99, 499, 1000, 1500)] == [1, 2, 5, 10]


def test_unanswered_request_remains_counted_after_its_window_elapsed():
    request = profile(capacity=10, window=.01, cost=6, feedback=False)
    domain = Domain(10, .01)
    ticket = Ticket(request, 1, 100)
    domain.requests.append(ticket)
    assert domain.delay(request, BULK, 101) > 0
    assert domain.requests == [ticket]
    ticket.received = True
    assert domain.delay(request, BULK, 101) == 0


@pytest.mark.anyio
async def test_all_counting_domains_are_reserved_together_after_the_slowest_window(tmp_path):
    queue = rest_queue.RequestQueue()
    ip = profile(scope=str(tmp_path) + '-ip', capacity=10, window=.02, cost=6, feedback=False)
    orders = profile(scope=str(tmp_path) + '-orders', capacity=10, window=.15, cost=6, feedback=False)
    try:
        previous = await asyncio.wrap_future(queue.submit(orders, BULK))
        queue.finished(previous)
        pending = queue.submit((ip, orders), BULK)
        await asyncio.sleep(.04)
        assert not pending.done()
        assert queue.domains[ip.scope].requests == [], 'waiting for orders must not reserve an earlier IP window'
        admitted = await asyncio.wait_for(asyncio.wrap_future(pending), 1)
        assert admitted.sent == admitted.companions[0].sent
        await asyncio.sleep(.03)
        assert queue.domains[ip.scope].delay(ip, BULK, time.time()) > 0
        for ticket in (admitted, *admitted.companions):
            queue.finished(ticket)
    finally:
        queue.close()


@pytest.mark.parametrize('asynchronous', [False, True])
def test_cancel_after_bundle_admission_finishes_every_untransmitted_reservation(monkeypatch, asynchronous):
    first, second = Ticket(profile(), 1, time.time()), Ticket(profile(scope='orders'), 1, time.time())
    first.companions = (second,)
    future = concurrent.futures.Future()
    future.set_result(first)
    completed = []
    monkeypatch.setattr(rest_queue, 'owner', lambda: SimpleNamespace(submit=lambda *args: future, finished=completed.append))
    stop = Event()
    stop.set()
    token = rest_queue.STOP_REQUEST.set(stop)
    try:
        with pytest.raises(ContractError):
            if asynchronous:
                asyncio.run(rest_queue.acquire((first.profile, second.profile)))
            else:
                rest_queue.acquire_sync((first.profile, second.profile))
        assert completed == [first, second]
    finally:
        rest_queue.STOP_REQUEST.reset(token)
