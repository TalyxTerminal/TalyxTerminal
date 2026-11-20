"""Exposure and legs of a portfolio run: gross and net exposure, the turnover, and a long and a short leg drawn in the up and down colours."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "modules/quant"))
from native.strategy_report import Report, price_text, quantity_text  # noqa: E402


def report(kind):
    made = Report.__new__(Report)
    made.kind, made.A, made.rates = kind, 365, {}
    return made


def book():
    days = pd.date_range("2025-01-01", periods=60, freq="D", tz="UTC")
    rng = np.random.default_rng(3)
    closes = pd.DataFrame({"AAA": 100 * np.cumprod(1 + rng.normal(0, .01, 60)), "BBB": 50 * np.cumprod(1 + rng.normal(0, .01, 60))}, index=days)
    held = pd.DataFrame({"AAA": 10.0, "BBB": -20.0}, index=days)
    return days, closes, held


def test_exposure_has_gross_net_turnover_and_two_legs():
    days, closes, held = book()
    equity = pd.Series(10_000.0, index=days)
    made = report("multi")
    # two fills on day 0 worth 1,000 and 1,000, one on day 5 bought and sold the same day (2 × 500): it counts though the holdings end unchanged
    ns = lambda day: int(days[day].value) + 3_600_000_000_000  # noqa: E731
    fills = [{"t": ns(0), "last_qty": 10.0, "last_px": 100.0}, {"t": ns(0), "last_qty": 20.0, "last_px": 50.0},
             {"t": ns(5), "last_qty": 5.0, "last_px": 100.0}, {"t": ns(5), "last_qty": 5.0, "last_px": 100.0}]
    for fill in fills:
        fill["notional"] = fill["last_qty"] * fill["last_px"]
    found = made.exposure(held, closes, equity, fills)
    assert (found["gross"] >= found["net"].abs() - 1e-12).all() and len(found["turnover"]) == 60 and set(found["legs"]) == {"long", "short"}
    assert abs(found["turnover"].iloc[-1]) < 1e-9   # nothing traded in the last month
    # day 9: the window has days 0..9 (10 days, the minimum): (2,000 + 1,000) ÷ 2 ÷ 10,000 over 10 days, × 365
    assert found["turnover"].iloc[9] == pytest.approx(3_000 / 2 / 10_000 / 10 * 365)


def test_the_long_leg_is_drawn_up_and_the_short_leg_down():
    days, closes, held = book()
    made = report("multi")
    found = made.exposure(held, closes, pd.Series(10_000.0, index=days), [])
    figures = made.long_short([], found, closes["AAA"] / closes["AAA"].iloc[0], None)["figures"]
    by_key = {item["meta"]["key"]: item for item in figures["legs"]["data"]}
    assert by_key["long"]["meta"] == {"role": "updown", "key": "long", "side": "up"} and by_key["short"]["meta"] == {"role": "updown", "key": "short", "side": "down"}
    assert by_key["market"]["meta"]["role"] == "benchmark"
    assert {"exposure", "turnover", "legs"} <= set(figures)


def test_a_single_instrument_has_no_legs_figure():
    days, closes, held = book()
    made = report("single")
    found = made.exposure(held, closes, pd.Series(10_000.0, index=days), [])
    assert "legs" not in made.long_short([], found, None, None)["figures"]


def test_the_trade_log_reads_prices_and_quantities_without_a_floats_tail():
    assert price_text(518.4845842837) == "518.48" and price_text(0.0612345) == "0.06123" and price_text(None) == "None"
    assert quantity_text(9.0) == "9" and quantity_text(1200.0) == "1,200" and quantity_text(0.00123456789) == "0.00123457"


def test_a_rankings_orders_on_the_next_bar_belong_to_it_so_its_weights_are_what_filled():
    import talyx_quant as q
    days, closes, held = book()
    held.iloc[:3] = 0.0  # nothing held before the first orders fill
    equity = pd.Series(10_000.0, index=days)
    made = report("multi")
    made.display = {}
    ranking = pd.DataFrame({"ts_available": [days[1].value] * 2 + [days[20].value] * 2, "instrument": ["AAA", "BBB"] * 2,
                            "weight": [0.5, -0.5] * 2, "rank": [2, 1] * 2, "value": [0.6, 0.4] * 2})
    made.strategy = type("S", (), {"reports": lambda self, engine, frame: {"Signal ranking": ranking}})()
    made.engine = made.frame = None
    q._rebalances.clear()
    stamp = lambda day: int(day.value // 1_000_000) + 14 * 3_600_000  # filled at the open, 14:00 UTC
    order = lambda day, name: {"time": stamp(day), "filledAt": stamp(day), "instrument": name, "side": "Buy", "qty": 1, "filled": 1, "price": 1.0, "status": "Filled", "reason": None}
    orders = [order(days[3], "AAA"), order(days[3], "BBB"), order(days[21], "AAA")]  # the ranking of day 1 trades on day 3 (a holiday between)
    rows = made.rebalances(orders, held, closes, equity, [])
    assert [row["count"] for row in rows] == [2, 1]
    assert rows[0]["actual"]["AAA"] > 0 and rows[0]["actual"]["BBB"] < 0, "the weights after its own fills, not 0.00%"
