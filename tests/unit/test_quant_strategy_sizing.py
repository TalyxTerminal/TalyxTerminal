"""What a strategy is allowed to open and what it was refused: Max position and the free balance cap a new position, refused orders are counted, and the costs say what funding they hold."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "modules/quant"))
import talyx_quant as q  # noqa: E402
from native import strategy_presets as P  # noqa: E402
from native.strategy_report import Report, order_counts  # noqa: E402


@pytest.mark.parametrize("risk, equity, stop, price, cap, expected", [
    (1, 10_000, 500, 50_000, 1.0, 0.2),     # risk sizing: 1% of 10,000 lost over a 500 stop is 0.2, worth 10,000: exactly the cap
    (1, 10_000, 100, 50_000, 1.0, 0.2),     # a tight stop would want 1.0 (worth 50,000, 5x equity): Max position 100% holds it to 0.2
    (1, 10_000, 100, 50_000, 0.5, 0.1),     # Max position 50%
    (1, 10_000, 2_000, 50_000, 1.0, 0.05),  # a wide stop wants less than the cap: risk decides
])
def test_max_position_caps_the_size_a_risk_sized_entry_opens(risk, equity, stop, price, cap, expected):
    assert q.risk_size(risk, equity, stop, price, cap) == pytest.approx(expected)


def test_the_breakout_preset_sizes_its_entries_through_the_cap():
    assert "q.risk_size(self.risk, self.equity(), self.stop_atr * atr, close, self.max_position)" in P.BREAKOUT


def test_every_refused_order_is_counted_and_the_liquidation_stops_apart():
    rows = [{"status": status, "tags": ["entry:breakout"]} for status in (["Filled"] * 3 + ["Rejected"] * 5 + ["Canceled"]) * 300]
    # TALYX's own stop at the liquidation price is not the strategy's order: refused or not, it is counted under its own name.
    rows += [{"status": "Rejected", "tags": ["exit:liquidation"]}] * 50
    assert order_counts(rows) == {"Filled": 900, "Rejected": 1500, "Canceled": 300, "Liquidation stop": 50}


@pytest.mark.parametrize("free, price, margin, fee, expected", [
    (10_000, 50_000, 1.0, 0.0005, 10_000 / (50_000 * 1.01 * 1.0005)),  # 1x: the price, its fee and room for the fill coming in a little higher
    (10_000, 50_000, 0.2, 0.0005, 10_000 / (50_000 * 1.01 * 0.2005)),  # 5x: a fifth of the price as margin, the fee on the whole of it
    (0, 50_000, 1.0, 0.0005, 0.0),
])
def test_an_entry_is_never_larger_than_the_free_balance_pays_for(free, price, margin, fee, expected):
    # at Max position 100% and 1x, a size worth exactly the equity cannot also pay its fee, so the venue refused it whole.
    assert q.fit_size(free, price, margin, fee) == pytest.approx(expected)
    assert free == 0 or free >= q.fit_size(free, price, margin, fee) * price * 1.01 * (margin + fee) - 1e-9


def test_the_breakout_preset_sizes_its_entries_within_what_the_account_can_pay():
    assert "self.affordable(self.instrument_id, close)" in P.BREAKOUT


@pytest.mark.parametrize("kind, funding, expected", [
    ("perpetual", False, {"off": True}),   # switched off says off, not a 0.00 that reads as computed
    ("perpetual", True, {}),
    ("spot", True, None),                  # nothing to fund: no row
])
def test_the_funding_row_says_off_when_switched_off_and_is_left_out_without_a_perpetual(kind, funding, expected):
    costs = {"gross": 100.0, "net": 90.0, "fees": 10.0, "spread": 0.0, "impact": 0.0, "borrow": 0.0, "funding": 0.0, "incomplete": []}
    report = SimpleNamespace(config={"instruments": [{"type": kind}]}, invocation={"runSettings": {"costs": {"funding": funding}}}, capital=1000.0)
    rows = {row["name"]: row for row in Report.costs_section(report, {"costs": costs, "checks": {}})["breakdown"]}
    assert (rows["Funding"].get("off") and {"off": True} or {}) == expected if expected is not None else "Funding" not in rows


def test_funding_the_source_has_no_rates_for_is_a_data_note_of_the_run():
    """OKX keeps three months of funding history, so a two-year run reads none for most of it: the run says which time its costs hold
    no funding for, beside its other data notes, not as a tag on Costs."""
    from modules.quant.backend.strategy_views import funding_gaps  # noqa: PLC0415
    work = {"costs": {"incomplete": [{"instrument": "BTC/USDT:USDT", "from": 1731456000000, "to": 1780271999999, "reason": "Market has no funding settlements for this period"}]}}
    assert funding_gaps(work) == ["BTC/USDT:USDT has no funding rates from Nov 13, 2024 to May 31, 2026 (Market has no funding settlements for this period); "
                                  "the costs hold no funding for that time"]
    assert funding_gaps(None) == [] and funding_gaps({"costs": {"incomplete": []}}) == []


def test_an_order_still_on_its_way_to_the_venue_counts_as_open():
    # an entry sent on one bar reaches the venue on the next; the next bar's on_bar saw no open order and no position and sent a second
    # entry, which the venue refused for margin once the first had filled (43 of the 109 refusals of the BTC preset).
    source = Path(q.__file__).read_text(encoding="utf-8")
    body = source[source.index("        def open_orders(self):"):source.index("        def __init_subclass__")]
    assert "orders_inflight(strategy_id=self.strategy_id)" in body


def test_a_stop_the_venue_refuses_because_the_price_is_already_through_it_closes_at_market():
    # the stop sent after the entry fill arrives a bar later; a gap past it got it refused, and the position ran on with no stop.
    assert "def on_order_rejected(self, event):" in P.BREAKOUT and 'self.close(self.instrument_id, reason=why)' in P.BREAKOUT


def test_a_snapshot_already_read_is_not_rebuilt():
    # The equity ring is read again every simulated hour and holds every snapshot so far: building rows for each one each time was
    # a second of a two-year hourly run. A snapshot is known by its event id before its rows are made.
    from native import strategy_equity as Q
    built = []
    original = Q.snapshot_rows
    Q.snapshot_rows = lambda snapshot: built.append(snapshot) or [{"snapshotId": snapshot.event_id}]
    try:
        capture = SimpleNamespace(seen=set(), rows=[])
        for snapshot in [SimpleNamespace(event_id="a"), SimpleNamespace(event_id="b")] * 3:
            Q.EquityCapture.receive(capture, snapshot)
    finally:
        Q.snapshot_rows = original
    assert len(built) == 2 and [row["snapshotId"] for row in capture.rows] == ["a", "b"]


def test_funding_is_charged_on_the_mark_price_at_the_settlement(monkeypatch):
    """funding was charged on the last trade close; the venue charges it on the mark price. 1 BTC long, rate 0.01%: 10.20 on a mark of
    102,000 where the last close was 100,000; on the last close where Market has no mark."""
    import sys

    from modules.quant.native import metrics
    monkeypatch.setitem(sys.modules, "native.metrics", metrics)
    from modules.quant.native import strategy_costs

    class Money:
        def __init__(self, amount, currency):
            self.amount, self.currency = amount, currency
    monkeypatch.setitem(sys.modules, "nautilus_trader.backtest", SimpleNamespace(SimulationModule=object))
    monkeypatch.setitem(sys.modules, "nautilus_trader.model", SimpleNamespace(Money=Money))
    at = 1704096000000
    rows = [{"instrument": "BTC", "time": at, "rate": .0001, "mark": 102_000.0}, {"instrument": "BTC", "time": at + 28_800_000, "rate": .0001, "mark": float("nan")}]
    rates = strategy_costs.funding_rates(rows)
    ledger = SimpleNamespace(entries=[])
    module = strategy_costs.simulation_module({"funding": True}, SimpleNamespace(close=lambda *args: 100_000), ledger, rates, {"BTC": "perpetual"})
    ctx = SimpleNamespace(positions=[SimpleNamespace(instrument_id="BTC", signed_qty=1, settlement_currency="USDT")])
    module.process(at * 1_000_000 - 1, ctx)
    module.process(at * 1_000_000 + 28_800_000 * 1_000_000, ctx)
    assert [entry["amount"] for entry in ledger.entries] == pytest.approx([10.2, 10.0])


def test_an_order_sent_on_the_last_bar_is_unfilled_at_end_not_halted_or_refused():
    # The orders a run sends on its last bar have no later bar to fill on: they were shown as refused ("No bar: halted or not trading") or filled
    # on the bar they were sent at. They are "Unfilled at end"; an order sent before the last bar still waits for the next one.
    import pandas as pd
    from native.strategy_costs import Prices
    from native.strategy_report import AT_END, order_status
    hour = 3_600_000_000_000
    times = [hour * at for at in range(1, 6)]
    frame = pd.DataFrame({"instrument": "BTC", "ts_event": times, "ts_init": times, "barType": "BTC-1-HOUR-LAST-EXTERNAL",
                          "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    prices = Prices(frame, 365)
    assert prices.after_last("BTC", times[-1]) and not prices.after_last("BTC", times[-2])
    assert order_status("REJECTED", True) == AT_END and order_status("ACCEPTED", False) == AT_END
    assert order_status("REJECTED", False) == "Rejected" and order_status("FILLED", False) == "Filled" and order_status("CANCELED", False) == "Canceled"
