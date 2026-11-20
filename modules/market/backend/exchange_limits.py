"""The exchanges' limits as Market knows them: counting domains, costs and how each says "too many".

Installed into the SDK's request queue (``rest_profiles.use``) when Market starts; the SDK itself knows no exchange.
"""
import json
import re
from dataclasses import replace
from hashlib import sha256
from urllib.parse import urlsplit

from talyx_sdk.rest_profiles import Profiles, RequestProfile

_order_limits = {}
_BAN_UNTIL = re.compile(r"banned until (\d{13})")
ERROR_BODY = 4096  # bytes: longer answers are data, not a venue's rate-limit error


def request_profile(client, path, api, method, params, config):
    parts = api if isinstance(api, list) else [api]
    urls = client.urls['api']
    # CCXT uses an API family (bitget names it second: public/spot), or one common REST origin.
    family = next((part for part in parts if part in urls), None)
    url = urls[family] if family is not None else urls['rest']
    if isinstance(url, dict):
        url = next((url[part] for part in parts if part in url and part != family), None) or next(iter(url.values()))
    if '{hostname}' in url:
        url = url.replace('{hostname}', client.hostname)
    host = urlsplit(url).hostname
    cost = client.calculate_rate_limiter_cost(api, method, path, params, config)
    bucket = client.tokenBucket
    window = (bucket['windowSize'] or 1000) / 1000
    capacity = window * 1000 / client.rateLimit
    scope, scale = str(api), 1
    account = str(client.uid) if client.uid else sha256(client.apiKey.encode()).hexdigest() if client.apiKey else 'public'
    if client.id.startswith('binance'):
        if host.startswith(('api1.', 'api2.', 'api3.', 'api4.')):
            host = 'api.binance.com'
        family = 'usdm' if parts[0].startswith('fapi') else 'coinm' if parts[0].startswith('dapi') else 'spot'
        scope = family
        if family == 'spot':
            # CCXT expresses spot weights in units of its one-weight time call.
            time_cost = client.api['public']['get']['time']['cost']
            scale = 1 / time_cost
        window = 60
        capacity = window * 1000 / client.rateLimit
    elif client.id == 'gate':
        public, product = parts
        endpoint = '/'.join((product, path))
        scope = f'{public}:{endpoint}'
        if public == 'private':
            scope += ':' + account
            if method != 'GET' and ('orders' in path or 'batch_order' in path):
                group = 'cancel' if method == 'DELETE' else 'place-amend'
                scope = f'private:{product}:{group}:{account}'
        # CCXT's rateLimit remains the conservative baseline until endpoint
        # response headers give its actual capacity; unrelated endpoints differ.
    elif client.id == 'hyperliquid':
        # 1200 weight a minute per IP; CCXT spreads it as one weight per rateLimit milliseconds.
        window = 60
        capacity = window * 1000 / client.rateLimit
    elif client.id == 'okx':
        scope = '/'.join((*parts, method, path))
        if parts[0] == 'private':
            scope += ':' + account
    proxy = client.httpsProxy or client.httpProxy or ''
    identity = '|'.join((host, proxy, scope))
    return RequestProfile(client.id, identity, url, capacity, window, cost,
                          client.id.startswith('binance'), scale, 'x-mbx-used-weight-1m')


def capture_limits(profile, body):
    """Binance publishes independent account order limits with its market catalog."""
    # Only the rateLimits array is read: the catalog around it is 5 MB, and parsing it all again held Market's loop ~250 ms.
    start = body.find('"rateLimits":') if profile.api.startswith('binance') else -1
    if start >= 0:
        rows, _end = json.JSONDecoder().raw_decode(body, start + len('"rateLimits":'))
        _order_limits[profile.scope] = tuple(row for row in rows if row['rateLimitType'] == 'ORDERS')


def order_profiles(client, profile, path, api, method, params):
    if not client.id.startswith('binance') or method != 'POST' or 'order' not in path.lower():
        return ()
    if api not in {'private', 'fapiPrivate', 'dapiPrivate', 'fapiPrivateV2', 'dapiPrivateV2'}:
        return ()
    account = str(client.uid) if client.uid else sha256(client.apiKey.encode()).hexdigest()
    count = 1
    if path.lower().endswith(('otoco', 'oto')):
        count = 3 if path.lower().endswith('otoco') else 2
    elif path.lower().endswith('oco'):
        count = 2
    if 'batchOrders' in params:
        batch = params['batchOrders']
        count = len(json.loads(batch) if isinstance(batch, str) else batch)
    units = {'SECOND': (1, 's'), 'MINUTE': (60, 'm'), 'DAY': (86400, 'd')}
    return tuple(replace(profile, scope=profile.scope + f'|orders:{account}:{row["intervalNum"]}{units[row["interval"]][1]}',
        capacity=float(row['limit']), window=row['intervalNum'] * units[row['interval']][0],
        cost=count, scale=1, used_header=f'x-mbx-order-count-{row["intervalNum"]}{units[row["interval"]][1]}')
        for row in _order_limits.get(profile.scope, ()))


