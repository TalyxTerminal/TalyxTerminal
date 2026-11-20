"""Account curves retain financial scope and imported bars become available at close."""
from array import array
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from modules.quant.native.strategy_equity import (
    equity_reports,
    observed_day_sampling,
    revalue,
    snapshot_rows,
)


def row(day, equity, currency="USD", valuation="complete"):
    return {"account": "SIM-001", "currency": currency, "timestampNs": str(pd.Timestamp(day, tz="UTC").value),
            "availableAtNs": "0", "snapshotId": day, "equity": str(equity), "valuation": valuation}


def daily_reports(rows, periods=252):
    """Every day with a mark is an observed day."""
    days = pd.DatetimeIndex(sorted({pd.Timestamp(int(item["timestampNs"]), tz="UTC").normalize() for item in rows}))
    return equity_reports(rows, {"dates": days, "annualization": periods, "basis": "Observed market-data days (UTC)"})


def test_equity_and_drawdown_do_not_compound_position_returns_or_combine_currencies():
    reports, summaries = daily_reports([row("2025-01-01", 100), row("2025-01-02", 120), row("2025-01-03", 90),
        row("2025-01-01", 2, "BTC"), row("2025-01-02", 3, "BTC")])
    usd, btc = summaries
    assert usd["finalEquity"] == "90" and usd["totalReturn"] == pytest.approx(-.1)
    assert usd["maximumDrawdown"] == pytest.approx(-.25)
    assert btc["finalEquity"] == "3" and btc["totalReturn"] == pytest.approx(.5)
    assert len([key for key in reports if key.startswith("Account daily returns")]) == 2


def test_missing_valuation_withholds_performance_and_downstream_returns():
    reports, summaries = daily_reports([row("2025-01-01", 100), row("2025-01-02", 50, valuation="incomplete")])
    assert summaries[0]["totalReturn"] is None and summaries[0]["dailySharpe"] is None
    assert not any(key.startswith("Account daily returns") for key in reports)
    assert pd.isna(reports["Account equity"].drawdown.iloc[-1])


def test_topic_without_market_keeps_return_and_drawdown_but_no_annualized_sharpe():
    _, summaries = daily_reports([row("2025-01-01", 100), row("2025-01-02", 120), row("2025-01-03", 90)], None)
    assert summaries[0]["totalReturn"] == pytest.approx(-.1) and summaries[0]["maximumDrawdown"] == pytest.approx(-.25)
    assert summaries[0]["dailySharpe"] is None and summaries[0]["annualizationPeriods"] is None
    assert "not annualized" in summaries[0]["annualization"]


def test_exchange_sharpe_ignores_timer_only_weekends_but_keeps_zero_return_sessions():
    sessions=pd.bdate_range("2024-01-01",periods=200)
    amounts={day:100+index+index%3 for index,day in enumerate(sessions)}
    rows=[];last=100
    for day in pd.date_range(sessions[0],sessions[-1]):
        last=amounts.get(day,last)
        rows.append(row(str(day.date()),last))
    sampling=observed_day_sampling({"instruments":[{"type":"equity"}]},pd.DataFrame({"ts_init":[str(day.tz_localize("UTC").value) for day in sessions]}),252)
    reports,summaries=equity_reports(rows,sampling)
    assert summaries[0]["dailyObservations"]==199
    assert summaries[0]["annualizationPeriods"]==252
    assert len(reports["Account equity"])>200  # The full native curve is retained.
    expected=pd.Series([amounts[day] for day in sessions]).pct_change().dropna()
    assert summaries[0]["dailySharpe"]==pytest.approx(expected.mean()/expected.std(ddof=1)*252**.5)


def test_crypto_uses_365_and_missing_observed_day_is_not_forward_filled():
    days=pd.date_range("2024-01-01",periods=4,tz="UTC")
    sampling=observed_day_sampling({"instruments":[{"type":"perpetual"}]},pd.DataFrame({"ts_init":[str(day.value) for day in days]}),365)
    _,summaries=equity_reports([row("2024-01-01",100),row("2024-01-02",110),row("2024-01-03",105),row("2024-01-04",120)],sampling)
    assert summaries[0]["annualizationPeriods"]==365
    expected=pd.Series([100,110,105,120]).pct_change().dropna()
    assert summaries[0]["dailySharpe"]==pytest.approx(expected.mean()/expected.std(ddof=1)*365**.5)
    _,missing=equity_reports([row("2024-01-01",100),row("2024-01-02",110),row("2024-01-04",120)],sampling)
    assert missing[0]["dailyObservations"]==1
    assert "1 observed days" in missing[0]["warning"]


