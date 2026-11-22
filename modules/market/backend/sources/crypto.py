"""Fourteen crypto venues through CCXT: catalogs, REST history and live bars.

Instrument records are built exactly as Trade builds them (same fields, same
hash), so ids agree across modules. REST requests go through the platform's
exchange queue (``rest_fuse``): shared budgets, priorities and persisted cooldowns.
Every day boundary is UTC (OKX's ``1D`` is Hong Kong time; CCXT asks ``1Dutc``).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import numpy as np
from talyx_sdk.i18n import t

from vocabulary.identity import timeframe_nanoseconds

from .. import bars, network
from ..common import DAY_NS, MarketError, now_ns
from . import binance_files, crypto_live, gate_files, okx_files
from .base import History, Source, Target, coverage, empty_history, float_column, row

MS = 1_000_000
# Per venue: request rows for history, history start, requests at once. Request pacing is the platform exchange queue's
# (per endpoint, at the venue's own windows), so Market's gate only bounds concurrency.
VENUES: dict[str, dict[str, Any]] = {
    "binance": {"name": "Binance", "rows": 1000, "depth": "2017-07-14", "concurrency": 6},
    "bitfinex": {"name": "Bitfinex", "rows": 10000, "depth": "2013-04-01", "concurrency": 2},
    "bitget": {"name": "Bitget", "rows": 200, "depth": "2018-01-01", "concurrency": 4},
    "bybit": {"name": "Bybit", "rows": 1000, "depth": "2018-11-01", "concurrency": 4},
    "coinbase": {"name": "Coinbase", "rows": 300, "depth": "2015-07-20", "concurrency": 4},
    "cryptocom": {"name": "Crypto.com", "rows": 300, "depth": "2019-01-01", "concurrency": 4},
    # Deribit's 6h, 12h and daily bars start at 08:00 UTC; HTX's days at 16:00 UTC (checked 2026-11-10).
    "deribit": {"name": "Deribit", "rows": 5000, "depth": "2018-08-14", "concurrency": 4,
                "off_grid": ("6h", "12h", "1d")},
    "gate": {"name": "Gate", "rows": 1000, "bars_back": 10000, "concurrency": 4},
    "htx": {"name": "HTX", "rows": 2000, "depth": "2019-01-01", "concurrency": 4, "off_grid": ("1d",)},
    "hyperliquid": {"name": "Hyperliquid", "rows": 5000, "bars_back": 5000, "concurrency": 2},
    "kraken": {"name": "Kraken", "rows": 720, "bars_back": 720, "concurrency": 2},
    "kucoin": {"name": "KuCoin", "rows": 1500, "depth": "2017-09-01", "concurrency": 4},
    "mexc": {"name": "MEXC", "rows": 1000, "depth": "2019-01-01", "concurrency": 4},
    # OKX pages mark and index bars 100 at a time.
    "okx": {"name": "OKX", "rows": 300, "depth": "2018-01-11", "concurrency": 4,
            "basis_rows": {"mark": 100, "index": 100}},
}
INTRADAY = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h")
FUNDING_VENUES = ("binance", "okx", "bybit", "gate", "bitget", "htx", "kucoin", "mexc", "hyperliquid")
# Open interest per contract (checked 2026-11-14): polled now through CCXT's fetchOpenInterest, and 5-minute history
# through fetchOpenInterestHistory where that is the contract's own (HTX's has no 5-minute period, Bitget, MEXC and
# Hyperliquid have none; Gate and MEXC can't be polled). CCXT's OKX history is summed over every contract of the coin,
# so OKX's comes from its per-contract endpoint. History is (rows per request, how far back); Binance's older days
# come from its files.
OPEN_INTEREST_LIVE = ("binance", "okx", "bybit", "bitget", "htx", "kucoin", "hyperliquid")
OPEN_INTEREST_HISTORY = {"binance": (500, "P30D"), "okx": (100, "P5D"), "bybit": (200, "P30D"),
                         "gate": (100, "P30D"), "kucoin": (200, "P6D")}
OPEN_INTEREST_STEP = 300 * 10**9


def record_of(exchange_id: str, market: dict, categories: dict) -> dict:
    """The vocabulary Instrument of one CCXT market (the same mapping Trade uses)."""
    info = market["info"] if isinstance(market.get("info"), dict) else {}  # some venues give a raw row list
    asset_class, underlying = "crypto", None
    if exchange_id == "binance":
        asset_class = {"EQUITY": "tokenized_equity", "HK_EQUITY": "tokenized_equity", "INDEX": "index",
                       "COMMODITY": "commodity", "FOREX": "fx"}.get(info.get("underlyingType"), "crypto")
    else:
        asset_class = {"stocks": "tokenized_equity", "indices": "index", "metals": "precious_metal",
                       "forex": "fx", "commodities": "commodity"}.get(info.get("contract_type"), "crypto")
        category = categories.get(market["base"])
        listed = category if isinstance(category, list) else [category]
        if market["spot"] and any(value in {"stocks", "xstocks"} for value in listed):
            asset_class = "tokenized_equity"
    if asset_class != "crypto":
        underlying = market["base"]
    orientation = "none" if market["spot"] else "linear" if market["linear"] else "inverse" if market["inverse"] else "unknown"
    size = info["multiplier"] if exchange_id == "gate" and market["option"] else market["contractSize"]
    return {
        "identityVersion": 1, "venueId": exchange_id, "symbol": market["symbol"], "displaySymbol": market["symbol"],
        "base": market["base"], "quote": market["quote"], "settle": market["settle"], "marketType": market["type"],
        "assetClass": asset_class, "contractOrientation": orientation,
        "contractSize": None if size is None else _decimal(size),
        "expiry": None if market["expiry"] is None else str(int(market["expiry"]) * MS),
        "strike": None if market["strike"] is None else _decimal(market["strike"]),
        "optionSide": market["optionType"], "underlyingSymbol": underlying, "retiredAt": None, "calendarId": "24_7",
        "adjustmentEligible": False, "nativeId": market["id"], "active": market["active"] is not False,
    }


QUOTES = ("FDUSD", "USDT", "USDC", "BUSD", "TUSD", "USDP", "USDS", "AEUR", "BIDR", "BVND", "IDRT", "BKRW", "DAI",
          "PAX", "UST", "VAI", "TRY", "EUR", "GBP", "AUD", "BRL", "RUB", "UAH", "NGN", "ZAR", "PLN", "RON", "ARS",
          "JPY", "MXN", "COP", "CZK", "BTC", "ETH", "BNB", "XRP", "TRX", "DOGE", "DOT")


def delisted_record(code: str, fam: str) -> dict | None:
    """The record of a Binance code known only from its files (``LUNAUSDT`` → LUNA/USDT, or the perpetual)."""
    if "_" in code:
        return None  # delivery contracts (BTCUSDT_230331): not listed from files
    quote = next((q for q in QUOTES if code.endswith(q) and len(code) > len(q)), None)
    if quote is None or (fam == "futures/um" and quote not in ("USDT", "USDC", "BUSD")):
        return None
    base = code[: -len(quote)]
    spot = fam == "spot"
    symbol = f"{base}/{quote}" if spot else f"{base}/{quote}:{quote}"
    return {"identityVersion": 1, "venueId": "binance", "symbol": symbol, "displaySymbol": symbol, "base": base,
            "quote": quote, "settle": None if spot else quote, "marketType": "spot" if spot else "swap",
            "assetClass": "crypto", "contractOrientation": "none" if spot else "linear",
            "contractSize": None if spot else "1", "expiry": None, "strike": None, "optionSide": None,
            "underlyingSymbol": None, "retiredAt": None, "calendarId": "24_7", "adjustmentEligible": False,
            "nativeId": code, "active": False}


def _decimal(value) -> str:
    text = np.format_float_positional(float(value), trim="-") if not isinstance(value, str) else value
    return text


class CryptoSource(Source):
    id = "ccxt"
    descriptor = {
        "name": "Crypto exchanges", "sourceIds": sorted(VENUES),
        "kinds": ["bars", "quotes", "trades", "order_book", "funding_rate", "open_interest"],
        "assetClasses": ["crypto", "tokenized_equity", "equity", "index", "fx", "commodity", "precious_metal"],
        "credentials": "none", "paid": False, "supports": ["catalog", "history", "live"],
    }

    # Live endpoints (tests point these at local fake services).
    urls = {"okx:business": crypto_live.OKX_BUSINESS, "okx:public": crypto_live.OKX_PUBLIC,
            "binance:spot": crypto_live.BINANCE_SPOT, "binance:usdm": crypto_live.BINANCE_USDM,
            "binance:coinm": crypto_live.BINANCE_COINM}
    binance_rotate = crypto_live.BINANCE_ROTATE
    heartbeat: float | None = None
    poll_seconds = {"funding_rate": 10.0, "open_interest": 30.0}

    def __init__(self, market):
        super().__init__(market)
        self._clients: dict[tuple[str, str], Any] = {}
        self._pro: dict[tuple[str, str], Any] = {}
        self._loading: dict[tuple[str, str], asyncio.Task] = {}
        self._pools: dict[str, Any] = {}
        # Venues whose own data files Market reads: each declares its history rows and answers from its files.
        self.files = {"binance": binance_files.BinanceFiles(self), "gate": gate_files.GateFiles(self),
                      "okx": okx_files.OkxFiles(self)}

    async def describe(self, source_id: str) -> dict:
        venue = VENUES[source_id]
        scope = {"venues": [source_id]}
        periods = []
        # Only periods the venue gives (CCXT's list) and cuts on the UTC grid; Market aggregates the others.
        offered = self.client(source_id).timeframes
        for timeframe in INTRADAY + ("1d",):
            if timeframe not in offered or timeframe in venue.get("off_grid", ()):
                continue
            step = timeframe_nanoseconds(timeframe)
            if "bars_back" in venue:
                depth = f"PT{venue['bars_back'] * step // 60_000_000_000}M"
            else:
                depth = venue["depth"]
            periods.append((timeframe, depth, venue["rows"],
                            {"basisRows": venue["basis_rows"]} if "basis_rows" in venue else {}))
        files = self.files.get(source_id)
        rows = files.declare(scope, periods) if files is not None else []
        kinds = {item["kind"] for item in rows}
        if "bars" not in kinds:
            rows.append(row(scope, "bars", periods, live="realtime", live_periods=["1m"]))
        rows += [row(scope, "quotes", live="realtime"), row(scope, "trades", live="realtime"),
                 row(scope, "order_book", live="realtime")]
        if source_id in FUNDING_VENUES and "funding_rate" not in kinds:
            rows.append(row({**scope, "marketTypes": ["swap"]}, "funding_rate", [("8h", "P90D", 100)], live="realtime"))
        if (source_id in OPEN_INTEREST_LIVE or source_id in OPEN_INTEREST_HISTORY) and "open_interest" not in kinds:
            history = [("5m", OPEN_INTEREST_HISTORY[source_id][1], OPEN_INTEREST_HISTORY[source_id][0])] \
                if source_id in OPEN_INTEREST_HISTORY else []
            rows.append(row({**scope, "marketTypes": ["swap", "future"]}, "open_interest", history,
                            live="realtime" if source_id in OPEN_INTEREST_LIVE else "none"))
        return coverage(rows, concurrency=venue["concurrency"], rpm=None, per_connection=200)

    # ── clients and catalogs ────────────────────────────────────────────────

    def client(self, venue: str, environment: str = "production"):
        key = (venue, environment)
        client = self._clients.get(key)
        if client is None:
            client = network.ccxt_client(venue, environment=environment)
            self._clients[key] = client
        network.ccxt_configure(client)
        return client

    async def markets(self, venue: str, environment: str = "production") -> dict:
        client = self.client(venue, environment)
        if client.markets:
            return client.markets
        key = (venue, environment)
        task = self._loading.get(key)
        if task is None or task.done():
            task = self._loading[key] = asyncio.create_task(self._load(venue, client, environment))
        await task
        return client.markets

    async def _load(self, venue: str, client, environment: str) -> None:
        if venue != "binance" or environment != "production":
            await self._call(venue, client.load_markets)
            return
        official = {name: client.urls["api"][name] for name in network.BINANCE_FUTURES_CATALOG}
        client.urls["api"].update(network.BINANCE_FUTURES_CATALOG)
        try:
            await self._call(venue, client.load_markets)
        finally:
            client.urls["api"].update(official)

    async def catalog(self, source_id: str, scope: dict, environment: str, cursor: str | None
                      ) -> tuple[list[dict], str | None]:
        markets = await self.markets(source_id, environment)
        categories = {}
        if source_id == "gate" and environment == "production":
            client = self.client(source_id, environment)
            rows = await self._call(source_id, client.publicSpotGetCurrencies)
            categories = {item["currency"]: item.get("category") for item in rows}
        records = [record_of(source_id, market, categories) for market in markets.values()
                   if market["expiry"] is None or market["expiry"] > now_ns() // MS]
        if source_id == "binance" and environment == "production":
            records += await self._binance_delisted(markets)
        return [{"type": "contract", "instrument": record} for record in records], None

    async def _binance_delisted(self, markets: dict) -> list[dict]:
        """Spot pairs and USD-M perpetuals that only Binance's files still have (inactive, searchable with
        includeInactive). A listing failure leaves the catalog as the API gave it."""
        known = {"spot": set(), "futures/um": set()}
        for market in markets.values():
            if market["spot"]:
                known["spot"].add(market["id"])
            elif market["swap"] and market["linear"]:
                known["futures/um"].add(market["id"])
        out = []
        for fam in ("spot", "futures/um"):
            try:
                codes = await self.files["binance"].delisted(fam, known[fam])
            except MarketError:
                continue
            for code in codes:
                record = delisted_record(code, fam)
                if record is not None:
                    out.append(record)
        return out

    # ── history ─────────────────────────────────────────────────────────────

    async def history(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str,
                      start: int, end: int,
                      archive: tuple[str, int] | None = None) -> History:
        files = self.files.get(source_id) if target.environment == "production" else None
        if files is not None:
            return await files.history(target, kind, timeframe, price_basis, start, end, archive)
        return await self._rest_history(source_id, target, kind, timeframe, price_basis, start, end)

    async def _rest_history(self, source_id: str, target: Target, kind: str, timeframe: str | None,
                            price_basis: str, start: int, end: int) -> History:
        if kind == "funding_rate":
            return await self._funding(source_id, target, start, end)
        if kind == "open_interest" and source_id in OPEN_INTEREST_HISTORY:
            return await self._open_interest(source_id, target, start, end)
        if kind != "bars":
            raise MarketError("unsupported", t("{source_id} history has no {kind}", source_id=source_id, kind=kind), source_id=source_id)
        _api_basis(source_id, price_basis)
        client = self.client(source_id, target.environment)
        markets = await self.markets(source_id, target.environment)
        symbol = target.instrument["symbol"]
        step = timeframe_nanoseconds(timeframe)
        venue = VENUES[source_id]
        limit = venue.get("basis_rows", {}).get(price_basis, venue["rows"])
        params = {}
        if price_basis in ("mark", "index"):
            params["price"] = price_basis
            if source_id == "okx" and price_basis == "index":  # CCXT sends instFamily, empty for a spot pair
                params["instId"] = _okx_index_id(markets[symbol])
        stop = min(end, now_ns())
        if start >= stop:
            return empty_history("source_has_none")
        count = min(limit, max(1, -(-(stop - start) // step)))

        async def fetch(at: int) -> list:
            return await self._call(source_id, client.fetch_ohlcv, symbol, _ccxt_timeframe(timeframe), at // MS,
                                    count, params)
        raw = await fetch(start)
        none = []
        if not raw and start + count * step < stop:
            # Nothing in this window: before the listing (a contract listed after the source's depth) or after a
            # delisting. The first window with bars is found by doubling then halving the distance (a few requests
            # where walking window by window took thousands on 1m), assuming no window-long hole before it.
            span, windows = count * step, -(-(stop - start) // (count * step))
            empty, found, probe = 0, None, 1
            while found is None and empty < windows - 1:
                at = min(probe, windows - 1)
                rows = await fetch(start + at * span)
                if rows:
                    found = (at, rows)
                else:
                    empty, probe = at, probe * 2
            if found is None:
                return History(bars.empty(), None, "source_has_none")
            while found[0] - empty > 1:
                middle = (empty + found[0]) // 2
                rows = await fetch(start + middle * span)
                if rows:
                    found = (middle, rows)
                else:
                    empty = middle
            begin = start + found[0] * span
            none, start, raw = [(start, begin)], begin, found[1]
        # CCXT gives OKX derivative volume in base units (volCcy), not contracts.
        columns = _ohlcv_columns(raw, step, source_id, timeframe)
        if price_basis in ("mark", "index") and bars.length(columns):
            columns["volume"] = np.full(bars.length(columns), np.nan)  # CCXT reads the confirm flag as volume
        columns = bars.window(columns, start, stop)
        columns = bars.take(columns, columns["close_time"] <= now_ns())
        last = int(columns["time"][-1]) if bars.length(columns) else None
        reached = last is not None and last + step >= stop
        next_start = None if reached or last is None else last + step
        if last is None and len(raw) == 0:
            return History(columns, None, "source_has_none")  # the last window up to stop holds nothing
        if last is None:
            next_start = None
        return History(columns, next_start, none=none)

    async def _funding(self, source_id: str, target: Target, start: int, end: int) -> History:
        client = self.client(source_id, target.environment)
        await self.markets(source_id, target.environment)
        raw = await self._call(source_id, client.fetch_funding_rate_history, target.instrument["symbol"],
                               start // MS, 100)
        times = np.asarray([int(item["timestamp"]) * MS for item in raw], dtype=np.int64)
        rates = np.asarray([float(item["fundingRate"]) for item in raw], dtype=np.float64)
        intervals = np.diff(times, prepend=times[:1] - 8 * 3600 * 10**9) / (3600 * 10**9) if len(times) else times
        columns = {"time": times, "rate": rates, "intervalHours": np.asarray(intervals, dtype=np.float64)}
        columns = bars.window(bars.sort_unique(columns), start, end)
        last = int(columns["time"][-1]) if bars.length(columns) else None
        return History(columns, None if last is None or len(raw) < 100 else last + 1,
                       "source_has_none" if last is None else None)

    async def _open_interest(self, source_id: str, target: Target, start: int, end: int) -> History:
        """The contract's open interest every 5 minutes (CCXT's fetchOpenInterestHistory). Each request names a window
        of at most one request's rows: a venue answers the newest rows of what it is asked (KuCoin), not the oldest."""
        client = self.client(source_id, target.environment)
        await self.markets(source_id, target.environment)
        rows = OPEN_INTEREST_HISTORY[source_id][0]
        stop = min(end, start + rows * OPEN_INTEREST_STEP)
        symbol = target.instrument["symbol"]
        if source_id == "okx":
            # Contracts (oi) and dollars (oiUsd) of this contract, newest first; begin and end are exclusive.
            answer = await self._call(source_id, client.publicGetRubikStatContractsOpenInterestHistory,
                                      {"instId": client.market(symbol)["id"], "period": "5m",
                                       "begin": str(start // MS - 1), "end": str(stop // MS), "limit": str(rows)})
            raw = [{"timestamp": int(ts), "openInterestAmount": float(oi), "openInterestValue": float(usd)}
                   for ts, oi, _ccy, usd in answer["data"]]
        else:
            raw = await self._call(source_id, client.fetch_open_interest_history, symbol, "5m", start // MS, rows,
                                   {"until": (stop - 1) // MS})
        values = [crypto_live.open_interest_values(item, target.instrument) for item in raw]
        columns = {"time": np.asarray([int(item["timestamp"]) * MS for item in raw], dtype=np.int64),
                   "value": float_column([b for b, _q in values]), "valueQuote": float_column([q for _b, q in values])}
        columns = bars.window(bars.sort_unique(columns), start, stop)
        return History(columns, stop if stop < end else None,
                       None if bars.length(columns) else "source_has_none")

    async def _call(self, venue: str, function, *args):
        import ccxt

        try:
            return await function(*args)
        except ccxt.RateLimitExceeded as exc:
            status = _status(exc)
            if status in (403, 451) and not (venue == "bybit" and "10006" in str(exc)):
                raise network.status_error(venue, status, "") from exc
            retry_at = getattr(exc, "retry_at", None)
            raise MarketError("rate_limited", t("{venue} is rate limiting requests", venue=venue), source_id=venue, status=429,
                              retry_at=int(retry_at * 1e9) if retry_at else now_ns() + 30 * 10**9) from exc
        except ccxt.AuthenticationError as exc:
            raise MarketError("unauthorized", t("{venue} rejected the key", venue=venue), source_id=venue) from exc
        except ccxt.PermissionDenied as exc:
            raise MarketError("forbidden", t("{venue} refused the request", venue=venue), source_id=venue, status=_status(exc)) from exc
        except ccxt.BadSymbol as exc:
            raise MarketError("not_found", t("{venue} has no such symbol", venue=venue), source_id=venue) from exc
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
            status = _status(exc)
            if status in network.REFUSED:
                raise network.status_error(venue, status, "") from exc
            if status is not None and 400 <= status < 500:
                raise MarketError("source_unavailable", t("{venue} answered HTTP {status}", venue=venue, status=status), source_id=venue, status=status) from exc
            url = (client_url := getattr(exc, "url", None)) or f"https://{venue}.invalid/"
            raise await self.egress.transport_failure(venue, client_url or url, exc) from exc
        except ccxt.BaseError as exc:
            status = _status(exc)
            raise MarketError("source_unavailable", (t("{venue} failed the request (HTTP {status})", venue=venue, status=status)
                    if status else t("{venue} failed the request", venue=venue)), source_id=venue,
                              status=status) from exc

    # ── live ────────────────────────────────────────────────────────────────

    def _pool(self, name: str):
        pool = self._pools.get(name)
        if pool is None:
            url = self.urls[name]
            if name.startswith("okx"):
                pool = crypto_live.okx_pool(self.egress, url, {})
            else:
                pool = crypto_live.binance_pool(self.egress, url, self.binance_rotate,
                                                **({"heartbeat": self.heartbeat} if self.heartbeat else {}))
            if self.heartbeat and name.startswith("okx"):
                pool.heartbeat = self.heartbeat
            self._pools[name] = pool
        return pool

    def live(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str = "last",
             depth: int | None = None):
        if kind in ("ticker", "order_book", "trades"):
            return self._observe(source_id, target, kind, depth or 0)
        if kind in ("funding_rate", "open_interest"):
            return self._poll(source_id, target, kind)
        return self._live(source_id, target, kind, timeframe, price_basis)

    async def _observe(self, source_id: str, target: Target, kind: str, depth: int):
        markets = await self.markets(source_id, target.environment)
        symbol = target.instrument["symbol"]
        if symbol not in markets:
            raise MarketError("not_found", t("{source_id} has no {symbol}", source_id=source_id, symbol=symbol), source_id=source_id)
        client = self.pro_client(source_id, target.environment)
        try:
            async for event in crypto_live.ccxt_observations(client, symbol, kind, depth):
                yield event
        except MarketError:
            raise
        except Exception as exc:  # noqa: BLE001 - CCXT errors map to Market codes
            raise await self._mapped(source_id, exc) from exc

    async def _poll(self, source_id: str, target: Target, kind: str):
        """Funding and open interest: few venues push them, so the REST value is polled."""
        client = self.client(source_id, target.environment)
        await self.markets(source_id, target.environment)
        symbol = target.instrument["symbol"]
        method = client.fetch_funding_rate if kind == "funding_rate" else client.fetch_open_interest
        instrument = target.instrument
        convert = crypto_live.funding_row if kind == "funding_rate" else \
            (lambda value: crypto_live.open_interest_row(value, instrument))
        interval = self.poll_seconds[kind]
        announced = False
        while True:
            value = await self._call(source_id, method, symbol)
            if not announced:
                announced = True
                yield {"type": "subscribed", "mode": "live"}
            yield {"type": "observation", "row": convert(value)}
            await asyncio.sleep(interval)

    async def option_chain(self, source_id: str, environment: str, underlying: str, expiry: int | None
                           ) -> tuple[dict, dict, int]:
        """Gate's options contracts (quotes, mark, open interest) and tickers (IV, greeks) for an underlying."""
        if source_id != "gate":
            raise MarketError("unsupported", t("{source_id} has no option chain in Market", source_id=source_id), source_id=source_id)
        client = self.client(source_id, environment)
        await self.markets(source_id, environment)
        query = {"underlying": f"{underlying}_USDT"}
        if expiry is not None:
            query["expiration"] = expiry // 10**9
        contracts, tickers = await asyncio.gather(
            self._call(source_id, client.publicOptionsGetContracts, query),
            self._call(source_id, client.publicOptionsGetTickers, {"underlying": query["underlying"]}))
        return {row["name"]: row for row in contracts}, {row["name"]: row for row in tickers}, now_ns()

    async def recent_trades(self, source_id: str, target: Target, limit: int) -> list[dict]:
        client = self.client(source_id, target.environment)
        await self.markets(source_id, target.environment)
        trades = await self._call(source_id, client.fetch_trades, target.instrument["symbol"], None, limit)
        return [crypto_live.trade_row(item) for item in trades]

    async def _live(self, source_id: str, target: Target, kind: str, timeframe: str | None, price_basis: str):
        markets = await self.markets(source_id, target.environment)
        symbol = target.instrument["symbol"]
        market = markets.get(symbol)
        if market is None:
            raise MarketError("not_found", t("{source_id} has no {symbol}", source_id=source_id, symbol=symbol), source_id=source_id)
        native = market["id"]
        if kind not in ("bars", "quotes"):
            raise MarketError("unsupported", t("{source_id} live {kind} is not available", source_id=source_id, kind=kind), source_id=source_id)
        if kind == "bars":
            _api_basis(source_id, price_basis)
        if source_id == "okx" and target.environment == "production":
            if kind == "bars":
                pool = self._pool("okx:business")
                inst = _okx_index_id(market) if price_basis == "index" else native
                key = f"{crypto_live.okx_channel(timeframe, price_basis)}:{inst}"
            else:
                pool = self._pool("okx:public")
                key = f"tickers:{native}"
            _register_contract(pool, native, not market["spot"])
            async for event in crypto_live.stream.events(pool, key):
                yield event
            return
        if source_id == "binance" and target.environment == "production" and price_basis == "last":
            name = "binance:spot" if market["spot"] else "binance:usdm" if market["linear"] else "binance:coinm"
            pool = self._pool(name)
            key = f"{native.lower()}@kline_{timeframe}" if kind == "bars" else f"{native.lower()}@ticker"
            async for event in crypto_live.stream.events(pool, key):
                yield event
            return
        client = self.pro_client(source_id, target.environment)
        if kind == "bars":
            params = {"price": price_basis} if price_basis != "last" else {}
            iterator = crypto_live.ccxt_bars(client, symbol, timeframe, params)
        else:
            iterator = crypto_live.ccxt_ticker(client, symbol)
        try:
            async for event in iterator:
                yield event
        except MarketError:
            raise
        except Exception as exc:  # noqa: BLE001 - CCXT errors map to Market codes
            raise await self._mapped(source_id, exc) from exc

    def pro_client(self, venue: str, environment: str = "production"):
        key = (venue, environment)
        client = self._pro.get(key)
        if client is None:
            client = network.ccxt_client(venue, environment=environment, pro=True)
            self._pro[key] = client
        network.ccxt_configure(client)
        return client

    async def _mapped(self, venue: str, exc: BaseException) -> MarketError:
        async def fail():
            raise exc
        try:
            await self._call(venue, fail)
        except MarketError as mapped:
            return mapped
        return MarketError("source_unavailable", f"{venue}: {exc}"[:500], source_id=venue)

    # ── trading terms ───────────────────────────────────────────────────────

    async def terms(self, source_id: str, target: Target) -> dict:
        """Fees, size rules, quantity unit, multiplier and tick of one contract, from the venue's market list."""
        markets = await self.markets(source_id, target.environment)
        instrument = target.instrument
        found = markets.get(instrument["symbol"])
        item: dict[str, Any] = {"fees": None, "minQuantity": None, "quantityIncrement": None, "quantityUnit": None,
                                "contractMultiplier": None, "priceIncrement": None, "unavailable": {}}
        unavailable = item["unavailable"]
        table = VENUE_FEES.get((source_id, instrument["marketType"]))
        if found is None:
            reason = f"{instrument['symbol']} is not in the {source_id} market list"
            for name in ("minQuantity", "quantityIncrement", "quantityUnit", "contractMultiplier", "priceIncrement"):
                unavailable[name] = reason
            _fees(item, None, table, reason)
            return item
        client = self.client(source_id, target.environment)
        info = found["info"] if isinstance(found.get("info"), dict) else {}
        _fees(item, found, table, f"{source_id} lists no fee and the venue table has none for "
                                  f"{instrument['marketType']}")
        amount = (found.get("limits") or {}).get("amount") or {}
        item["minQuantity"] = _positive(amount.get("min"))
        if item["minQuantity"] is None:
            unavailable["minQuantity"] = "the venue reports no minimum amount"
        item["quantityIncrement"], reason = _increment((found.get("precision") or {}).get("amount"),
                                                       client.precisionMode)
        if reason:
            unavailable["quantityIncrement"] = reason
        size = _positive(info.get("multiplier") if source_id == "gate" and found.get("option")
                         else found.get("contractSize"))
        if not found.get("contract"):
            item.update(quantityUnit="base", contractMultiplier="1")
        elif size is None:
            unavailable["contractMultiplier"] = "the venue reports no contract size"
            unavailable["quantityUnit"] = "the venue reports no contract size, so contracts versus base is unknown"
        else:
            # An amount is in the base asset only for a linear contract worth exactly one unit.
            item.update(contractMultiplier=size,
                        quantityUnit="base" if found.get("linear") is True and size == "1" else "contracts")
        tick = next((value for value in (_positive(info.get(name)) for name in _TICK_FIELDS.get(source_id, ()))
                     if value is not None), None)
        item["priceIncrement"], reason = (tick, None) if tick is not None else \
            _increment((found.get("precision") or {}).get("price"), client.precisionMode)
        if reason:
            unavailable["priceIncrement"] = reason
        return item

    # ── quotes ──────────────────────────────────────────────────────────────

    async def quotes(self, source_id: str, targets: list[Target], basis: str) -> dict[str, dict]:
        environment = targets[0].environment if targets else "production"
        client = self.client(source_id, environment)
        markets = await self.markets(source_id, environment)
        out: dict[str, dict] = {}
        symbols = {}
        for target in targets:
            symbol = target.instrument["symbol"]
            if symbol not in markets:
                out[target.instrument["instrumentId"]] = {"problem": MarketError(
                    "not_found", t("{source_id} has no {symbol}", source_id=source_id, symbol=symbol), source_id=source_id).problem()}
            else:
                symbols[symbol] = target.instrument["instrumentId"]
        if not symbols:
            return out
        tickers: dict[str, dict] = {}
        try:
            if client.has.get("fetchTickers") and len(symbols) > 1:
                tickers = await self._call(source_id, client.fetch_tickers, list(symbols))
            else:
                for symbol in symbols:
                    tickers[symbol] = await self._call(source_id, client.fetch_ticker, symbol)
        except MarketError as exc:
            if len(symbols) == 1:
                for instrument_id in symbols.values():
                    out[instrument_id] = {"problem": exc.problem()}
                return out
            # One bad symbol fails a batch on some venues: ask one by one.
            for symbol, instrument_id in symbols.items():
                try:
                    tickers[symbol] = await self._call(source_id, client.fetch_ticker, symbol)
                except MarketError as single:
                    out[instrument_id] = {"problem": single.problem()}
        for symbol, instrument_id in symbols.items():
            if instrument_id in out:
                continue
            ticker = tickers.get(symbol)
            out[instrument_id] = {"quote": crypto_live.quote_of_ticker(ticker)} if ticker else {
                "problem": MarketError("no_data", t("{source_id} gave no ticker for {symbol}", source_id=source_id, symbol=symbol),
                                       source_id=source_id).problem()}
        return out

    async def close(self) -> None:
        for pool in self._pools.values():
            await pool.close()
        self._pools.clear()
        for client in list(self._clients.values()) + list(self._pro.values()):
            await client.close()
        self._clients.clear()
        self._pro.clear()


_STATUS = re.compile(r"\b(?:GET|POST|PUT|DELETE)\s+\S+\s+([1-5]\d\d)\b|^\S+\s+([1-5]\d\d)\b")


def _status(exc) -> int | None:
    if exc.args and (status := network.handshake_status(exc.args[0])) is not None:  # CCXT Pro wraps it
        return status
    match = _STATUS.search(str(exc))
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _api_basis(source_id: str, price_basis: str) -> None:
    """Venue APIs give last, mark and index bars; premium index bars are only in Binance's files."""
    if price_basis not in ("last", "mark", "index"):
        raise MarketError("unsupported", (t("{source_id} publishes no {price_basis} bars through its API (premium index bars come only from Binance's files)",
                    source_id=source_id, price_basis=price_basis)
                if price_basis == 'premium' else t("{source_id} publishes no {price_basis} bars through its API", source_id=source_id, price_basis=price_basis)),
                          source_id=source_id)


def _ccxt_timeframe(timeframe: str) -> str:
    return timeframe


def _ohlcv_columns(raw: list, step: int, venue: str, timeframe: str) -> bars.Columns:
    if not raw:
        return bars.empty()
    array = np.asarray([[float("nan") if v is None else v for v in item[:6]] for item in raw], dtype=np.float64)
    times = array[:, 0].astype(np.int64) * MS
    grid = min(step, DAY_NS)
    if DAY_NS % grid == 0 and np.any(times % grid):
        raise MarketError("source_unavailable", t("{venue} {timeframe} bars are not cut on the UTC grid", venue=venue, timeframe=timeframe), source_id=venue)
    columns = {"time": times, "close_time": times + step, "open": array[:, 1], "high": array[:, 2],
               "low": array[:, 3], "close": array[:, 4], "volume": array[:, 5]}
    return bars.sort_unique(columns)


def _okx_index_id(market: dict) -> str:
    """OKX keys index candles by the index (BTC-USDT), not by a contract (BTC-USDT-SWAP): a
    contract's instFamily, or the spot pair itself."""
    return market["info"].get("instFamily") or market["id"]


def _register_contract(pool, native: str, contract: bool) -> None:
    """OKX gives contract volume in volCcy: remember which instIds are derivatives."""
    pool.contracts[native] = contract


# ── trading terms ──────────────────────────────────────────────────────────

# (maker, taker) for venues whose market list carries no fee: the base tier of the venue's own
# published schedule (regular user, no VIP level, no token discount), read on official pages
# 2026-10-31. Venues whose rate depends on the user's region are left out (fee unknown).
VENUE_FEES = {
    # Binance spot "Regular User": https://www.binance.com/en/fee/schedule (0.100% / 0.100%).
    ("binance", "spot"): ("0.001", "0.001"),
    # OKX perpetual swaps, regular user, global fee framework from 2025-11-25:
    # https://www.okx.com/en-us/help/updates-to-global-fee-framework (0.0200% / 0.0500%).
    ("okx", "swap"): ("0.0002", "0.0005"),
    # Bybit non-VIP spot 0.1% / 0.1% and USDT perpetual 0.02% / 0.055% (Bybit Help Center fee pages).
    ("bybit", "spot"): ("0.001", "0.001"),
    ("bybit", "swap"): ("0.0002", "0.00055"),
}
# The venue's own tick field in the raw market row, preferred over CCXT's derived precision.
_TICK_FIELDS = {"okx": ("tickSz",), "gate": ("order_price_round",), "binance": ("tickSize",)}


def _positive(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number) or number <= 0:
        return None
    return value if isinstance(value, str) else _decimal(number)


def _finite(value) -> str | None:
    """A fee as a decimal; a maker rebate is negative."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(number) else value if isinstance(value, str) else _decimal(number)


def _increment(value, mode) -> tuple[str | None, str | None]:
    """CCXT gives precision as a step (TICK_SIZE), a count of decimals or of significant digits, by venue."""
    import ccxt
    number = _positive(value)
    if number is None:
        return None, "the venue reports no precision"
    if mode == ccxt.TICK_SIZE:
        return number, None
    if mode == ccxt.DECIMAL_PLACES:
        return _decimal(10.0 ** -int(float(number))), None
    return None, "the venue reports precision as significant digits, which is no step"


def _fees(item: dict, market: dict | None, table, reason: str) -> None:
    maker, taker = (_finite(market.get("maker")), _finite(market.get("taker"))) if market else (None, None)
    if maker is not None and taker is not None:
        item["fees"] = {"maker": maker, "taker": taker, "basis": "venue_metadata"}
    elif table is not None:
        item["fees"] = {"maker": table[0], "taker": table[1], "basis": "market_fee_table"}
    else:
        item["unavailable"]["fees"] = reason
