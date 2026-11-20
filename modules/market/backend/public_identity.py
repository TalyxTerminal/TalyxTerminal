"""Instrument spelling and record transfer at the public Market boundary."""

from __future__ import annotations

from datetime import date, timedelta

import orjson
import pandas as pd

from . import calendars
from .codes import MARKET_CALENDARS, parse_typed
from .common import MarketError
from .routing import in_worker


def instant(text, calendar, *, inclusive_end=False):
    """Convert a trading date or zoned instant to an internal half-open window boundary."""
    if text is None:
        return None
    try:
        if len(text) == 10:
            day = date.fromisoformat(text) + timedelta(days=int(inclusive_end))
            return pd.Timestamp(day, tz=calendars.timezone(calendar)).value
        stamp = pd.Timestamp(text)
        if stamp.tz is None:
            raise ValueError("missing timezone")
        return stamp.value + int(inclusive_end)
    except (ValueError, OverflowError) as error:
        raise MarketError(
            "invalid_input", f"Expected a trading date or a zoned ISO instant, got {text}"
        ) from error


def offset(value):
    cursor = value.get("cursor")
    if cursor is None:
        return 0
    if not isinstance(cursor, str) or not cursor.isascii() or not cursor.isdecimal():
        raise MarketError("invalid_input", "Invalid page cursor")
    return int(cursor)


def fits(value):
    return len(orjson.dumps(value).decode("utf-8")) <= 18_000


class PublicIdentity:
    def __init__(self, market):
        self.market = market
        self.identity = market.identity
        self.instruments = market.instruments

    def spelling(self, record):
        code, venue, identity = record["symbol"], record["venueId"], record["instrumentId"]
        if record.get("delistedAt") or record.get("retiredAt") or record.get("active") is False:
            return identity
        if record["identityVersion"] == 1:
            return f"{venue}:{code}"
        if self.identity.find_code(venue, code) != identity:
            return identity
        fixed, _ = parse_typed(code)
        if fixed == venue:
            return code
        qualified = any(
            self.identity.find_code(other, code) for other in MARKET_CALENDARS if other != venue
        )
        return f"{venue}:{code}" if qualified or fixed else code

    def by_id(self, symbol):
        record = self.identity.record(symbol)
        if record is None:
            raise MarketError("not_found", f"Unknown instrument ID {symbol}")
        return record

    async def record(self, symbol, *, at=None, environment="production"):
        if symbol.startswith(("vi1_", "vi2_")):
            return await in_worker(self.by_id, symbol)
        market, _ = parse_typed(symbol)
        request = {"text": symbol, "environment": environment}
        if at is not None:
            request["at"] = str(instant(at, MARKET_CALENDARS.get(market, "24_7")))
        return (await self.instruments.resolve(request))["instrument"]

    def transferred(self, original):
        result = self.instruments.accept_record(original)
        if result["kind"] == "missing":
            raise MarketError(
                "ambiguous",
                f"Could not reconcile {original['symbol']} with a local listing",
                details={
                    "instrumentId": original["instrumentId"],
                    "candidates": result["candidates"],
                },
            )
        return self.identity.record(result["id"])

    async def match(self, original, request):
        if isinstance(original, dict):
            record = await in_worker(self.transferred, original)
            text = original["instrumentId"]
        else:
            text = original
            try:
                record = await self.record(
                    text, at=request.get("at"), environment=request.get("environment", "production")
                )
            except MarketError as error:
                if error.code not in ("ambiguous", "not_found"):
                    raise
                return {"status": error.code, "symbol": text, **(error.details or {})}
        identifier = record["instrumentId"]
        moved = text.startswith(("vi1_", "vi2_")) and text != identifier
        match = {
            "status": "redirect" if moved else "found",
            "instrument": record,
            "symbol": await in_worker(self.spelling, record),
        }
        if moved:
            match["previousId"] = text
        if "codes" in request.get("include", []):

            def history():
                def day(stamp, *, last=False):
                    if stamp is None:
                        return None
                    value = int(stamp) - int(last)
                    return (
                        pd.Timestamp(value, unit="ns", tz="UTC")
                        .tz_convert(calendars.timezone(record["calendarId"]))
                        .date()
                        .isoformat()
                    )

                codes = [
                    {
                        "code": period["code"],
                        "name": period["name"],
                        "start": day(period["from"]),
                        "end": day(period["until"], last=True),
                    }
                    for period in self.identity.codes(identifier)
                ]
                return {
                    "codes": codes,
                    "externalIds": self.identity.external_ids(identifier),
                    "conflicts": self.identity.conflicts(identifier),
                }

            match.update(await in_worker(history))
        return match

    async def get(self, request):
        targets = request.get("symbols") or request["records"]
        start = offset(request)
        items = []
        for target in targets[start : start + request.get("limit", 24)]:
            item = await self.match(target, request)
            if not fits({"items": [*items, item], "nextCursor": str(start + len(items))}):
                if not items:
                    raise MarketError("too_large", "One instrument record exceeds the page size")
                break
            items.append(item)
        end = start + len(items)
        return {"items": items, "nextCursor": str(end) if end < len(targets) else None}

    async def search(self, request):
        value = dict(request)
        for one, many in (
            ("venue", "venues"),
            ("market", "markets"),
            ("assetClass", "assetClasses"),
            ("marketType", "marketTypes"),
        ):
            if one in value:
                item = value.pop(one)
                value[many] = item if isinstance(item, list) else [item]
        value["limit"] = request.get("limit", 24)
        while True:
            page = await self.search_page(value)
            for result in page["results"]:
                record = result["instrument"]
                result.update(symbol=self.spelling(record), instrumentId=record["instrumentId"])
            if fits(page):
                return page
            if len(page["results"]) <= 1:
                raise MarketError("too_large", "One search result exceeds the page size")
            value["limit"] = len(page["results"]) // 2

    async def search_page(self, value):
        if not value.get("query"):
            venues = value.get("venues", [])
            if len(venues) != 1:
                raise MarketError("invalid_input", "Supply query or one venue")
            page = await self.instruments.catalog(
                {
                    "venueId": venues[0],
                    "environment": value.get("environment", "production"),
                    "limit": value["limit"],
                    "cursor": value.get("cursor"),
                    "products": value.get("marketTypes"),
                    "assetClasses": value.get("assetClasses"),
                    "includeInactive": value.get("includeInactive", False),
                }
            )
            return {
                "results": [
                    self.instruments.search_result(
                        {"instrument": i, "matched": "code", "formerCode": None}, value.get("environment", "production")
                    )
                    for i in page["instruments"]
                ],
                "venues": self.instruments.venue_states(value),
                "nextCursor": page["nextCursor"],
            }
        return await self.instruments.search(value)
