import sys
from pathlib import Path

import pandas as pd
import pytest

from modules.quant.backend import research_calendar, research_data

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
from native.allocation_holdings import book_period  # noqa: E402
from native.allocation_prices import daily_returns  # noqa: E402

DAY = 86_400_000
HOUR = DAY // 24


def test_close_fee_is_charged_on_the_equity_traded():
    daily = pd.Series([.1, .0])
    book_period(daily, pd.DataFrame({"A": [0, 0]}), 0, 1, [1], None, [.01])
    assert daily.iloc[0] == pytest.approx(.089)  # USD 100 -> 110 -> 108.90


def test_hourly_crypto_calendar_annualizes_four_daily_closes_as_days():
    bars = [(h * HOUR, (h + 1) * HOUR) for h in range(96)]
    _, days = research_data.shared_calendar({("24_7", "all"): ("UTC", bars)}, "1h", calendars="many")
    annual = research_calendar.annualization(days, 0, 4 * DAY - 1)
    closes = [100, 99, 102, 104]
    frame = pd.DataFrame([(h * HOUR, "BTC", closes[h // 24]) for h in range(96)], columns=["label", "column", "close"])
    returns, _ = daily_returns(frame, ["BTC"])
    assert annual == 365
    assert returns["BTC"].std() * annual ** .5 == pytest.approx(.39887, abs=.00002)
    assert len(days) == 4


def test_separate_market_closes_are_kept_on_the_same_session_date():
    rows = [(day * DAY + hour * HOUR, asset, price * (1 + day / 10)) for day in range(4)
            for hour, asset, price in [(16, "A", 100), (21, "B", 200)]]
    frame = pd.DataFrame(rows, columns=["label", "column", "close"])
    returns, info = daily_returns(frame, ["A", "B"])
    assert info["closes"].iloc[-1].tolist() == pytest.approx([130, 260])
    assert returns.iloc[0].tolist() == pytest.approx([.1, .1])


def test_evening_hourly_bars_use_the_exchange_date_across_utc_midnight():
    # New York 20:00 EST is 01:00 UTC the following day. Tokyo 15:00 JST is 06:00 UTC the same day.
    rows = [(day * DAY + hour * HOUR, name, base * (day + 1))
            for day in range(4) for hour, name, base in [(25, "NY", 100), (6, "Tokyo", 200)]]
    frame = pd.DataFrame(rows, columns=["label", "column", "close"])
    _, info = daily_returns(frame, ["NY", "Tokyo"], zones={"NY": "America/New_York", "Tokyo": "Asia/Tokyo"})
    assert info["closes"].index.tolist() == pd.date_range("1970-01-01", periods=4).tolist()
    assert info["closes"].iloc[-1].tolist() == [400, 800]


@pytest.mark.parametrize("align", ["weekly", "monthly"])
def test_surviving_asset_retains_periods_after_the_other_exits(align):
    start = pd.Timestamp("2025-01-01").value // 10**6
    rows = [(start + day * DAY, name, 100 + day) for name, count in [("A", 56), ("B", 28)] for day in range(count)]
    returns, info = daily_returns(pd.DataFrame(rows, columns=["label", "column", "close"]), ["A", "B"], {"align": align})
    assert info["closes"]["A"].iloc[-1] == 155
    assert pd.isna(info["closes"]["B"].iloc[-1])
    assert len(returns) > 0
