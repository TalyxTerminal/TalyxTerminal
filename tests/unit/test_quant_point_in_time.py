"""What a past date knew, in research and backtests: an industry as classified on each date (a stock moved from A to B in 2022 is in A
in 2018), and a name whose prices stop (100, 110, then delisted) is out at its last close in every path, the move to it counted."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "modules/quant"))
import talyx_quant as q  # noqa: E402
from native import factor_work as W  # noqa: E402
from native import formula as F  # noqa: E402
from native import metrics as M  # noqa: E402

from modules.quant.backend import object_data, research_inputs  # noqa: E402
from modules.quant.backend.factor_data import FactorData  # noqa: E402

DAY = 86_400_000


def ms(day):
    return int(pd.Timestamp(day, tz="UTC").value // 1_000_000)


# ── industry by date ───────────────────────────────────────────────────────

MOVED = [[ms("2016-03-28"), ms("2022-03-28"), "A"], [ms("2022-03-28"), None, "B"]]  # A from 2016, B from the 2022-03-28 update on


def test_a_name_moved_from_a_to_b_in_2022_is_in_a_in_2018_and_in_b_in_2023():
    labels = np.asarray([ms("2015-06-01"), ms("2018-06-01"), ms("2022-03-25"), ms("2022-03-28"), ms("2023-06-01")])
    table = W.industry_table(labels, ["X", "Y"], {"X": MOVED, "Y": [[None, None, "C"]]})
    assert table[:, 0].tolist() == [None, "A", "A", "B", "B"]  # nothing before the first known date
    assert table[:, 1].tolist() == ["C"] * 5  # a source with only its current one: every date
    assert research_inputs.industry_at([tuple(row) for row in MOVED], ms("2018-06-01")) == "A"
    assert research_inputs.industry_at([tuple(row) for row in MOVED], ms("2023-06-01")) == "B"


def test_data_frozen_by_0_1_with_one_label_per_name_still_reads():
    labels = np.asarray([ms("2018-06-01"), ms("2023-06-01")])
    assert W.industry_table(labels, ["X"], {"X": "Banks"})[:, 0].tolist() == ["Banks", "Banks"]


def frames(values, industry):
    index = pd.to_datetime([ms("2018-06-01"), ms("2023-06-01")], unit="ms", utc=True)
    columns = ["X", "Y", "Z", "W"]
    return pd.DataFrame(values, index=index, columns=columns), pd.DataFrame(industry, index=index, columns=columns, dtype=object)


def test_neutralizing_by_industry_uses_each_days_industry():
    # X is with Y in A in 2018 and with Z in B in 2023: each day its value less its own industry's mean that day.
    values, industry = frames([[1.0, 3.0, 10.0, 20.0], [1.0, 3.0, 10.0, 20.0]], [["A", "A", "B", "B"], ["B", "A", "B", "C"]])
    out = F.neutralize(values, industry).to_numpy()
    assert out[0].tolist() == pytest.approx([-1.0, 1.0, -5.0, 5.0])
    assert out[1].tolist() == pytest.approx([-4.5, 0.0, 4.5, 0.0])  # A and C hold one name each that day


def test_ranking_and_grouping_inside_each_industry_use_each_days_industry():
    values, industry = frames([[1.0, 3.0, 10.0, 20.0], [1.0, 3.0, 10.0, 20.0]], [["A", "A", "B", "B"], ["B", "A", "B", "C"]])
    ranked = F._cross("IndRank", [values], F.Context({"$x": values}, industry)).to_numpy()
    assert ranked[0].tolist() == pytest.approx([.5, 1.0, .5, 1.0]) and ranked[1].tolist() == pytest.approx([.5, 1.0, 1.0, 1.0])
    groups = W.group_of(values.to_numpy(), {"grouping": {"method": "equal", "count": 2, "withinIndustry": True}}, industry)
    assert groups.tolist() == [[1, 2, 1, 2], [1, 1, 2, 1]]


def test_a_panel_keeps_each_days_industry_and_codes_it():
    labels = np.asarray([ms("2018-06-01"), ms("2023-06-01")])
    fields = {name: np.ones((2, 2)) for name in ("open", "high", "low", "close", "volume", "rawClose", "rawVolume")}
    panel = W.Panel(labels, ["X", "Y"], fields, np.ones((2, 2), dtype=bool), {"annualization": 252},
                    industry=W.industry_table(labels, ["X", "Y"], {"X": MOVED}))
    codes, names = panel.industry_codes("Unknown")
    assert [[names[code] for code in row] for row in codes] == [["A", "Unknown"], ["B", "Unknown"]]
    assert panel.truncate(ms("2020-01-01")).industry.to_numpy().tolist() == [["A", None]]
    assert q.ResearchData.from_panel(panel).industry.iloc[:, 0].tolist() == ["A", "B"]


def test_group_neutral_returns_take_off_each_days_industry():
    # Every name moves exactly with its industry of the day; X changes industry and its relative return stays zero.
    days = 4
    returns = np.array([[0, 0, 0, 0], [.01, .01, .03, .03], [.03, .01, .03, .05], [.03, .01, .03, .05]])
    close = np.cumprod(1 + returns, axis=0) * 10
    labels = np.arange(days) * DAY
    industry = np.array([["A", "A", "B", "B"]] * 2 + [["B", "A", "B", "C"]] * 2, dtype=object)
    fields = {name: close.copy() for name in ("open", "high", "low", "close", "rawClose")}
    fields.update(volume=np.ones((days, 4)), rawVolume=np.ones((days, 4)))
    panel = W.Panel(labels, list("XYZW"), fields, np.ones((days, 4), dtype=bool), {"annualization": 252}, industry=industry)
    settings = {"holding": 1, "delay": 0, "costBps": 0, "borrowPct": 0, "demeaned": True, "groupAdjust": True}
    values = np.ones((days, 4))
    groups = np.tile([1, 1, 2, 2], (days, 1))
    found = W.returns_of(panel, groups, 2, settings, values, codes=panel.industry_codes("Unknown")[0])
    assert np.nanmax(np.abs(found["relative"])) < 1e-12


# ── a name whose prices stop ───────────────────────────────────────────────


def test_prices_100_110_then_delisted_are_out_at_110_in_forward_returns():
    close = np.array([[100.0, 50.0], [110.0, 50.0], [np.nan, 50.0], [np.nan, 50.0]])
    forward = M.forward_returns(close, 2)
    assert forward[0, 0] == pytest.approx(.10)  # held from 100 to its last close, 110: the delisting day's move counts
    assert forward[1, 0] == pytest.approx(0.0)  # held from its last close: nothing after
    assert np.isnan(forward[2:]).all()  # past the end of the data


def test_a_book_holding_a_delisted_name_earns_the_move_to_its_last_close_and_nothing_after():
    close = np.array([[100.0, 50.0], [110.0, 50.0], [np.nan, 50.0], [np.nan, 50.0]])
    fields = {name: close.copy() for name in ("open", "high", "low", "close", "rawClose")}
    fields.update(volume=np.ones((4, 2)), rawVolume=np.ones((4, 2)))
    panel = W.Panel(np.arange(4) * DAY, ["X", "Y"], fields, np.ones((4, 2), dtype=bool), {"annualization": 252})
    settings = {"holding": 1, "delay": 0, "costBps": 0, "borrowPct": 0, "demeaned": False, "groupAdjust": False}
    groups = np.array([[2, 1]] * 4)
    found = W.returns_of(panel, groups, 2, settings, np.ones((4, 2)))
    assert found["gross"][1] == pytest.approx(.10) and found["gross"][2] == pytest.approx(0.0)


def test_a_strategy_sells_a_delisted_name_at_its_last_close(monkeypatch):
    """X's prices stop after its third bar while Y trades on: X has ended, and the order selling it is priced at its last close and dated
    at that bar's close, though it reaches the venue later."""
    from native.strategy_costs import Ledger, Prices, fill_model
    day = 86_400_000_000_000
    times = [day * at for at in range(1, 6)]
    frame = pd.DataFrame({"instrument": ["X"] * 3 + ["Y"] * 5, "ts_event": times[:3] + times, "ts_init": times[:3] + times,
                          "barType": "X-1-DAY-LAST-EXTERNAL", "open": [100.0, 104.0, 108.0] + [50.0] * 5, "high": 120.0, "low": 90.0,
                          "close": [102.0, 106.0, 110.0] + [50.0] * 5, "volume": 1000.0})
    prices = Prices(frame, 252)
    assert prices.ended("X") == 2 and prices.ended("Y") is None

    class Book:
        def __init__(self, *_args):
            self.orders = []

        def add(self, order, *_args):
            self.orders.append(order)

    class Price(float):
        def as_double(self):
            return float(self)

    class Size(float):
        def as_double(self):
            return float(self)

        def __str__(self):
            return "10"

    monkeypatch.setitem(sys.modules, "nautilus_trader.execution", SimpleNamespace(FillModel=object))
    monkeypatch.setitem(sys.modules, "nautilus_trader.model", SimpleNamespace(
        BookOrder=lambda side, price, size, number: (side, float(price), size), BookType=SimpleNamespace(L2_MBP="L2"), OrderBook=Book,
        OrderSide=SimpleNamespace(BUY="BUY", SELL="SELL"), Quantity=SimpleNamespace(from_str=float)))
    ledger = Ledger()
    model = fill_model({"halfSpreadBps": 0, "impact": 0}, "nextOpen", prices, ledger, lambda: times[4])
    order = SimpleNamespace(order_type="OrderType.MARKET", client_order_id="O-1", ts_init=times[3], side="SELL",
                            leaves_qty=Size(10))
    instrument = SimpleNamespace(id="X", make_price=Price, price_increment="0.01")
    monkeypatch.setitem(q._delisted_exits, "O-1", times[2])
    book = model.get_orderbook_for_fill_simulation(instrument, order, None, None)
    assert [price for _side, price, _size in book.orders] == [110.0]
    assert ledger.moments[("O-1", times[4])] == times[2] and "O-1" not in ledger.unfilled
    monkeypatch.delitem(q._delisted_exits, "O-1")
    refused = model.get_orderbook_for_fill_simulation(instrument, SimpleNamespace(**{**vars(order), "client_order_id": "O-2"}), None, None)
    assert refused.orders == [] and ledger.unfilled["O-2"].startswith("No bar")  # any other order finds no bar to fill on


def test_sessions_after_a_names_last_bar_are_its_end_not_gaps():
    schedule = [(at * DAY, at * DAY + 1) for at in range(1, 8)]
    frame = pd.DataFrame({"time": [DAY, 3 * DAY, 4 * DAY], "closeAt": [DAY + 1, 3 * DAY + 1, 4 * DAY + 1]})
    item = {"coverage": {"exportedRows": 3}, "instrument": {"displaySymbol": "X"}, "series": {"timeframe": "1d"}}
    object_data.record_missing_bars(None, item, frame, 10 * DAY, schedule)
    assert item["coverage"]["missingDays"] == ["1970-01-03"] and item["coverage"]["endedAt"] == 4 * DAY + 1


# ── prices adjusted unless set otherwise ───────────────────────────────────


def test_research_prices_left_unset_are_adjusted_for_splits_and_dividends(tmp_path):
    data = FactorData(None, tmp_path, SimpleNamespace(), None)
    chosen = data.selection({"symbols": [{"instrument": {"instrumentId": "a"}}], "timeframe": "1d", "since": "2020-01-01", "until": "2020-12-31"})
    assert chosen["adjustment"] == "split_dividend"
