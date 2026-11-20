"""A stored series is behind only when its source has published a closed bar it does not hold.

Before the US open, AAPL's hourly series that ends with yesterday's last bar (15:30 New York)
is current; it was listed "behind" because "closed until now" counted the night as missing.
A bar counts as published once its close is past the series' publish cutoff (now minus the
source's publish delay), so Tuesday's daily bar is not due five minutes after the close.
"""

from datetime import UTC, datetime

from modules.market.backend.data import Data

AAPL = {"calendarId": "XNYS"}
BTC = {"calendarId": "24_7"}
HOUR = 3600 * 10**9


def ns(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()) * 10**9


def behind(instrument, timeframe, last, cutoff):
    return last < Data.__new__(Data).last_closed_open(instrument["calendarId"], timeframe, cutoff)


def test_a_series_is_behind_only_when_a_published_bar_is_missing():
    before_open = ns("2026-11-10T12:45")  # Tuesday, 07:45 in New York
    assert not behind(AAPL, "1h", ns("2026-11-09T20:30"), before_open)   # Monday's last bar (15:30-16:00)
    assert behind(AAPL, "1h", ns("2026-11-09T19:30"), before_open)
    assert not behind(AAPL, "1d", ns("2026-11-09T05:00"), before_open)   # Monday's daily bar (local midnight)
    assert behind(AAPL, "1d", ns("2026-11-06T05:00"), before_open)
    after_close = ns("2026-11-10T21:05")
    assert behind(AAPL, "1d", ns("2026-11-09T05:00"), after_close)
    # With a four-hour publish delay, Tuesday's daily bar is due at 20:00 New York, not at the 16:00 close.
    assert not behind(AAPL, "1d", ns("2026-11-09T05:00"), after_close - 4 * HOUR)
    assert behind(AAPL, "1d", ns("2026-11-09T05:00"), ns("2026-11-11T01:05") - 4 * HOUR)
    assert not behind(BTC, "1h", ns("2026-11-10T11:00"), before_open)    # 11:00-12:00 closed, 12:00 forming
    assert behind(BTC, "1h", ns("2026-11-10T10:00"), before_open)
