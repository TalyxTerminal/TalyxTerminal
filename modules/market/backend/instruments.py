"""instruments.search / resolve / get / catalog, and loading directories into the identity table."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from weakref import WeakValueDictionary

from vocabulary.instruments import MarketInstrument

from .codes import MARKET_CALENDARS, parse_typed
from .common import MarketError, now_ns
from .display import place_name, presented
from .routing import in_worker

DISCOVERY_MEMORY = 600.0
CATALOG_REFRESH_NS = 24 * 3600 * 10**9


def _cursor(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode()


def _uncursor(text: str | None) -> dict:
    if not text:
        return {}
    try:
        return json.loads(base64.urlsafe_b64decode(text.encode()))
    except ValueError as exc:
        raise MarketError("invalid_input", "Unknown cursor") from exc


class Instruments:
    def __init__(self, market):
        self.market = market
        self.identity = market.identity
        self._loads: dict[tuple[str, str, str], asyncio.Task] = {}
        self._first_pages: dict[tuple[str, str, str], asyncio.Event] = {}
        self._environments: dict[tuple[str, str], list[dict]] = {}
        self._discoveries: dict[tuple, tuple[float, list[str]]] = {}
        # Callers share a lock only while looking up the same text, scope and set of enabled providers.
        self._discovery_locks: WeakValueDictionary[tuple, asyncio.Lock] = WeakValueDictionary()

    # ── directories ─────────────────────────────────────────────────────────

    def directories(self) -> list[tuple[str, str, str]]:
        """(source id, venue or market, family) of every directory a source can list."""
        found = []
        for source_id, entry in self.market.registry.entries.items():
            if "catalog" not in entry.descriptor["supports"] or not self.market.registry.enabled(source_id):
                continue
            if entry.coverage is None:
                continue
            for item in entry.coverage["rows"]:
                scope = item["scope"]
                for venue in scope.get("venues", []):
                    found.append((source_id, venue, "contracts"))
                if item["kind"] == "listings":
                    for market in scope.get("markets", []):
                        found.append((source_id, market, "listings"))
        return sorted(set(found))

    def start_loading(self, *, force: bool = False) -> None:
        states = {(c["venue"], c["family"]): c for c in self.identity.catalogs()}
        for source_id, venue, family in self.directories():
            state = states.get((venue, family))
            fresh = state and state["state"] == "ready" and state["loadedAt"] and \
                now_ns() - state["loadedAt"] < CATALOG_REFRESH_NS
            if fresh and not force:
                continue
            self.load(source_id, venue, family)

    def load(self, source_id: str, venue: str, family: str) -> asyncio.Task:
        key = (source_id, venue, family)
        task = self._loads.get(key)
        if task is None or task.done():
            self._first_pages[key] = asyncio.Event()
            task = self._loads[key] = asyncio.create_task(self._load(source_id, venue, family))
            self.market.track_task(task)
        return task

    async def _load(self, source_id: str, venue: str, family: str) -> None:
        await in_worker(self.identity.catalog_state, venue, family, "loading")
        try:
            scope = {"venues": [venue]} if family == "contracts" else {"markets": [venue]}
            cursor = None
            while True:
                candidates, cursor = await self.market.registry.call(source_id, "catalog", scope, "production", cursor)
                await in_worker(self.ingest, candidates, source_id)
                self._first_pages[(source_id, venue, family)].set()
                if cursor is None:
                    break
            await in_worker(self.identity.catalog_state, venue, family, "ready", revision=str(now_ns()))
            await self.market.after_catalog(source_id, venue, family)
        except MarketError as exc:
            await in_worker(self.identity.catalog_state, venue, family, "failed", exc.problem())
        finally:
            self._first_pages[(source_id, venue, family)].set()

    def ingest(self, candidates: list[dict], source_id: str, *, names: str = "replace") -> list[str]:
        """Store a directory page (its names replace older ones) or search answers (``names='fill'``)."""
        listings: dict[str, list[dict]] = {}
        contracts: dict[str, list[dict]] = {}
        for candidate in candidates:
            if candidate["type"] == "listing":
                listings.setdefault(candidate["market"], []).append(candidate)
            elif candidate["type"] == "contract":
                contracts.setdefault(candidate["instrument"]["venueId"], []).append(candidate["instrument"])
        ids = []
        for market, rows in listings.items():
            ids += self.identity.upsert_listings(market, rows, source=source_id, naming=names)
        for venue, rows in contracts.items():
            ids += self.identity.upsert_contracts(venue, rows)
        return ids

    async def environment_catalog(self, venue: str, environment: str) -> list[dict]:
        key = (venue, environment)
        if key not in self._environments:
            ranked = [sid for sid, entry in self.market.registry.entries.items()
                      if sid == venue and "catalog" in entry.descriptor["supports"]]
            if not ranked:
                raise MarketError("not_found", f"No {environment} directory for {venue}")
            candidates, _cursor_value = await self.market.registry.call(venue, "catalog", {"venues": [venue]},
                                                                        environment, None)
            # Shown like every other record (BTCUSDT.P, OKX), so OKX:BTCUSDT.P resolves in a test environment too.
            self._environments[key] = [presented(MarketInstrument.from_wire({**c["instrument"],
                                                                             "instrumentId": None}).wire_dict())
                                       for c in candidates if c["type"] == "contract"]
        return self._environments[key]

    # ── search and resolve ──────────────────────────────────────────────────

    async def search(self, value: dict, *, exact_code: bool = False) -> dict:
        query = value["query"].strip()
        limit = value.get("limit", 50)
        offset = _uncursor(value.get("cursor")).get("offset", 0)
        environment = value.get("environment", "production")
        constraint = value.get("constraint")
        if constraint:
            environment = constraint["environment"]
        if environment != "production" or constraint:
            return await self._environment_search(value, query, limit, offset, environment, constraint)
        venue, _, rest = query.partition(":")
        if rest and venue.lower() in self._venues():
            # EXCHANGE:SYMBOL, as TradingView's search box takes it (OKX:BTCUSDT.P): only that exchange.
            if value.get("venues") and venue.lower() not in value["venues"]:
                return {"results": [], "venues": self.venue_states(value), "nextCursor": None}
            query, value = rest.strip(), {**value, "venues": [venue.lower()]}
        kwargs = dict(markets=value.get("markets"), venues=value.get("venues"),
                      asset_classes=value.get("assetClasses"), market_types=value.get("marketTypes"),
                      include_inactive=value.get("includeInactive", False))
        sources = tuple(sid for sid, entry in self.market.registry.entries.items()
                        if "search" in entry.descriptor["supports"] and self.market.registry.usable(entry))
        key = (query.casefold(), tuple(sorted(value.get("markets") or ())), sources)
        expires, hits = self._discoveries.get(key, (0, []))
        cached = expires > time.monotonic()
        kwargs.update(limit=limit + 1, offset=offset)
        results = await in_worker(self.identity.search, query, discovered=hits if cached else (), **kwargs)
        # A local code prefix is enough while typing. An ETF whose name mentions an index is not a match
        # for its full name. A resolver needs the exact code even when a longer local code starts with it.
        if exact_code or query.startswith("^"):
            local_match = results and results[0]["rank"] == 0
        else:
            local_match = results and (results[0]["rank"] <= 1.5 or len(query) < 3)
        if sources and query and not cached and not offset and not value.get("venues") and not local_match:
            lock = self._discovery_locks.setdefault(key, asyncio.Lock())
            try:
                async with lock:
                    expires, hits = self._discoveries.get(key, (0, []))
                    if expires <= time.monotonic():
                        hits = await self.discover(query, value.get("markets"), sources)
                        self._discoveries[key] = (time.monotonic() + DISCOVERY_MEMORY, hits)
            except MarketError:
                if exact_code or not results:
                    raise
            else:
                results = await in_worker(self.identity.search, query, discovered=hits, **kwargs)
        more = len(results) > limit
        results = results[:limit]
        return {"results": [self.search_result(r) for r in results], "venues": self.venue_states(value),
                "nextCursor": _cursor({"offset": offset + limit}) if more else None}

    def _venues(self) -> set[str]:
        """Exchanges with a directory (the venue names ``resolve`` takes before a colon)."""
        return {sid for sid, entry in self.market.registry.entries.items() if "catalog" in entry.descriptor["supports"]}

    async def _environment_search(self, value, query, limit, offset, environment, constraint) -> dict:
        venue = constraint["venueId"] if constraint else (value.get("venues") or [None])[0]
        if venue is None:
            raise MarketError("invalid_input", "Searching a test environment needs one venue")
        records = await self.environment_catalog(venue, environment) if environment != "production" else \
            [r["instrument"] for r in self.identity.search(query, venues=[venue], limit=500)]
        text = query.upper().replace("-", "/")
        products = (constraint or {}).get("products") or value.get("marketTypes")
        allowed = set((constraint or {}).get("instrumentIds") or [])
        matches = []
        for record in records:
            if products and record["marketType"] not in products:
                continue
            if allowed and record["instrumentId"] not in allowed:
                continue
            symbol = record["symbol"].upper()
            if text in symbol or text in (record.get("base") or ""):
                rank = 0 if symbol == text else 1 if symbol.startswith(text) else 2
                matches.append((rank, len(symbol), record))
        matches.sort(key=lambda item: item[:2])
        page = matches[offset:offset + limit + 1]
        return {"results": [{"instrument": r, "matched": "contract", "formerCode": None,
                             "local": self.market.has_local(r["instrumentId"], environment), "series": []} for _a, _b, r in page[:limit]],
                "venues": [{"venue": venue, "venueName": place_name(venue), "family": "spot", "state": "ready",
                            "problem": None}],
                "nextCursor": _cursor({"offset": offset + limit}) if len(page) > limit else None}

    def search_result(self, item: dict, environment: str = "production") -> dict:
        record = item["instrument"]
        return {"instrument": record, "matched": item["matched"], "formerCode": item["formerCode"],
                "local": self.market.has_local(record["instrumentId"], environment),
                "series": self.market.extra_series(record)[:20] if environment == "production" else []}

    def venue_states(self, value: dict) -> list[dict]:
        states = []
        for catalog in self.identity.catalogs():
            if catalog["family"] not in ("listings", "contracts"):
                continue  # code histories are not directories
            family = "listings" if catalog["family"] == "listings" else "spot"
            if value.get("markets") and catalog["venue"] not in value["markets"] and family == "listings":
                continue
            if value.get("venues") and catalog["venue"] not in value["venues"] and family != "listings":
                continue
            states.append({"venue": catalog["venue"], "venueName": place_name(catalog["venue"]), "family": family,
                           "state": catalog["state"], "problem": catalog["problem"]})
        return states

    async def discover(self, query: str, markets: list[str] | None, sources: tuple[str, ...]) -> list[str]:
        """Ask sources that search (Yahoo) once and keep the results that have prices."""
        hits = []
        for source_id in sources:
            entry = self.market.registry.entry(source_id)
            candidates = await self.market.registry.call(source_id, "search", query, {"markets": markets or []}, 10)
            listings = [c for c in candidates if c["type"] == "listing" and (not markets or c["market"] in markets)]
            checker = getattr(entry.source, "has_prices", None)
            if checker is not None:
                exact = [c for c in listings if c["code"].casefold() == query.casefold()]
                full_names = [c for c in listings if (c.get("name") or "").casefold() == query.casefold()]
                candidates = exact or full_names or listings[:3]
                verified = await asyncio.gather(*(checker(c["code"], c["market"]) for c in candidates),
                                                return_exceptions=True)
                listings = [c for c, ok in zip(candidates, verified, strict=True) if ok is True]
                for answer in verified:
                    if isinstance(answer, BaseException) and (not listings or not isinstance(answer, MarketError)):
                        raise answer
            if listings:
                hits.extend(await in_worker(self.ingest, listings, source_id, names="fill"))
        return hits

    async def resolve(self, value: dict) -> dict:
        text = value["text"].strip()
        at = int(value["at"]) if value.get("at") else None
        environment = value.get("environment", "production")
        if ":" in text and text.split(":", 1)[0].lower() not in {m.lower() for m in MARKET_CALENDARS}:
            venue, symbol = text.split(":", 1)
            return await self._resolve_contract(venue.lower(), symbol.upper(), environment)
        if value.get("venueId"):
            return await self._resolve_base(text.upper(), value)
        market, code = parse_typed(text)
        if value.get("market"):
            market = value["market"]
        candidates = []
        markets = [market] if market else list(MARKET_CALENDARS)
        for name in markets:
            found = self.identity.find_code(name, code, at)
            if found:
                candidates.append(found)
        if not candidates and at is None:
            result = await self.search({"query": code, "markets": [market] if market else None, "limit": 10},
                                       exact_code=True)
            exact = [r for r in result["results"] if r["instrument"]["symbol"].upper() == code.upper()
                     or (r["formerCode"] or {}).get("code", "").upper() == code.upper()]
            candidates = [r["instrument"]["instrumentId"] for r in exact]
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            raise MarketError("not_found", f"No instrument matches {text}")
        if len(candidates) > 1:
            raise MarketError("ambiguous", f"{text} matches several instruments",
                              details={"candidates": [self.identity.record(c) for c in candidates[:10]]})
        record = self.identity.record(candidates[0])
        matched = "code" if record["symbol"].upper() == code.upper() else "former_code"
        return {"instrument": record, "matched": matched}

    async def _resolve_contract(self, venue: str, symbol: str, environment: str) -> dict:
        def matching(records):
            # CCXT symbols and the exchange's chart labels both identify an exact, non-retired contract.
            return [r for r in records if (r["symbol"].upper() == symbol or "/" not in symbol
                    and r["displaySymbol"].upper() == symbol) and r.get("retiredAt") is None]

        if environment != "production":
            exact = matching(await self.environment_catalog(venue, environment))
        else:
            for first_page in (True, False):
                await self._directory_loaded(venue, first_page=first_page)
                exact = matching(r["instrument"] for r in self.identity.search(symbol, venues=[venue], limit=50))
                if exact:
                    break
        if not exact:
            self._directory_failed(venue)
            raise MarketError("not_found", f"{venue} has no {symbol}")
        return {"instrument": exact[0], "matched": "contract"}

    async def _resolve_base(self, base: str, value: dict) -> dict:
        await self._directory_loaded(value["venueId"])
        records = [r["instrument"] for r in self.identity.search(base, venues=[value["venueId"]], limit=200)]
        records = [r for r in records if (r.get("base") or "").upper() == base and r.get("retiredAt") is None
                   and (not value.get("marketType") or r["marketType"] == value["marketType"])
                   and (not value.get("quote") or r.get("quote") == value["quote"].upper())
                   and (not value.get("settle") or r.get("settle") == value["settle"].upper())]
        if not records:
            self._directory_failed(value["venueId"])
            raise MarketError("not_found", f"No {base} contract on {value['venueId']}")
        if len(records) > 1:
            raise MarketError("ambiguous", f"{base} matches several contracts", details={"candidates": records[:10]})
        return {"instrument": records[0], "matched": "contract"}

    async def _directory_loaded(self, venue: str, *, first_page=False) -> None:
        """A venue's contracts are resolved from its whole directory: a load still running (first
        start) or never made is awaited."""
        state = next((c for c in self.identity.catalogs() if (c["venue"], c["family"]) == (venue, "contracts")), None)
        if state is None or state["state"] == "loading":
            sources = [s for s, v, f in self.directories() if v == venue and f == "contracts"]
            if sources:
                task = self.load(sources[0], venue, "contracts")
                if first_page:
                    await self._first_pages[(sources[0], venue, "contracts")].wait()
                    if task.done():
                        task.result()
                else:
                    await task

    def _directory_failed(self, venue: str) -> None:
        """Nothing matched and the venue's directory could not load: the load's own error is the answer."""
        state = next((c for c in self.identity.catalogs() if (c["venue"], c["family"]) == (venue, "contracts")), None)
        if state is not None and state["state"] == "failed" and state["problem"]:
            problem = state["problem"]
            raise MarketError(problem["code"], problem["message"], source_id=problem.get("sourceId"),
                              status=problem.get("status"))

    # ── get ─────────────────────────────────────────────────────────────────

    def accept_record(self, record: dict) -> dict:
        """A record from elsewhere (workspace, export, another machine) → a local id."""
        with self.identity._write:
            return self._accept_record(record)

    def _accept_record(self, record: dict) -> dict:
        instrument_id = record["instrumentId"]
        survivor = self.identity.survivor(instrument_id)
        if survivor is not None:
            return {"kind": "known", "id": survivor}
        if record["identityVersion"] == 1:
            if record["venueId"].upper() in MARKET_CALENDARS:
                # A listing rebuilt as a contract (its id dropped) is another instrument; it must not become one here.
                raise MarketError("invalid_input", f"{record['symbol']} on {record['venueId']} is a listing; send the "
                                  "record with the id Market assigned (identity version 2)")
            checked = MarketInstrument.from_wire(record).wire_dict()
            self.identity.upsert_contracts(checked["venueId"], [checked])
            return {"kind": "known", "id": checked["instrumentId"]}
        market, code = record["venueId"], record["symbol"]
        rows = self.identity.db().execute(
            "select distinct id from codes where market=? and code=? collate nocase", (market, code)).fetchall()
        local = []
        for (candidate,) in rows:
            other = self.identity.record(candidate)
            if other is None or other["symbol"].upper() != code.upper():
                continue
            if record.get("listedFrom") and other.get("listedFrom") and record["listedFrom"] != other["listedFrom"]:
                continue
            local.append(other)
        if not local:
            if rows:
                return {"kind": "missing", "candidates": [self.identity.record(r[0]) for r in rows[:10]]}
            self.identity.adopt(MarketInstrument.from_wire(record).wire_dict())
            return {"kind": "adopted", "id": instrument_id}
        if len(local) > 1:
            return {"kind": "missing", "candidates": local[:10]}
        self.identity.alias(instrument_id, local[0]["instrumentId"], record)
        return {"kind": "alias", "id": local[0]["instrumentId"]}

    # ── catalog ─────────────────────────────────────────────────────────────

    async def catalog(self, value: dict) -> dict:
        venue, environment = value["venueId"], value.get("environment", "production")
        cursor, limit = _uncursor(value.get("cursor")), value["limit"]
        products = value.get("products")
        if environment != "production":
            records = await self.environment_catalog(venue, environment)
            if products:
                records = [r for r in records if r["marketType"] in products]
            offset = cursor.get("offset", 0)
            page = records[offset:offset + limit]
            return {"instruments": page, "nextCursor": _cursor({"offset": offset + limit}) if offset + limit < len(records) else None,
                    "revision": f"{environment}:{len(records)}", "complete": True, "loadedAt": str(now_ns()),
                    "environment": environment}
        states = {(c["venue"], c["family"]): c for c in self.identity.catalogs()}
        family = "listings" if venue in MARKET_CALENDARS else "contracts"
        state = states.get((venue, family))
        if state is None or state["state"] == "loading":
            sources = [s for s, v, f in self.directories() if v == venue and f == family]
            if not sources:
                raise MarketError("not_found", f"No directory for {venue}")
            await self.load(sources[0], venue, family)
            state = {(c["venue"], c["family"]): c for c in self.identity.catalogs()}[(venue, family)]
        if cursor and cursor.get("revision") != state["revision"]:
            raise MarketError("invalid_input", "The directory changed; start again without a cursor")
        kinds = ("listing",) if family == "listings" else ("contract",)
        records = self.identity.catalog_page(venue, kinds=kinds, asset_classes=value.get("assetClasses"),
                                             market_types=products, include_inactive=value.get("includeInactive", False),
                                             after=cursor.get("after"), limit=limit + 1)
        more = len(records) > limit
        records = records[:limit]
        return {"instruments": records,
                "nextCursor": _cursor({"after": records[-1]["instrumentId"], "revision": state["revision"]}) if more else None,
                "revision": state["revision"] or "0", "complete": state["state"] == "ready",
                "loadedAt": str(state["loadedAt"] or now_ns()), "environment": environment}
