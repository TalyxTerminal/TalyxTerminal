"""Exchange calendars: trading days, session times and bar boundaries.

The calendar data is bundled with exchange_calendars (no network). Stocks keep the
exchange's local trading day: a daily bar's ``time`` is the trading date's local
midnight as UTC and its ``closeTime`` the actual close. Crypto (24_7) uses UTC days.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import exchange_calendars as xcals
import numpy as np
import pandas as pd
from exchange_calendars.exchange_calendar_xshg import XSHGExchangeCalendar
from talyx_sdk.i18n import t

from vocabulary.identity import normalize_timeframe, timeframe_nanoseconds

from .common import DAY_NS, NS, MarketError

CONTINUOUS = "24_7"
EXCHANGES = ("XNYS", "XSHG", "XHKG")
_ALIASES = {"XNAS": "XNYS", "XASE": "XNYS", "ARCX": "XNYS", "BATS": "XNYS", "OTCM": "XNYS", "XSHE": "XSHG",
            "XBSE": "XSHG"}
_STARTS = {"XSHG": "1991-01-01"}
# US extended hours (pre-market 04:00, after hours until 20:00 New York time).
_EXTENDED = {"XNYS": (4 * 3600, 20 * 3600)}
# A-share closures published after the bundled exchange_calendars: the weekdays of the State Council's
# holiday notice for the year (2027: 国办发明电〔2026〕10号, 2026-11-05). The exchanges close on these and
# on every weekend, make-up working days included.
_XSHG_PUBLISHED = pd.to_datetime([
    "2027-01-01", "2027-02-05", "2027-02-08", "2027-02-09", "2027-02-10", "2027-02-11", "2027-02-12",
    "2027-04-05", "2027-05-03", "2027-05-04", "2027-05-05", "2027-06-09", "2027-09-15",
    "2027-10-01", "2027-10-04", "2027-10-05", "2027-10-06", "2027-10-07",
])


class _XSHG(XSHGExchangeCalendar):
    """XSHG holidays are published lists, so the calendar runs to the end of the last published year."""

    @classmethod
    def precomputed_holidays(cls):
        return XSHGExchangeCalendar.precomputed_holidays().union(_XSHG_PUBLISHED)


def canonical(calendar_id: str) -> str:
    value = calendar_id.strip().upper().replace("24/7", CONTINUOUS)
    return _ALIASES.get(value, value)


def exchange(calendar_id: str):
    return _exchange(canonical(calendar_id))


@lru_cache(maxsize=32)
def _exchange(name: str):
    if name == "XSHG":
        return _XSHG(start=_STARTS[name], end=_XSHG.bound_max())
    try:
        # From 1960: Yahoo's US and Hong Kong histories reach back past 1990, and dividend adjustment needs the
        # session before each ex-date.
        return xcals.get_calendar(name, start=_STARTS.get(name, "1960-01-01"))
    except xcals.errors.InvalidCalendarName as exc:
        raise MarketError("unsupported", t("Unknown trading calendar {name}", name=name)) from exc


def prepare() -> None:
    """Build the calendars of the exchanges Market's sources list. Each takes a few hundred milliseconds of pandas;
    Market builds them on a worker thread while it starts, so its loop never does."""
    for name in EXCHANGES:
        _schedule(name)


def timezone(calendar_id: str) -> str:
    return "UTC" if canonical(calendar_id) == CONTINUOUS else str(exchange(calendar_id).tz)


@lru_cache(maxsize=32)
def _schedule(calendar_id: str):
    cal = exchange(calendar_id)
    frame = cal.schedule
    return (frame.index.values.astype("datetime64[D]"), frame["open"].values.astype("datetime64[ns]").astype(np.int64),
            frame["close"].values.astype("datetime64[ns]").astype(np.int64),
            frame["break_start"].values.astype("datetime64[ns]").astype(np.int64),
            frame["break_end"].values.astype("datetime64[ns]").astype(np.int64))


@lru_cache(maxsize=32)
def _session_midnights(calendar_id: str) -> np.ndarray:
    """Local 00:00 of every session in the schedule: bar grids index it instead of asking pandas each time."""
    return local_midnight(calendar_id, _schedule(calendar_id)[0])


def local_midnight(calendar_id: str, days: np.ndarray) -> np.ndarray:
    """Local 00:00 of each date (datetime64[D]) as UTC nanoseconds."""
    if canonical(calendar_id) == CONTINUOUS:
        return days.astype("datetime64[ns]").astype(np.int64)
    index = pd.DatetimeIndex(days.astype("datetime64[ns]")).tz_localize(timezone(calendar_id))
    return index.asi8.astype(np.int64)


def trading_day(calendar_id: str, times: np.ndarray) -> np.ndarray:
    """The local calendar date (datetime64[D]) of UTC nanosecond instants."""
    if canonical(calendar_id) == CONTINUOUS:
        return times.astype("datetime64[ns]").astype("datetime64[D]")
    local = pd.DatetimeIndex(times.astype("datetime64[ns]")).tz_localize("UTC").tz_convert(timezone(calendar_id))
    return local.tz_localize(None).values.astype("datetime64[D]")


def session_closes(calendar_id: str, days: np.ndarray) -> np.ndarray:
    """Close instant of each trading date; dates outside the calendar close at the
    calendar's latest regular close time on that date."""
    if canonical(calendar_id) == CONTINUOUS:
        return days.astype("datetime64[ns]").astype(np.int64) + DAY_NS
    sessions, _opens, closes, _bs, _be = _schedule(calendar_id)
    position = np.searchsorted(sessions, days)
    position = np.clip(position, 0, len(sessions) - 1)
    found = sessions[position] == days
    result = np.empty(len(days), dtype=np.int64)
    result[found] = closes[position[found]]
    if (~found).any():
        cal = exchange(calendar_id)
        seconds = cal.close_times[-1][1]
        offset = (seconds.hour * 3600 + seconds.minute * 60) * NS
        result[~found] = local_midnight(calendar_id, days[~found]) + offset
    return result


