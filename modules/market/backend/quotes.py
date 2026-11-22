"""Current quotes: the ``quotes`` stream and the ``quotes.current`` service.

One **feed** per (instrument, environment) keeps the latest quote from the best
ranked source's live quotes (pushed by exchanges, polled for Yahoo); feeds are
shared by every subscriber and outlive the last one by a short grace, so a
watchlist that resubscribes with a changed set does not reconnect the rest. A
subscriber gets every instrument first, then only changed ones, at most four
items a second. One instrument failing carries ``problem``; the others go on.
"""

from __future__ import annotations

import asyncio
import contextlib

from talyx_sdk.i18n import t

from .common import MarketError, now_ns
from .live import BACKOFF, FATAL
from .routing import in_worker

MAX_RATE = 4.0
GRACE = 10.0
FRESH_NS = 15 * 10**9


class Feed:
    def __init__(self, hub: Quotes, key, instrument: dict, environment: str):
        self.hub, self.key, self.instrument, self.environment = hub, key, instrument, environment
        self.quote: dict | None = None
        self.problem: dict | None = None
        self.updated = 0
        self.listeners: set[QuoteConsumer] = set()
        self.task: asyncio.Task | None = None
        self.closer: asyncio.Task | None = None
        self.started = 0

    def attach(self, consumer: QuoteConsumer) -> None:
        if self.closer:
            self.closer.cancel()
            self.closer = None
        self.listeners.add(consumer)
        if self.task is None or self.task.done():
            self.started += 1
            self.task = asyncio.ensure_future(self.run())

    def detach(self, consumer: QuoteConsumer) -> None:
        self.listeners.discard(consumer)
        if not self.listeners and self.closer is None:
            self.closer = asyncio.ensure_future(self._close_later())

    async def _close_later(self) -> None:
        await asyncio.sleep(self.hub.grace)
        if not self.listeners:
            await self.stop()
            self.hub.feeds.pop(self.key, None)

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task

    def set(self, quote: dict | None, problem: dict | None) -> None:
        self.quote, self.problem, self.updated = quote, problem, now_ns()
        for consumer in tuple(self.listeners):
            consumer.changed(self.instrument["instrumentId"])

    async def run(self) -> None:
        market = self.hub.market
        ranked = market.registry.rank(self.instrument, "quotes")
        if not ranked:
            self.set(None, MarketError("unsupported", t("No source quotes {symbol}", symbol=self.instrument['symbol'])).problem())
            return
        source_id, row = ranked[0]
        entry = market.registry.entry(source_id)
        target = await in_worker(market.data.target, self.instrument, self.environment, source_id)
        attempt = 0
        while True:
            try:
                if row.get("live", "none") != "none" and "live" in entry.descriptor["supports"]:
                    async for event in entry.source.live(source_id, target, "quotes", None):
                        if event["type"] == "ticker":
                            attempt = 0
                            self.set(event["quote"], None)
                        elif event["type"] == "gap" and event.get("problem"):
                            self.problem = event["problem"]
                    raise MarketError("source_unavailable", t("{source_id} ended the quote stream", source_id=source_id),
                                      source_id=source_id)
                result = await market.registry.call(source_id, "quotes", [target], "rolling_24h")
                item = result.get(self.instrument["instrumentId"], {})
                self.set(item.get("quote"), item.get("problem"))
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                raise
            except MarketError as exc:
                self.set(self.quote if exc.code not in FATAL else None, exc.problem())
                if exc.code in FATAL:
                    return
                delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                if exc.retry_at:
                    delay = max(delay, (exc.retry_at - now_ns()) / 1e9)
                attempt += 1
                await asyncio.sleep(delay)


class QuoteConsumer:
    def __init__(self, hub: Quotes):
        self.hub = hub
        self.feeds: dict[str, Feed] = {}
        self.problems: dict[str, dict] = {}
        self.pending: set[str] = set()
        self.event = asyncio.Event()

    def changed(self, instrument_id: str) -> None:
        self.pending.add(instrument_id)
        self.event.set()

    def item(self, instrument_id: str) -> dict:
        feed = self.feeds.get(instrument_id)
        if feed is None:
            return {"instrumentId": instrument_id, "quote": None, "problem": self.problems.get(instrument_id)}
        return {"instrumentId": instrument_id, "quote": feed.quote, "problem": feed.problem if feed.quote is None
                or feed.problem else None}


