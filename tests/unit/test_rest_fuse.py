import json
import subprocess
import sys
import time
from dataclasses import replace

import ccxt
import ccxt.async_support as ccxt_async
import pytest
import requests
from requests.structures import CaseInsensitiveDict
from talyx_sdk import rest_fuse, rest_profiles
from talyx_sdk.rest_fuse import COOLDOWN_SECONDS, RateLimited, fused
from talyx_sdk.rest_profiles import RequestProfile
from talyx_sdk.rest_queue import Ticket

from modules.market.backend.exchange_limits import ExchangeProfiles

SPOT = "https://demo-api.binance.com/api/v3/time"
FUTURES = "https://demo-fapi.binance.com/fapi/v1/time"
LIMITED = '{"code":-1003,"msg":"Too much request weight used; current limit is 6000 request weight per 1 MINUTE."}'


class Clock:
    now = 1_900_000_000.0
    strftime = staticmethod(time.strftime)
    localtime = staticmethod(time.localtime)

    def time(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(rest_fuse, "time", value)
    monkeypatch.setattr(rest_fuse, "_cooldowns", rest_fuse.Cooldowns())     # in this process only
    monkeypatch.setattr(rest_profiles, "_profiles", ExchangeProfiles())       # the exchanges' words, as Market installs them
    return value


class Reply:
    reason = "reply"

    def __init__(self, status, body='{"serverTime":1}', headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self, errors=None):
        return self.body


class AsyncSession:
    closed, headers = False, {}

    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    def get(self, url, **options):
        self.sent.append(str(url))
        return self.replies.pop(0)

    post = get


class SyncSession:
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []
        self.cookies, self.headers = requests.cookies.RequestsCookieJar(), {}

    def request(self, method, url, **options):
        self.sent.append(url)
        reply = self.replies.pop(0)
        response = requests.Response()
        response.status_code, response.reason, response.url = reply.status, reply.reason, url
        response.headers = CaseInsensitiveDict(reply.headers)
        response._content = reply.body.encode()
        return response


def async_client(session, kind=None):
    client = (kind or fused(ccxt_async.binance))({"enableRateLimit": False})
    client.session, client.own_session = session, False
    return client


@pytest.mark.anyio
async def test_ccxt_generated_endpoints_use_the_queue_and_its_current_request_signature(clock):
    feedback = {"Date": "Thu, 19 Nov 2026 05:00:00 GMT", "X-MBX-USED-WEIGHT-1M": "1"}
    session = AsyncSession(Reply(200, headers=feedback))
    assert await async_client(session).publicGetTime() == {"serverTime": 1}
    assert session.sent[0].endswith("/api/v3/time")
    sync = fused(ccxt.binance)({"enableRateLimit": False})
    sync.session = SyncSession(Reply(200, headers=feedback))
    assert sync.publicGetTime() == {"serverTime": 1}
    assert sync.session.sent[0].endswith("/api/v3/time")


@pytest.mark.anyio
async def test_one_429_stops_every_client_of_that_host_until_retry_after(clock):
    first = AsyncSession(Reply(429, LIMITED, {"Retry-After": "30"}))
    with pytest.raises(RateLimited):
        await async_client(first).fetch(SPOT)
    # Another client in the process, async or synchronous, sends nothing to that host.
    second, third = AsyncSession(), SyncSession()
    with pytest.raises(RateLimited, match="demo-api.binance.com is rate limited. Requests resume at " + time.strftime(
            "%H:%M:%S", time.localtime(clock.now + 30))):
        await async_client(second).fetch(SPOT)
    with pytest.raises(RateLimited):
        sync = fused(ccxt.binance)({"enableRateLimit": False})
        sync.session = third
        sync.fetch(SPOT)
    assert second.sent == [] and third.sent == []
    # Other hosts keep their own budgets.
    futures = AsyncSession(Reply(200))
    assert await async_client(futures).fetch(FUTURES) == {"serverTime": 1}
    clock.now += 30
    later = AsyncSession(Reply(200))
    assert await async_client(later).fetch(SPOT) == {"serverTime": 1}
    assert later.sent == [SPOT]


def test_a_ban_lasts_until_the_expiry_binance_names(clock):
    expiry = int((clock.now + 3600) * 1000)
    body = f'{{"code":-1003,"msg":"Way too many requests; IP(1.2.3.4) banned until {expiry}. Please use the websocket for live updates to avoid bans."}}'
    client = fused(ccxt.binance)({"enableRateLimit": False})
    client.session = SyncSession(Reply(418, body, {"Retry-After": "120"}))
    with pytest.raises(RateLimited):
        client.fetch(SPOT)
    clock.now += 3599
    with pytest.raises(RateLimited):
        client.fetch(SPOT)
    clock.now += 1
    client.session = SyncSession(Reply(200))
    assert client.fetch(SPOT) == {"serverTime": 1}


@pytest.mark.anyio
async def test_a_429_without_retry_after_pauses_for_the_fixed_cooldown(clock):
    with pytest.raises(RateLimited):
        await async_client(AsyncSession(Reply(429, LIMITED))).fetch(SPOT)
    clock.now += COOLDOWN_SECONDS - 1
    with pytest.raises(RateLimited):
        await async_client(AsyncSession()).fetch(SPOT)
    clock.now += 1
    assert await async_client(AsyncSession(Reply(200))).fetch(SPOT) == {"serverTime": 1}


def test_cooling_survives_another_process_and_is_announced_once(tmp_path, clock, monkeypatch):
    announced = []
    monkeypatch.setattr(rest_fuse, "on_cooldown", announced.append)
    rest_fuse.configure(tmp_path)
    rest_fuse.observe(SPOT, 429, {'Retry-After': '90'}, '')
    assert len(announced) == 1
    program = '''
import json,sys
from talyx_sdk import rest_fuse
rest_fuse.configure(sys.argv[1])
rest_fuse.time.time = lambda: float(sys.argv[2])
announced = []
rest_fuse.on_cooldown = announced.append
try:
    rest_fuse.check(sys.argv[3])
except rest_fuse.RateLimited as error:
    print(json.dumps(error.details))
else:
    raise AssertionError('Cooling was lost on restart')
rest_fuse.observe(sys.argv[3], 429, {'Retry-After': '30'}, '')
print(len(announced))
'''
    result = subprocess.run([sys.executable, '-B', '-c', program, str(tmp_path), str(clock.now), SPOT],
                            check=True, capture_output=True, text=True)
    details, count = result.stdout.splitlines()
    assert json.loads(details)['retryAt'] == int((clock.now + 90) * 1000)
    assert count == '0', "the other process's overlapping limit is the same period: not announced again"
    # An overlapping response cannot shorten cooling or create a second notice.
    rest_fuse.observe(SPOT, 429, {'Retry-After': '30'}, '')
    with pytest.raises(RateLimited) as error:
        rest_fuse.check(SPOT)
    assert error.value.retry_at == clock.now + 90
    assert len(announced) == 1
    clock.now += 91
    rest_fuse.observe(SPOT, 429, {'Retry-After': '5'}, '')
    assert len(announced) == 2


@pytest.mark.parametrize(('exchange', 'body'), [('okx', '{"code":"50011","msg":"Rate limit reached"}'),
    ('gate', '{"label":"TOO_MANY_REQUESTS","message":"Too many requests"}')])
def test_business_limits_preserve_scope_and_ordinary_bad_requests_do_not_open_a_fuse(clock, exchange, body):
    p = RequestProfile(exchange, 'example.test||private:orders:user', 'https://example.test', 100, 10, 1)
    ticket = Ticket(p, 1, clock.now)
    rest_fuse.observe(p.url, 400, {}, '{"code":"51000","label":"INVALID_PARAM_VALUE"}', ticket=ticket)
    rest_fuse.check(p.url, scope=p.scope)
    rest_fuse.observe(p.url, 200, {}, body, ticket=ticket)
    with pytest.raises(RateLimited) as error:
        rest_fuse.check(p.url, scope=p.scope)
    assert error.value.details['api'] == exchange
    assert error.value.details['retryAt'] == int((clock.now + 10) * 1000)
    rest_fuse.check(p.url, scope=replace(p, scope='another-endpoint').scope)


def test_gate_reset_header_on_a_success_is_not_a_ban(clock):
    p = RequestProfile('gate', 'example.test||public:spot/tickers', 'https://example.test', 100, 10, 1)
    rest_fuse.observe(p.url, 200, {'X-Gate-RateLimit-Reset-Timestamp': str(int((clock.now + 60) * 1000))},
                      '{}', ticket=Ticket(p, 1, clock.now))
    rest_fuse.check(p.url, scope=p.scope)
