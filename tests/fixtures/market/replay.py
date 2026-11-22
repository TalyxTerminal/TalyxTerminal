"""Replays recorded source responses to Market (no network in tests).

``ReplayEgress`` stands in for Market's network egress: requests are answered
from ``recorded/<source>/*.json`` (real responses captured once, see each
folder's README) or from responses a test adds. ``CcxtReplay`` answers CCXT
clients the same way, and serves OKX candles for any window from the recorded
rows (plus deterministic synthetic rows when a test asks for more history than
was recorded).
"""

from __future__ import annotations

import base64
import json
import math
import re
from email.utils import formatdate
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from modules.market.backend import network
from modules.market.backend.network import Egress, Reachability, status_error

RECORDED = Path(__file__).parent / "recorded"


def recorded(source: str, name: str) -> list[dict]:
    return json.loads((RECORDED / source / f"{name}.json").read_text(encoding="utf-8"))["responses"]


def _path(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{unquote(parts.path)}"


class _Headers(dict):
    def get(self, key, default=None):
        for name, value in self.items():
            if name.lower() == key.lower():
                return value
        return default

    def getall(self, key, default=None):
        values = [value for name, value in self.items() if name.lower() == key.lower()]
        return values if values else ([] if default is None else default)


class ReplayEgress(Egress):
    """Egress answering from recordings; records every request it is asked for."""

    def __init__(self, *recordings: tuple[str, str]):
        super().__init__(Reachability(probes=("https://www.okx.com/api/v5/public/time",
                                              "https://data-api.binance.vision/api/v3/ping")))
        self.entries: list[dict] = []
        for source, name in recordings:
            self.entries.extend(recorded(source, name))
        self.requests: list[dict] = []
        self.down: set[str] = set()          # hosts whose connections fail
        self.rules: list = []                # (predicate, status, body, headers), checked first

    def add(self, predicate, body, status: int = 200, headers: dict | None = None) -> None:
        if isinstance(predicate, str):
            text = predicate
            predicate = lambda url, params: text in unquote(url)  # noqa: E731
        self.rules.insert(0, (predicate, status, body, headers or {}))

    def count(self, text: str) -> int:
        return sum(1 for item in self.requests if text in unquote(item["url"]))

    async def probe(self, url: str) -> bool:
        return urlsplit(url).netloc not in self.down

    async def close(self) -> None:
        await super().close()  # WebSocket sessions (live tests) are real

    def _match(self, url: str, params: dict) -> tuple[int, bytes, dict]:
        params = {key: str(value) for key, value in (params or {}).items()}
        for predicate, status, body, headers in self.rules:
            if predicate(url, params):
                if callable(body):
                    body = body(url, params)
                if not isinstance(body, (bytes, str)):
                    body = json.dumps(body)
                return status, body.encode() if isinstance(body, str) else body, _Headers(headers)
        path = _path(url)
        candidates = [entry for entry in self.entries if _path(entry["url"]) == path]
        for name in ("interval", "range", "q"):
            if name in params:
                candidates = [entry for entry in candidates if entry["params"].get(name) == params[name]]
            else:
                candidates = [entry for entry in candidates if name not in entry["params"]]
        if not candidates:
            raise AssertionError(f"No recorded response for {url} {params}")
        for name in ("period1", "startTime"):
            if name in params:
                wanted = int(params[name])
                candidates.sort(key=lambda entry, n=name: abs(int(entry["params"].get(n, 0)) - wanted))
        for name in ("prefix", "marker", "symbol"):
            if name in params or any(name in entry["params"] for entry in candidates):
                candidates = [entry for entry in candidates if entry["params"].get(name) == params.get(name)]
                if not candidates:
                    raise AssertionError(f"No recorded response for {url} {params}")
        entry = candidates[0]
        if "bodyBase64" in entry:
            return entry["status"], base64.b64decode(entry["bodyBase64"]), _Headers()
        return entry["status"], entry["body"].encode(), _Headers()

    async def request(self, source_id, url, *, method="GET", params=None, headers=None, data=None, json_body=None,
                      timeout=20.0, cookies=None, accept_status=()):
        if isinstance(data, dict):
            params = {**(params or {}), **data}
        if isinstance(json_body, dict):
            # JSON-RPC style sources (Tushare): match on the call's name and parameters, never the token.
            params = {**(params or {}), **{k: v for k, v in json_body.items() if k not in ("token", "params", "fields")},
                      **{k: str(v) for k, v in (json_body.get("params") or {}).items()}}
        self.requests.append({"source": source_id, "url": url, "params": dict(params or {})})
        if urlsplit(url).netloc in self.down:
            raise await self.transport_failure(source_id, url, OSError("connection refused (test)"))
        status, body, response_headers = self._match(url, params)
        self.reachability.succeeded(url)
        if status >= 400 and status not in set(accept_status):
            raise status_error(source_id, status, body.decode("utf-8", "replace"), response_headers, url)
        return status, body, response_headers


class CcxtReplay:
    """Patches ``network.ccxt_client`` so CCXT clients answer from recordings."""

    def __init__(self, monkeypatch, *recordings: tuple[str, str], synthetic: bool = False):
        self.entries: list[dict] = []
        for source, name in recordings:
            self.entries.extend(recorded(source, name))
        self.synthetic = synthetic
        self.requests: list[str] = []
        self.down = False
        self.candles: dict[tuple[str, str], dict[int, list]] = {}
        for entry in self.entries:
            if "candles" in entry["url"]:
                query = dict(parse_qsl(urlsplit(entry["url"]).query))
                rows = self.candles.setdefault((query["instId"], query["bar"]), {})
                for item in entry["body"]["data"]:
                    rows[int(item[0])] = item
        original = network.ccxt_client

        def client(exchange_id, *, environment="production", pro=False):
            value = original(exchange_id, environment=environment, pro=pro)

            async def fetch(url, method="GET", headers=None, body=None):
                result = await self._fetch(url, method, headers, body)
                # Let the platform's REST ledger see a response as it would on the wire.
                answer = {"Date": formatdate(usegmt=True), "x-mbx-used-weight-1m": "1", "x-mbx-used-weight": "1"}
                value.on_rest_response(200, "OK", url, method, answer, json.dumps(result), headers or {}, body)
                return result

            value.fetch = fetch
            return value

        monkeypatch.setattr(network, "ccxt_client", client)

    def count(self, text: str) -> int:
        return sum(1 for url in self.requests if text in url)

    async def _fetch(self, url, method="GET", headers=None, body=None):
        import ccxt

        self.requests.append(url)
        if self.down:
            raise ccxt.NetworkError(f"connection refused (test) {url}")
        if "candles" in url:
            return self._candles(url)
        if "/api/v3/klines" in url and self.synthetic:
            return self._binance_klines(url)
        if "/market/tickers" in url and self.synthetic:
            return self._okx_tickers(url)
        for entry in self.entries:
            if entry["url"] == url:
                return entry["body"]
        raise AssertionError(f"No recorded CCXT response for {url}")

    def _candles(self, url: str) -> dict:
        query = dict(parse_qsl(urlsplit(url).query))
        bar = query["bar"]
        step = {"1m": 60_000, "1Dutc": 86_400_000, "4H": 14_400_000, "1H": 3_600_000}[bar]
        after = int(query.get("after", 2**62))
        before = int(query.get("before", -1))
        limit = int(query.get("limit", 100))
        rows = self.candles.get((query["instId"], bar), {})
        picked = sorted((ts for ts in rows if before < ts < after), reverse=True)[:limit]
        data = [rows[ts] for ts in picked]
        if not data and self.synthetic:
            top = after - step if after % step == 0 else after - after % step
            data = [_synthetic(ts, step) for ts in range(top, max(before, top - limit * step), -step)][:limit]
        return {"code": "0", "msg": "", "data": data}


    def _binance_klines(self, url: str) -> list:
        query = dict(parse_qsl(urlsplit(url).query))
        step = {"1m": 60_000, "1d": 86_400_000}[query["interval"]]
        start = int(query.get("startTime", 0))
        start = -(-start // step) * step
        limit = int(query.get("limit", 500))
        end = int(query.get("endTime", start + limit * step))
        rows = []
        for ts in range(start, min(end + 1, start + limit * step), step):
            row = _synthetic(ts, step)
            rows.append([ts, row[1], row[2], row[3], row[4], row[6], ts + step - 1, "0", 1, "0", "0", "0"])
        return rows

    def _okx_tickers(self, url: str) -> dict:
        data = []
        for entry in self.entries:
            if "public/instruments" in entry["url"]:
                for item in entry["body"]["data"]:
                    data.append({"instType": item["instType"], "instId": item["instId"], "last": "100.5",
                                 "lastSz": "1", "askPx": "100.6", "askSz": "1", "bidPx": "100.4", "bidSz": "1",
                                 "open24h": "99", "high24h": "101", "low24h": "98", "volCcy24h": "10",
                                 "vol24h": "1000", "sodUtc0": "99.5", "sodUtc8": "99.5", "ts": "1730678400000"})
        return {"code": "0", "msg": "", "data": data}


def synthetic_row(ts_ms: int, step_ms: int) -> list:
    """The deterministic candle the fake services give for a period (OKX array layout)."""
    return _synthetic(ts_ms, step_ms)


def _synthetic(ts: int, step: int) -> list:
    base = 60000 + 500 * math.sin(ts / 3.6e6)
    return [str(ts), f"{base:.1f}", f"{base + 5:.1f}", f"{base - 5:.1f}", f"{base + 1:.1f}", "1000", "10", "600000",
            "1"]


def synthetic_okx_file(url: str, _params: dict) -> bytes:
    """An OKX one-minute candlestick file (``static.okx.com``) holding the synthetic candles of its UTC+8 day or
    month, in the file's own layout (swap volume in contracts, vol_ccy in the base currency)."""
    import io
    import zipfile
    from datetime import UTC, datetime, timedelta

    name = url.rsplit("/", 1)[-1][:-len(".zip")]
    inst, label = name.split("-candlesticks-")
    first = datetime.strptime(label + ("-01" if len(label) == 7 else ""), "%Y-%m-%d").replace(tzinfo=UTC)
    last = first + timedelta(days=1) if len(label) == 10 else \
        datetime(first.year + first.month // 12, first.month % 12 + 1, 1, tzinfo=UTC)
    offset = 8 * 3600_000
    lines = ["instrument_name,open,high,low,close,vol,vol_ccy,vol_quote,open_time,confirm"]
    for ts in range(int(first.timestamp() * 1000) - offset, int(last.timestamp() * 1000) - offset, 60_000):
        row = _synthetic(ts, 60_000)
        lines.append(",".join([inst, *row[1:5], row[5], row[6], row[7], row[0], "1"]))
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        archive.writestr(name + ".csv", "\n".join(lines) + "\n")
    return packed.getvalue()


def baostock_key(message: str) -> str:
    """A Baostock request without its header (version, type, length) and checksum."""
    return message[21:].rsplit("\x01", 1)[0]


class _NoSocket:
    def settimeout(self, _value):
        pass

    def close(self):
        pass


class BaostockReplay:
    """Answers the Baostock library from recordings at its one boundary (``socketutil.send_msg``)."""

    def __init__(self, monkeypatch, *names: str):
        import baostock.util.socketutil as socketutil

        self.answers: dict[str, str] = {}
        self.open_ended: dict[str, str] = {}
        for name in names:
            for entry in recorded("baostock", name):
                self.answers[entry["request"]] = entry["response"]
                prefix, _sep, last = entry["request"].rpartition("\x01")
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", last):
                    # A query that runs to today was recorded on one day; replayed later it names a later day.
                    self.open_ended[prefix] = entry["response"]
        self.requests: list[str] = []
        self.before = None  # called (in the Baostock thread) with each request before it is answered
        monkeypatch.setattr(network, "open_tcp", lambda host, port, timeout=15.0: _NoSocket())
        monkeypatch.setattr(socketutil, "send_msg", self._send)

    def _send(self, message: str) -> str:
        key = baostock_key(message)
        self.requests.append(key)
        if self.before is not None:
            self.before(key)
        prefix, _sep, last = key.rpartition("\x01")
        if key not in self.answers and re.fullmatch(r"\d{4}-\d{2}-\d{2}", last) and prefix in self.open_ended:
            return self.open_ended[prefix]
        if key not in self.answers:
            raise AssertionError(f"No recorded Baostock answer for {key!r}")
        return self.answers[key]

    def count(self, text: str) -> int:
        return sum(1 for key in self.requests if text in key)

    @property
    def queries(self) -> list[str]:
        """Requests other than login and further pages of an answer."""
        return [key for key in self.requests if "\x01login\x01" not in key and _page(key) == 1]


def _page(key: str) -> int:
    parts = key.split("\x01")
    try:
        return int(parts[2])
    except (IndexError, ValueError):
        return 1
