"""Market's trading calendar as the research data needs it: bars per year, each bar's label and when its value could be known."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from talyx_sdk import ContractError

from .market_inputs import ms, ns
from .materials import plain

YEAR_MS = 365.25 * 86_400_000
PAGE = 5000
WINDOW_MS = 366 * 86_400_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def date_window(ctx, calendar_id, timeframe, start, end):
    """Explicit date-label bounds become instants only for daily bars; ordinary timestamp windows stay untouched."""
    if not timeframe.endswith("d"):
        return start, end
    first, last = (EPOCH + timedelta(milliseconds=start)).date(), (EPOCH + timedelta(milliseconds=end)).date()  # any instant, whatever the OS allows
    page = plain(await ctx.call("market.calendar.sessions", {
        "calendarId": calendar_id, "startDate": first.isoformat(), "endDate": first.isoformat()}))
    if page["timezone"] is None:
        raise ContractError("unavailable", f"Market has no timezone for {calendar_id}.")
    zone = ZoneInfo(page["timezone"])
    opened = datetime.combine(first, datetime.min.time(), zone)
    stopped = datetime.combine(last + timedelta(days=1), datetime.min.time(), zone)
    return int(opened.timestamp() * 1000), int(stopped.timestamp() * 1000) - 1


async def date_windows(ctx, instruments, timeframe, start, end):
    """One conversion per calendar; mixed-market strategies keep a window for every instrument."""
    calendars = {item["calendarId"] for item in instruments}
    windows = {calendar: await date_window(ctx, calendar, timeframe, start, end) for calendar in sorted(calendars)}
    return {item["instrumentId"]: windows[item["calendarId"]] for item in instruments}


async def date_boundaries(ctx, calendar_id, timeframe, scope, start, end):
    start, end = await date_window(ctx, calendar_id, timeframe, start, end)
    return await boundaries(ctx, calendar_id, timeframe, scope, start, end)


async def boundaries(ctx, calendar_id, timeframe, scope, start, end):
    """Market's bar boundaries (open and close, in milliseconds) between two instants, and the calendar's time zone. Market answers at most a year at a time."""
    found, zone, window = {}, "UTC", start
    stop = end + 1
    while window < stop:
        limit = min(stop, window + WINDOW_MS)
        after = None
        while True:
            page = plain(await ctx.call("market.calendar.bar_times", {
                "calendarId": calendar_id, "timeframe": timeframe, "sessionScope": scope,
                "start": ns(window), "end": str(limit * 1_000_000 - 1), "limit": PAGE,
                **({"after": after} if after is not None else {})}))
            if page["evidence"] != "verified":
                raise ContractError("unavailable", f"Market has no verified trading calendar {calendar_id} for {timeframe} bars: {page['reason']}.",
                                    {"calendarId": calendar_id})
            zone = page["timezone"]
            found.update((ms(opened), ms(closed)) for opened, closed in zip(page["open"], page["close"], strict=True))
            after = page["next"]
            if after is None:
                break
        window = limit
    return zone, sorted(found.items())


def annualization(found, start, end):
    """Bars per year: the boundaries Market counts in the range, scaled to one year."""
    if not found:
        raise ContractError("unavailable", "Market's calendar lists no bars in the selected range.")
    return round(len(found) * YEAR_MS / (end - start))


def label_of(time_ms, timeframe, zone):
    """The bar's place on the research table: its opening time, and for daily bars the midnight (UTC) of its local session date."""
    if timeframe[-1] != "d":
        return time_ms
    local = datetime.fromtimestamp(time_ms / 1000, ZoneInfo(zone)).date()
    return int(datetime(local.year, local.month, local.day, tzinfo=UTC).timestamp() * 1000)


def available_at(labels, timeframe, zone, found):
    """For each label the close of the bar it names: when the value computed from that bar's close could be known."""
    closes = {label_of(opened, timeframe, zone): closed for opened, closed in found}
    missing = [label for label in labels if label not in closes]
    if missing:
        raise ContractError("unavailable", f"Market's calendar has no bar at {len(missing)} of the values' times (the first is {missing[0]}).")
    return [closes[label] for label in labels]
