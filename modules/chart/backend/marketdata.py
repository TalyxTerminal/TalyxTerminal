"""What Chart uses of Market's public services and streams."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from talyx_sdk import ContractError

from modules.market.public.python.client import MarketClient
from vocabulary.instruments import MarketInstrument, ohlcv_series

PINE_PERIODS = {"s": "S", "m": "", "h": "", "d": "D", "w": "W", "mo": "M"}


def pine_period(timeframe: str) -> str:
    """Market timeframe as Pine's ``timeframe.period`` (1m → "1", 4h → "240", 1d → "1D")."""
    count = int("".join(ch for ch in timeframe if ch.isdigit()))
    unit = timeframe[len(str(count)):]
    if unit == "m":
        return str(count)
    if unit == "h":
        return str(count * 60)
    if unit == "y":
        return f"{count * 12}M"
    return f"{count}{PINE_PERIODS[unit]}"


def market_timeframe(period: str) -> str:
    """Pine's timeframe ("1", "60", "240", "D", "1W", "M", "30S") as Market's (1m, 1h, 4h, 1d, 1w, 1mo, 30s)."""
    digits = "".join(ch for ch in period if ch.isdigit())
    count, unit = int(digits or 1), period[len(digits):].upper()
    if unit == "":
        return f"{count // 60}h" if count % 60 == 0 else f"{count}m"
    units = {"S": "s", "D": "d", "W": "w", "M": "mo"}
    if unit not in units:
        raise ValueError(f"Unknown timeframe {period!r}")
    return f"{count}{units[unit]}"


class Market:
    def __init__(self, ctx):
        self.ctx = ctx
        self.client = MarketClient(ctx)
        self.zones: dict[str, str] = {}

    async def matches(self, symbols):
        items = []
        for start in range(0, len(symbols), 500):
            query = {"symbols": symbols[start:start + 500]}
            while True:
                page = await self.client.instruments_get(query)
                items.extend(page["items"])
                if page["nextCursor"] is None:
                    break
                query["cursor"] = page["nextCursor"]
        return items

    async def records(self, symbols):
        result = []
        for match in await self.matches(symbols):
            if match["status"] not in ("found", "redirect"):
                raise ContractError(match["status"], f"Could not resolve {match['symbol']}",
                                    {"candidates": match.get("candidates") or []})
            result.append(match["instrument"])
        return result

    async def instrument(self, instrument_id: str) -> tuple[dict, str | None]:
        match, = await self.matches([instrument_id])
        if match["status"] not in ("found", "redirect"):
            raise LookupError(f"Market does not know instrument {instrument_id}")
        record = match["instrument"]
        return record, record["instrumentId"] if match["status"] == "redirect" else None

    def default_series(self, instrument: dict, timeframe: str, **choice) -> dict:
        """Market's default price series of an instrument (adjustment and session per market)."""
        return ohlcv_series(MarketInstrument.from_wire(instrument), timeframe,
                            **{key: value for key, value in choice.items() if value is not None}).to_dict()

    async def price_increment(self, instrument: dict) -> str | None:
        items = (await self.client.instruments_terms({"symbols": [instrument["instrumentId"]], "allowMissingPrices": True}))["items"]
        return items[0]["priceIncrement"] if items else None

    async def timezone(self, calendar_id: str) -> str:
        if calendar_id not in self.zones:
            today = date.today()
            result = await self.client.calendar_sessions({"calendarId": calendar_id, "startDate": today.isoformat(),
                                                   "endDate": (today + timedelta(days=1)).isoformat()})
            self.zones[calendar_id] = result["timezone"] or "UTC"
        return self.zones[calendar_id]

    async def bar_times(self, instrument: dict, series: dict, start_ms: int, end_ms: int) -> tuple[list, list, int | None]:
        """Open and close times of the series' bars in [start, end) by Market's calendar, and
        when its coverage ends (ms, None when unknown)."""
        opens, closes, after, covered = [], [], None, None
        end_ms = min(end_ms, start_ms + 366 * 86_400_000)
        while True:
            query = {"calendarId": instrument["calendarId"], "timeframe": series["timeframe"],
                     "sessionScope": series["sessionScope"], "start": str(start_ms * 1_000_000),
                     "end": str(end_ms * 1_000_000 - 1), **({"after": after} if after else {})}
            page = await self.client.calendar_bar_times(query)
            opens += [int(value) // 1_000_000 for value in page["open"]]
            closes += [int(value) // 1_000_000 for value in page["close"]]
            if page["coveredUntil"]:
                day = date.fromisoformat(page["coveredUntil"]) + timedelta(days=1)
                zone = ZoneInfo(page["timezone"] or "UTC")
                covered = int(datetime(day.year, day.month, day.day, tzinfo=zone).timestamp() * 1000)
            after = page["next"]
            if after is None:
                return opens, closes, covered

    async def subscribe(self, instrument: dict, series: dict, warmup: int):
        """Market's session stream of a series. When Market restarts, the SDK opens it again by itself: the series
        starts over with Market's first page, so alerts keep watching."""
        return await self.ctx.subscribe("market.session", {"instrument": instrument, "series": series,
                                                           "warmup": warmup})

    async def older(self, instrument: dict, series: dict, before_ms: int, limit: int) -> dict:
        """One page of closed bars before a time (Market fetches from its source when needed)."""
        return await self.client.read_series({"instrumentId": instrument["instrumentId"], "series": series, "before": str(before_ms * 1_000_000),
                                     "limit": limit, "fill": "await"})


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