def websocket_profiles(exchange, url, proxy, connection=None):
    """WebSocket dial and message limits, separate from REST weight. WS handshakes have no usage feedback, so the
    queue divides these among the processes using the same domain."""
    host = urlsplit(url).hostname
    scope = f'ws:{exchange}:{host}:{proxy or ""}'
    if exchange.startswith('binance'):
        if connection is None:
            # 300 attempts / 5 minutes per IP.
            return (RequestProfile(exchange, scope + ':dial', url, 280, 300, 1),)
        # Five incoming control frames/second, including protocol pong. Keep
        # one frame for heartbeat; REST endpoint costs never enter this domain.
        return (RequestProfile(exchange, scope + f':messages:{connection}', url, 4, 1, 1),)
    if exchange == 'gate' and connection is None:
        # Gate permits 300 live sockets/IP.
        return (RequestProfile(exchange, scope + ':connections', url, 280, 0, 1),)
    if exchange == 'okx':
        if connection is None:
            return (RequestProfile(exchange, scope + ':dial', url, 3, 1, 1),)
        return (RequestProfile(exchange, scope + f':messages:{connection}', url, 480, 3600, 1),)
    return ()


class ExchangeProfiles(Profiles):
    """Market's clients are CCXT's: the queue's and the fuse's refusals are CCXT errors (imported when first needed,
    CCXT takes seconds to import)."""

    @property
    def rate_limited(self):
        import ccxt
        return ccxt.RateLimitExceeded

    @property
    def refused(self):
        import ccxt
        return ccxt.BadRequest

    @property
    def busy(self):
        import ccxt
        return ccxt.ExchangeNotAvailable

    @property
    def timeout(self):
        import ccxt
        return ccxt.RequestTimeout

    @property
    def network(self):
        import ccxt
        return ccxt.NetworkError

    def request(self, client, path, api, method, params, config):
        return request_profile(client, path, api, method, params, config)

    def companions(self, client, profile, path, api, method, params):
        return order_profiles(client, profile, path, api, method, params)

    def capture(self, profile, body):
        capture_limits(profile, body)

    def limited(self, profile, status, body):
        exchange = '' if profile is None else profile.api
        limited, reason = status in (418, 429), None
        # A limit answer is a short error object; a data answer (a page of klines, Binance's 5 MB exchangeInfo) is not
        # parsed here a second time on the event loop, where it held the loop for over 200 ms.
        if (exchange in {'okx', 'gate'} or exchange.startswith('binance')) and len(body or '') <= ERROR_BODY:
            try:
                payload = json.loads(body)
            except (TypeError, ValueError):
                payload = {}
            if isinstance(payload, dict):
                limited = limited or (exchange == 'okx' and payload.get('code') == '50011') or (
                    exchange == 'gate' and payload.get('label') in {'TOO_MANY_REQUESTS', 'RATE_LIMIT_EXCEEDED'}) or (
                    exchange.startswith('binance') and str(payload.get('code')) in {'-1003', '-1015'})
                reason = payload.get('msg') or payload.get('message')
        return limited, reason

    def resume_hints(self, profile, headers, body):
        hints = []
        ban = _BAN_UNTIL.search(body or '')
        if ban is not None:
            hints.append(int(ban.group(1)) / 1000)
        if profile is not None and profile.api == 'gate' and 'x-gate-ratelimit-reset-timestamp' in headers:
            hints.append(float(headers['x-gate-ratelimit-reset-timestamp']) / 1000)
        return hints

    def endpoint_limits(self, profile, headers):
        # Gate's headers give endpoint capacity, not a shared private allowance.
        if profile.api == 'gate' and 'x-gate-ratelimit-limit' in headers:
            return float(headers['x-gate-ratelimit-limit']), float(headers['x-gate-ratelimit-requests-remain'])
        return None

    def websocket(self, exchange, url, proxy, connection=None):
        return websocket_profiles(exchange, url, proxy, connection)