def test_snapshot_preserves_exact_native_money_and_nanoseconds():
    money = SimpleNamespace(currency="USD", as_decimal=lambda: Decimal("100.123456789"))
    snapshot = SimpleNamespace(account_id="A-001", total_equity=[money], ts_event=1700000000000000123,
        ts_init=1700000000000000124, event_id="a", is_stale=False, unpriced_instruments=[], stale_instruments=[], stale_currencies=[])
    result = snapshot_rows(snapshot)[0]
    assert result["timestampNs"] == "1700000000000000123" and result["equity"] == "100.123456789"


def test_full_native_ring_withholds_full_period_metrics_and_return_handoff(monkeypatch):
    import modules.quant.native.strategy_equity as module
    monkeypatch.setattr(module, "NATIVE_SNAPSHOT_CAPACITY", 3)
    snapshots = []
    for i, amount in enumerate([100, 120, 90]):
        money = SimpleNamespace(currency="USD", as_decimal=lambda amount=amount: Decimal(amount))
        snapshots.append(SimpleNamespace(account_id="A-001", total_equity=[money], ts_event=1700000000000000123 + i * 86_400_000_000_000,
            ts_init=1700000000000000123 + i * 86_400_000_000_000, event_id=str(i), is_stale=False, unpriced_instruments=[], stale_instruments=[], stale_currencies=[]))
    engine = SimpleNamespace(list_venues=lambda: ["SIM"], cache=SimpleNamespace(account_for_venue=lambda _: SimpleNamespace(id="A-001"), instruments=lambda: []),
        portfolio=SimpleNamespace(snapshots=lambda _: snapshots))
    sampling = {"dates": pd.DatetimeIndex(pd.to_datetime([1700000000000000123], unit="ns", utc=True).normalize()), "annualization": 252, "basis": "Observed market-data days (UTC)"}
    reports, summaries = module.capture_reports(engine, sampling)
    assert summaries[0]["totalReturn"] is None and summaries[0]["maximumDrawdown"] is None
    assert summaries[0]["warning"] and pd.isna(reports["Account performance"].totalReturn.iloc[0])
    assert "Account equity" in reports and not any(key.startswith("Account daily returns") for key in reports)


def test_spot_balances_are_valued_at_the_last_price_so_equity_return_drawdown_and_sharpe_agree():
    hour = 3_600_000_000_000
    start = pd.Timestamp("2024-01-01", tz="UTC").value
    closes = [100.0, 110.0, 99.0, 99.0, 120.0, 120.0]
    marks = {"BTC/USDT.X": (array("q", [start + i * hour for i in range(6)]), array("d", closes))}
    # Buy 10 BTC at 110 in hour 1, sell them at 120 in hour 4; native total equity would list USDT and BTC apart.
    cash = {0: Decimal("2000"), 1: Decimal("900"), 4: Decimal("2100")}
    held = {0: Decimal("0"), 1: Decimal("10"), 4: Decimal("0")}
    balances = {"SIM-001": [(start + hour * i, {"USDT": cash[i], "BTC": held[i]}) for i in (0, 1, 4)]}
    unfolded = [row("2024-01-01", 2000)]
    rows = revalue(unfolded, {"BTC": [("USDT", "BTC/USDT.X")]}, marks, balances, {"USDT": 2, "BTC": 8})
    assert {item["currency"] for item in rows} == {"USDT"} and len(rows) == 6
    expected = [2000, 2000, 1890, 1890, 2100, 2100]
    assert [item["equityNumeric"] for item in rows] == expected
    assert all(item["valuation"] == "complete" for item in rows)
    _, summaries = daily_reports(rows, 365)
    curve = pd.Series(expected, dtype=float)
    assert summaries[0]["totalReturn"] == pytest.approx(.05)
    assert summaries[0]["maximumDrawdown"] == pytest.approx((curve / curve.cummax() - 1).min())
    assert summaries[0]["annualizationPeriods"] == 365


def test_spot_balance_without_a_price_yet_withholds_performance():
    marks = {"ETH/USDT.X": (array("q", [0]), array("d", [10.0])), "BTC/USDT.X": (array("q", [2_000_000_000_000]), array("d", [100.0]))}
    pairs = {"ETH": [("USDT", "ETH/USDT.X")], "BTC": [("USDT", "BTC/USDT.X")]}
    balances = {"SIM-001": [(0, {"USDT": Decimal("1000"), "ETH": Decimal("0"), "BTC": Decimal("1")})]}
    rows = revalue([], pairs, marks, balances, {"USDT": 2, "BTC": 8, "ETH": 8})
    assert rows[0]["valuation"] == "incomplete" and rows[0]["missingInstruments"] == "BTC/USDT.X"
    assert rows[-1]["valuation"] == "complete" and float(rows[-1]["equity"]) == 1100
    _, summaries = daily_reports(rows, 365)
    assert summaries[0]["totalReturn"] is None