def daily_times(calendar_id: str, days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return local_midnight(calendar_id, days), session_closes(calendar_id, days)


def coverage(calendar_id: str) -> tuple[date | None, date | None]:
    if canonical(calendar_id) == CONTINUOUS:
        return None, None
    cal = exchange(calendar_id)
    return cal.first_session.date(), cal.last_session.date()


def sessions(calendar_id: str, start: date, end: date) -> dict:
    if end < start or (end - start).days > 3660:
        raise MarketError("invalid_input", t("sessions take a window of at most ten years"))
    if canonical(calendar_id) == CONTINUOUS:
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        items = [{"date": day.isoformat(), "open": str(_utc(day)), "close": str(_utc(day) + DAY_NS), "breaks": [],
                  "short": False} for day in days]
        return {"calendarId": CONTINUOUS, "timezone": "UTC", "mode": "continuous", "evidence": "verified",
                "coveredFrom": None, "coveredUntil": None, "sessions": items}
    first, last = coverage(calendar_id)
    days, opens, closes, breaks_start, breaks_end = _schedule(calendar_id)
    lo, hi = np.searchsorted(days, np.datetime64(start)), np.searchsorted(days, np.datetime64(end), side="right")
    regular = _regular_length(calendar_id)
    items = []
    for i in range(lo, hi):
        breaks = []
        if breaks_start[i] > 0:
            breaks.append({"start": str(int(breaks_start[i])), "end": str(int(breaks_end[i])), "unit": "ns",
                           "bounds": "[)"})
        items.append({"date": str(days[i]), "open": str(int(opens[i])), "close": str(int(closes[i])),
                      "breaks": breaks, "short": int(closes[i] - opens[i]) < regular})
    evidence = "verified" if first <= start and end <= last else "unknown"
    return {"calendarId": canonical(calendar_id), "timezone": timezone(calendar_id), "mode": "exchange",
            "evidence": evidence, "coveredFrom": first.isoformat(), "coveredUntil": last.isoformat(),
            "sessions": items}


def _utc(day: date) -> int:
    return int(np.datetime64(day, "D").astype("datetime64[ns]").astype(np.int64))


@lru_cache(maxsize=32)
def _regular_length(calendar_id: str) -> int:
    _days, opens, closes, _bs, _be = _schedule(calendar_id)
    return int(np.median(closes - opens))


def bar_times(calendar_id: str, timeframe: str, scope: str, start: int, end: int, after: int | None,
              limit: int) -> dict:
    """Open and close instants of the bars of one timeframe inside [start, end)."""
    timeframe = normalize_timeframe(timeframe)
    if end <= start or end - start > 366 * DAY_NS:
        raise MarketError("invalid_input", t("bar_times takes a window of at most 366 days"))
    opens, closes = bar_grid(calendar_id, timeframe, start, end, scope)
    if after is not None:
        keep = opens > after
        opens, closes = opens[keep], closes[keep]
    more = len(opens) > limit
    opens, closes = opens[:limit], closes[:limit]
    first, last = coverage(calendar_id)
    continuous = canonical(calendar_id) == CONTINUOUS
    evidence = "verified" if continuous or (first and _date(start) >= first and _date(end - 1) <= last) else "unknown"
    return {"timezone": timezone(calendar_id), "evidence": evidence,
            "coveredFrom": None if continuous else first.isoformat(),
            "coveredUntil": None if continuous else last.isoformat(),
            "open": [str(int(x)) for x in opens], "close": [str(int(x)) for x in closes],
            "next": str(int(opens[-1])) if more and len(opens) else None,
            "reason": None if evidence == "verified" else t("Outside the bundled exchange calendar")}


def _date(ns: int) -> date:
    return (np.datetime64(ns, "ns").astype("datetime64[D]")).astype(object)


def bar_grid(calendar_id: str, timeframe: str, start: int, end: int, scope: str = "regular"
             ) -> tuple[np.ndarray, np.ndarray]:
    """Every bar [open, close) of a timeframe whose open lies in [start, end)."""
    timeframe = normalize_timeframe(timeframe)
    step = timeframe_nanoseconds(timeframe)
    if canonical(calendar_id) == CONTINUOUS:
        if step is None or step >= DAY_NS:
            days = np.arange(np.datetime64(_date(start)), np.datetime64(_date(end - 1)) + np.timedelta64(1, "D")).astype("datetime64[D]")
            opens = period_starts(CONTINUOUS, timeframe, days.astype("datetime64[ns]").astype(np.int64))
            opens = np.unique(opens)
            closes = period_ends(CONTINUOUS, timeframe, opens)
        else:
            first = -(-start // step) * step
            opens = np.arange(first, end, step, dtype=np.int64)
            closes = opens + step
        keep = (opens >= start) & (opens < end)
        return opens[keep], closes[keep]
    days, s_open, s_close, b_start, b_end = _schedule(calendar_id)
    # One session either side catches bars whose local day differs from the UTC day; past the calendar's last session there are none.
    lo = max(np.searchsorted(days, np.datetime64(_date(start))) - 1, 0)
    hi = min(np.searchsorted(days, np.datetime64(_date(end - 1)), side="right") + 1, len(days))
    if step is None or step >= DAY_NS:
        mids = _session_midnights(calendar_id)[lo:hi]
        if step == DAY_NS:
            opens, closes = mids, s_close[lo:hi]
        else:
            opens = np.unique(period_starts(calendar_id, timeframe, mids))
            closes = period_ends(calendar_id, timeframe, opens)
        keep = (opens >= start) & (opens < end)
        return opens[keep], closes[keep]
    out_open, out_close = [], []
    for i in range(lo, hi):
        o, c = int(s_open[i]), int(s_close[i])
        if scope in ("extended", "all") and canonical(calendar_id) in _EXTENDED:
            mid = int(_session_midnights(calendar_id)[i])
            o, c = mid + _EXTENDED[canonical(calendar_id)][0] * NS, mid + _EXTENDED[canonical(calendar_id)][1] * NS
            pieces = [(o, c)]
        elif b_start[i] > 0:
            pieces = [(o, int(b_start[i])), (int(b_end[i]), c)]
        else:
            pieces = [(o, c)]
        for a, b in pieces:
            bars = np.arange(a, b, step, dtype=np.int64)
            out_open.append(bars)
            out_close.append(np.minimum(bars + step, b))
    if not out_open:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    opens, closes = np.concatenate(out_open), np.concatenate(out_close)
    keep = (opens >= start) & (opens < end)
    return opens[keep], closes[keep]


def period_starts(calendar_id: str, timeframe: str, times: np.ndarray) -> np.ndarray:
    """Start of the daily-or-longer period containing each bar time (bar times are local midnights)."""
    timeframe = normalize_timeframe(timeframe)
    count = int(timeframe.rstrip("dwmoy"))
    unit = timeframe[len(str(count)):]
    days = trading_day(calendar_id, times)
    if unit == "d":
        epoch = days.astype(np.int64)
        start = days - (epoch % count).astype("timedelta64[D]")
    elif unit == "w":
        # ISO weeks start on Monday; 1970-01-01 was a Thursday.
        epoch = days.astype(np.int64)
        monday = epoch - (epoch + 3) % 7
        start = (monday - (monday // 7 % count) * 7).astype("datetime64[D]")
    elif unit == "mo":
        months = days.astype("datetime64[M]").astype(np.int64)
        start = (months - months % count).astype("datetime64[M]").astype("datetime64[D]")
    else:
        years = days.astype("datetime64[Y]").astype(np.int64)
        start = (years - years % count).astype("datetime64[Y]").astype("datetime64[D]")
    return local_midnight(calendar_id, start.astype("datetime64[D]"))


def period_end(calendar_id: str, timeframe: str, start: int) -> int:
    """The instant the daily-or-longer period starting at ``start`` ends."""
    return int(period_ends(calendar_id, timeframe, np.array([start], dtype=np.int64))[0])


def period_ends(calendar_id: str, timeframe: str, starts: np.ndarray) -> np.ndarray:
    """``period_end`` of many period starts at once (a weekly chart's 2,000 periods: one pandas call, not 2,000)."""
    timeframe = normalize_timeframe(timeframe)
    count = int(timeframe.rstrip("dwmoy"))
    unit = timeframe[len(str(count)):]
    days = trading_day(calendar_id, starts)
    if unit == "d":
        nxt = days + np.timedelta64(count, "D")
    elif unit == "w":
        nxt = days + np.timedelta64(7 * count, "D")
    elif unit == "mo":
        nxt = (days.astype("datetime64[M]") + np.timedelta64(count, "M")).astype("datetime64[D]")
    else:
        nxt = (days.astype("datetime64[Y]") + np.timedelta64(count, "Y")).astype("datetime64[D]")
    if canonical(calendar_id) == CONTINUOUS:
        return nxt.astype("datetime64[ns]").astype(np.int64)
    # The period closes at the last session close before the next period starts.
    sessions, _o, closes, _bs, _be = _schedule(calendar_id)
    index = np.searchsorted(sessions, nxt) - 1
    inside = (index >= 0) & (sessions[np.maximum(index, 0)] >= days)
    ends = np.empty(len(days), dtype=np.int64)
    ends[inside] = closes[index[inside]]
    if not inside.all():
        ends[~inside] = local_midnight(calendar_id, nxt[~inside])
    return ends