class Quotes:
    def __init__(self, market):
        self.market = market
        self.feeds: dict[tuple, Feed] = {}
        self.grace = GRACE
        self.min_interval = 1.0 / MAX_RATE
        self._inflight: dict[tuple, asyncio.Future] = {}

    def feed(self, instrument: dict, environment: str) -> Feed:
        key = (instrument["instrumentId"], environment)
        feed = self.feeds.get(key)
        if feed is None:
            feed = self.feeds[key] = Feed(self, key, instrument, environment)
        return feed

    def open(self, params: dict):
        return self._items(params)

    async def _items(self, params: dict):
        environment = params.get("environment", "production")
        consumer = QuoteConsumer(self)
        order = []
        for value, (instrument, problem) in zip(params["instruments"],
                                                await in_worker(self.records, params["instruments"]), strict=True):
            if problem is not None:
                consumer.problems[value["instrumentId"]] = problem
                order.append(value["instrumentId"])
                continue
            feed = self.feed(instrument, environment)
            consumer.feeds[instrument["instrumentId"]] = feed
            order.append(instrument["instrumentId"])
        try:
            missing = [feed.instrument for feed in consumer.feeds.values() if feed.quote is None]
            if missing:
                current = await self.current({"instruments": missing, "environment": environment})
                for item in current["items"]:
                    feed = consumer.feeds.get(item["instrumentId"])
                    if feed is not None and feed.quote is None:
                        feed.quote, feed.problem = item["quote"], item["problem"]
            for feed in consumer.feeds.values():
                feed.attach(consumer)
            consumer.pending.clear()
            yield {"type": "quotes", "items": [consumer.item(i) for i in order]}
            last = asyncio.get_running_loop().time()
            while True:
                while not consumer.pending:
                    consumer.event.clear()
                    await consumer.event.wait()
                wait = self.min_interval - (asyncio.get_running_loop().time() - last)
                if wait > 0:
                    await asyncio.sleep(wait)
                changed, consumer.pending = consumer.pending, set()
                last = asyncio.get_running_loop().time()
                yield {"type": "quotes", "items": [consumer.item(i) for i in order if i in changed]}
        finally:
            for feed in consumer.feeds.values():
                feed.detach(consumer)

    def records(self, values: list[dict]) -> list[tuple[dict | None, dict | None]]:
        """(local record, problem) of each instrument handed in: identity reads, for a worker."""
        out = []
        for value in values:
            try:
                out.append((self.market.instrument_record(value), None))
            except MarketError as exc:
                out.append((None, exc.problem()))
        return out

    async def current(self, value: dict) -> dict:
        environment = value.get("environment", "production")
        basis = value.get("basis", "rolling_24h")
        items: dict[str, dict] = {}
        order: list[str] = []
        groups: dict[str, list] = {}
        now = now_ns()
        for record, (instrument, problem) in zip(value["instruments"],
                                                 await in_worker(self.records, value["instruments"]), strict=True):
            order.append(record["instrumentId"])
            if problem is not None:
                items[record["instrumentId"]] = {"quote": None, "problem": problem}
                continue
            feed = self.feeds.get((instrument["instrumentId"], environment))
            if feed is not None and feed.quote is not None and now - feed.updated < FRESH_NS:
                items[record["instrumentId"]] = {"quote": feed.quote, "problem": None}
                continue
            ranked = self.market.registry.rank(instrument, "quotes")
            if not ranked:
                items[record["instrumentId"]] = {"quote": None, "problem": MarketError(
                    "unsupported", t("No source quotes {symbol}", symbol=instrument['symbol'])).problem()}
                continue
            groups.setdefault(ranked[0][0], []).append((record["instrumentId"], instrument))

        async def ask(source_id: str, members: list) -> None:
            targets = await in_worker(lambda: [self.market.data.target(instrument, environment, source_id)
                                               for _id, instrument in members])
            key = (source_id, environment, basis, tuple(sorted(i for i, _ in members)))
            future = self._inflight.get(key)
            if future is None:
                future = asyncio.ensure_future(self.market.registry.call(source_id, "quotes", targets, basis))
                self._inflight[key] = future
                future.add_done_callback(lambda _f, k=key: self._inflight.pop(k, None))
            try:
                result = await asyncio.shield(future)
            except MarketError as exc:
                for requested, _instrument in members:
                    items[requested] = {"quote": None, "problem": exc.problem()}
                return
            for requested, instrument in members:
                entry = result.get(instrument["instrumentId"], {})
                items[requested] = {"quote": entry.get("quote"), "problem": entry.get("problem")}

        await asyncio.gather(*(ask(source_id, members) for source_id, members in groups.items()))
        return {"receivedAt": str(now_ns()),
                "items": [{"instrumentId": i, **items.get(i, {"quote": None, "problem": None})} for i in order]}

    async def option_chain(self, value: dict) -> dict:
        """One venue's option chain (Gate): contracts from the venue's directory, quotes, IV and greeks from
        one shared upstream read per (environment, underlying, expiry)."""
        venue, environment, underlying = value["venueId"], value["environment"], value["underlying"]
        entry = self.market.registry.entries.get(venue)
        chain = getattr(entry.source, "option_chain", None) if entry else None
        if chain is None:
            raise MarketError("unsupported", t("{venue} has no option chain in Market", venue=venue))
        if environment != "production":
            records = await self.market.instruments.environment_catalog(venue, environment)
        else:
            records = await in_worker(self.market.identity.catalog_page, venue, kinds=("contract",),
                                      asset_classes=None, market_types=["option"], include_inactive=False,
                                      after=None, limit=100_000)
        members = [r for r in records if r["marketType"] == "option" and r.get("base") == underlying]
        expiries = sorted({r["expiry"] for r in members if r.get("expiry")}, key=int)
        expiry = value.get("expiry")
        if expiry is not None:
            members = [r for r in members if r.get("expiry") == str(expiry)]
        members.sort(key=lambda r: (int(r["expiry"] or 0), float(r["strike"] or 0), r.get("optionSide") or ""))
        if len(members) > MAX_CHAIN:
            raise MarketError("too_large", t("The {underlying} chain has {members} contracts; choose an expiry", underlying=underlying, members=len(members)))
        offset = int(value.get("cursor") or 0)
        page = members[offset:offset + value["limit"]]
        key = (venue, environment, underlying, expiry)
        future = self._inflight.get(key)
        if future is None:
            future = asyncio.ensure_future(chain(venue, environment, underlying, None if expiry is None else int(expiry)))
            self._inflight[key] = future
            future.add_done_callback(lambda _f, k=key: self._inflight.pop(k, None))
        contracts, tickers, observed = await asyncio.shield(future)
        rows, price = [], None
        for record in page:
            contract = contracts.get(record["nativeId"]) or {}
            ticker = tickers.get(record["nativeId"]) or {}
            price = _decimal(contract.get("underlying_price")) or price
            # Gate gives an empty side of the book as price 0: no bid or ask, not one at zero.
            bid, ask = (None if _zero(contract.get(name)) else _decimal(contract.get(name))
                        for name in ("bid1_price", "ask1_price"))
            greeks = {name: _decimal(ticker.get(name)) for name in ("delta", "gamma", "theta", "vega")}
            mark, iv = _decimal(contract.get("mark_price")), _decimal(ticker.get("mark_iv"))
            interest = _decimal(contract.get("position_size"))
            stamp = str(observed)
            rows.append({"instrument": record, "bid": bid, "ask": ask, "mark": mark, "impliedVolatility": iv, **greeks,
                         "openInterest": interest,
                         "asOf": {"quote": stamp if bid is not None or ask is not None else None,
                                  "mark": stamp if mark is not None else None, "iv": stamp if iv is not None else None,
                                  "greeks": stamp if any(v is not None for v in greeks.values()) else None,
                                  "openInterest": stamp if interest is not None else None}})
        return {"rows": rows, "expiries": expiries, "underlyingPrice": price,
                "nextCursor": str(offset + value["limit"]) if offset + value["limit"] < len(members) else None,
                "environment": environment, "asOf": str(observed)}

    async def close(self) -> None:
        for feed in list(self.feeds.values()):
            await feed.stop()
        self.feeds.clear()


MAX_CHAIN = 2000


def _decimal(value) -> str | None:
    if value is None or value == "":
        return None
    from decimal import Decimal, InvalidOperation
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    text = format(number.normalize(), "f")
    return text if text != "-0" else "0"


def _zero(value) -> bool:
    text = _decimal(value)
    return text is None or text == "0"