def test_base_currency_with_several_pairs_is_valued_through_the_currency_the_account_started_with():
    hour = 3_600_000_000_000
    marks = {"BTC/USDT.X": (array("q", [0, hour]), array("d", [100.0, 110.0])), "BTC/USDC.X": (array("q", [0, hour]), array("d", [99.0, 120.0]))}
    pairs = {"BTC": [("USDT", "BTC/USDT.X"), ("USDC", "BTC/USDC.X")]}
    precision = {"USDT": 2, "USDC": 2, "BTC": 8}
    balances = {"SIM-001": [(0, {"USDT": Decimal("1000"), "BTC": Decimal("0")}), (0, {"USDT": Decimal("0"), "BTC": Decimal("10")})]}
    rows = revalue([], pairs, marks, balances, precision)
    assert [(item["currency"], item["equityNumeric"], item["valuation"]) for item in rows] == [("USDT", 1000.0, "complete"), ("USDT", 1100.0, "complete")]
    # Cash in both quote currencies at the start: which one the BTC belongs to is not known.
    both = {"SIM-001": [(0, {"USDT": Decimal("500"), "USDC": Decimal("500"), "BTC": Decimal("10")})]}
    rows = revalue([], pairs, marks, both, precision)
    assert {item["valuation"] for item in rows} == {"incomplete"} and rows[0]["missingInstruments"] == "BTC"
    assert {item["currency"] for item in rows} == {"USDT", "USDC", "BTC"}
    _, summaries = daily_reports(rows, 365)
    assert all(item["totalReturn"] is None for item in summaries)


def test_a_base_currency_quoted_in_another_base_currency_is_valued_through_the_chain():
    marks = {"ETH/BTC.X": (array("q", [0]), array("d", [0.5])), "BTC/USDT.X": (array("q", [0]), array("d", [100.0]))}
    pairs = {"ETH": [("BTC", "ETH/BTC.X")], "BTC": [("USDT", "BTC/USDT.X")]}
    balances = {"SIM-001": [(0, {"USDT": Decimal("1000"), "BTC": Decimal("1"), "ETH": Decimal("4")})]}
    rows = revalue([], pairs, marks, balances, {"USDT": 2, "BTC": 8, "ETH": 8})
    assert [(item["currency"], item["equityNumeric"], item["valuation"]) for item in rows] == [("USDT", 1300.0, "complete")]
    circle = {"BTC": [("USDT", "BTC/USDT.X")], "USDT": [("BTC", "USDT/BTC.X")]}
    marks["USDT/BTC.X"] = (array("q", [0]), array("d", [0.01]))
    rows = revalue([], circle, marks, balances, {"USDT": 2, "BTC": 8, "ETH": 8})
    assert {item["valuation"] for item in rows} == {"incomplete"}


def test_days_with_engine_data_are_the_observed_days_of_a_code_step_and_weekends_are_not():
    from modules.quant.native.strategy_equity import engine_day_sampling, record_marks, release
    bar = type("Bar", (), {})
    friday, monday = pd.Timestamp("2024-01-05 21:00", tz="UTC"), pd.Timestamp("2024-01-08 21:00", tz="UTC")
    items = []
    for stamp, close in ((friday, 10.0), (monday, 11.0)):
        item = bar()
        item.ts_init, item.close = stamp.value, close
        item.bar_type = SimpleNamespace(instrument_id="A.X")
        items.append(item)
    engine = SimpleNamespace()
    record_marks(engine, items)
    sampling = engine_day_sampling(engine, 252)
    assert list(sampling["dates"]) == [friday.normalize(), monday.normalize()] and sampling["annualization"] == 252
    release(engine)
    # Weekend marks stay in the equity curve but never become zero-return days.
    rows = [row(str(day.date()), 100 + index) for index, day in enumerate(pd.date_range("2024-01-05", "2024-01-08"))]
    rows = [{**item, "equity": str(100 if item["snapshotId"] in ("2024-01-05", "2024-01-06", "2024-01-07") else 101)} for item in rows]
    _, summaries = equity_reports(rows, sampling)
    assert summaries[0]["dailyObservations"] == 1
