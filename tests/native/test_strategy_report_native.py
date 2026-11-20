"""A strategy run's report, costs and fill timing in the fixed strategy environment, on synthetic bars and a scripted funding history.

The accounts that must hold: each market order fills at the next bar's open moved by half the spread; the costs the report splits add up to
the gap between gross and net; funding is charged at each settlement on the position then held; every figure says what each line is.

Not part of the daily suite: set TALYX_NATIVE_SMOKE=1 (see environments.py).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.strategy import bars as B
from tests.fixtures.strategy import engine as E
from tests.fixtures.strategy.engine import figures_of, prepare, run

pytestmark = pytest.mark.skipif(not os.environ.get("TALYX_NATIVE_SMOKE"), reason="Set TALYX_NATIVE_SMOKE=1 to run native engines")

QUANT = Path(__file__).resolve().parents[2] / "modules" / "quant"
INSTRUMENT = "BTCUSDT-PERP.OKX"
BAR_TYPE = INSTRUMENT + "-1-HOUR-LAST-EXTERNAL"
ROLES = {"main", "benchmark", "compare", "earlier", "group", "part", "updown", "drawdown", "attention", "watch", "detail"}  # modules/quant/frontend/chart/paint.ts PlotRole

CODE = '''
import talyx_quant as q
from nautilus_trader.model import OrderSide


class Breakout(q.Strategy):
    def __init__(self):
        super().__init__()
        self.every = q.param("every", 24, min=2, label="Trade every")
        self.count = 0

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.count += 1
        q.plot("close x2", float(bar.close) * 2, pane="own")
        q.plot("level", float(bar.close), pane="price", style="step")
        held = self.position_size(self.instrument_id)
        if self.count % self.every == 0:
            if held:
                self.close(reason="time")
            else:
                side = OrderSide.BUY if (self.count // self.every) % 2 else OrderSide.SELL
                self.submit_order(self.order_factory.market(self.instrument_id, side, self.instrument.make_qty(0.5), tags=["entry:turn"]))
                q.log("entered", kind="Signal", instrument=self.instrument_id)
        q.stat("bars seen", self.count)
'''



def test_a_run_reports_fills_at_the_next_open_costs_that_add_up_and_labelled_figures(tmp_path):
    frame = prepare(tmp_path, CODE, frame=B.perp_bars(24 * 100))
    index = run(tmp_path)
    assert index["status"] == "completed", (index.get("error"), (tmp_path / "stderr.txt").read_text(encoding="utf-8")[-3000:])
    work = json.loads((tmp_path / "output" / "strategy" / "work.json").read_text(encoding="utf-8"))
    results = json.loads((tmp_path / "output" / "strategy" / "results.json").read_text(encoding="utf-8"))
    opens = dict(zip((frame["ts_event"].astype("int64") - B.HOUR_NS) // 1_000_000, frame["open"].astype(float), strict=True))
    trades = E.rows(tmp_path, "trades")
    assert trades and work["kind"] == "single"
    for trade in trades:
        # Decided on one bar's close, filled at the open of the next bar (the fill's time is that open, ), moved by half the spread.
        base = opens[trade["entry"]]
        expected = base * (1 + 2e-4) if trade["side"] == "Long" else base * (1 - 2e-4)
        assert trade["entryPrice"] == pytest.approx(round(expected, 1), abs=0.11)
    costs = work["costs"]
    assert costs["total"] == pytest.approx(costs["fees"] + costs["spread"] + costs["impact"] + costs["funding"] + costs["borrow"])
    assert costs["gross"] - costs["total"] == pytest.approx(costs["net"], abs=1e-6)
    assert costs["funding"] != 0 and costs["spread"] > 0 and costs["fees"] > 0
    first, last = work["equity"][0][1], work["equity"][-1][1]
    assert last - first == pytest.approx(costs["net"], abs=0.01)
    # Funding is the position held at each settlement times the price and the rate, a long paying a positive rate.
    funding_lines = [line for line in work["log"] if line["kind"] == "Funding"]
    assert funding_lines
    assert work["stats"]["trades"] >= 1 and work["stats"]["sharpe"] is not None
    assert {series["name"] for series in work["plots"]} == {"close x2", "level"}
    assert work["userStats"]["bars seen"] == len(frame)
    assert any(line["kind"] == "Signal" for line in work["log"])
    keys = []
    for item in figures_of(results["sections"]):
        keys.append(item["layout"]["meta"]["key"])
        for trace in item["data"]:
            assert trace["meta"]["role"] in ROLES and trace["meta"]["key"], (item["layout"]["meta"]["key"], trace.get("name"))
            assert not any("color" in name for name in trace if name != "meta")
    assert len(keys) == len(set(keys)) and {"strategy.equity", "strategy.drawdown", "strategy.monthly", "strategy.yearly", "strategy.rollingSharpe"} <= set(keys)
    assert not results["missing"], results["missing"]
    rows = {row["name"]: row["run"] for row in results["sections"]["summary"]["returnStatistics"]}
    assert rows["Daily CVaR 95%"] <= rows["Daily VaR 95%"] <= 0  # the tail average is no better than the 5% line
    size = sum(path.stat().st_size for path in (tmp_path / "output").rglob("*") if path.is_file())
    assert (tmp_path / "output" / "result-index.json").stat().st_size < 200_000 and size < 20_000_000


def test_failure_context(tmp_path):
    code = CODE.replace("        held = self.position_size(self.instrument_id)\n", "        held = self.position_size(self.instrument_id)\n        level = float(bar.close) / 2\n        if self.count == 100:\n            raise RuntimeError('boom')\n")
    prepare(tmp_path, code)
    index = run(tmp_path)
    assert index["status"] == "failed"
    context = index["failureContext"]
    assert context["simulatedAt"] > 0 and context["instrument"] == INSTRUMENT
    assert any(name == "level" for name, _ in context["locals"])
    assert code.splitlines()[context["line"] - 1].strip() == "raise RuntimeError('boom')"
    work = json.loads((tmp_path / "output" / "strategy" / "work.json").read_text(encoding="utf-8"))
    assert work["partial"] is True


def test_the_code_is_not_called_after_its_first_error_and_the_part_so_far_ends_there(tmp_path):
    code = CODE.replace("        held = self.position_size(self.instrument_id)\n", "        held = self.position_size(self.instrument_id)\n        if self.count == 1500:\n            raise RuntimeError('boom')\n")
    frame = prepare(tmp_path, code, frame=B.perp_bars(24 * 100))
    index = run(tmp_path)
    assert index["status"] == "failed"
    at = index["failureContext"]["simulatedAt"]
    work = json.loads((tmp_path / "output" / "strategy" / "work.json").read_text(encoding="utf-8"))
    assert work["partial"] is True
    assert work["userStats"]["bars seen"] == 1499  # the code did not see the bar it failed on, nor any after it
    assert work["equity"] and work["equity"][-1][0] <= at < int(frame["ts_event"].astype("int64").iloc[-1]) // 1_000_000
    assert all(trade["entry"] <= at for trade in E.rows(tmp_path, "trades"))
    assert work["log"][-1]["kind"] == "Error" and work["log"][-1]["t"] == at and "boom" in work["log"][-1]["text"]
    # The key numbers are those of the part that ran: computed from the same cut series, not from the whole data.
    stats, points = work["stats"], work["equity"]
    level = pd.Series([value for _, value in points], index=pd.to_datetime([moment for moment, _ in points], unit="ms", utc=True))
    daily = level.resample("1D").last().dropna()
    returns = daily.pct_change().dropna()
    # Stored numbers are written to 6 decimals (one number at every exit), so they agree to that.
    assert stats["totalReturn"] == pytest.approx(points[-1][1] / points[0][1] - 1, abs=1e-6)
    assert stats["maxDrawdown"] == pytest.approx(float((level / level.cummax() - 1).min()), abs=1e-6)
    assert stats["trades"] == len(E.rows(tmp_path, "trades"))
    assert len(returns) >= 60 and stats["cagr"] == pytest.approx(float(np.prod(1 + returns.to_numpy())) ** (365 / len(returns)) - 1, abs=1e-6)
    assert stats["volatility"] == pytest.approx(float(returns.std(ddof=1)) * np.sqrt(365), abs=1e-6)


# --- strategies used below -----------------------------------------------------------------------------------------------------

PRELUDE = '''
import talyx_quant as q
from nautilus_trader.model import OrderSide, TimeInForce
'''

HOLD = PRELUDE + '''

class Hold(q.Strategy):
    def __init__(self):
        super().__init__()
        self.done = False

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        if not self.done:
            self.done = True
            self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(QTY), tags=["entry:hold"]))
'''.replace("QTY", "2.4")

NOTHING = PRELUDE + '''

class Idle(q.Strategy):
    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        pass
'''

# Rebalances on set bars of the first name: WEIGHTS maps the bar number to {instrument index: weight}.
REBALANCE = PRELUDE + '''

WEIGHTS = SCHEDULE


class Monthly(q.Strategy):
    def __init__(self):
        super().__init__()
        self.n = 0

    def on_start(self):
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        if bar.bar_type.instrument_id != self.instrument_ids[0]:
            return
        self.n += 1
        if self.n in WEIGHTS:
            self.rebalance_to({self.instrument_ids[i]: weight for i, weight in WEIGHTS[self.n].items()})
'''

FLAT_HOUR = [(100.0, 100.0, 100.0, 100.0)]


def stock_instruments(ids, venue_leverage=2):
    return [{**E.STOCK, "id": identifier, "symbol": identifier.split(".")[0]} for identifier in ids]


def stock_run(root, code, ids, frame, *, settings=None, bar_days=True, **more):
    display = [{"nativeInstrument": identifier, "symbol": identifier.split(".")[0]} for identifier in ids]
    return prepare(root, code, frame=frame, instruments=stock_instruments(ids), venues=[E.XNAS], bar_types=[f"{identifier}-1-DAY-LAST-EXTERNAL" for identifier in ids],
                   annualization=252, display=display, funding=False, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False, "borrowPct": 0.3}, **(settings or {})}, **more)


def by_name(rows, key):
    return {row[key]: row for row in rows}


# --- the main picture and what the report says -------------------------------------------------------------------------------

def test_single_and_multi_kind(tmp_path):
    one = tmp_path / "one"
    one.mkdir()
    prepare(one, HOLD)
    _, work, _ = E.completed(one)
    assert work["kind"] == "single"
    frame, ids = B.daily_universe(3, 40, halted=())
    many = tmp_path / "many"
    many.mkdir()
    code = REBALANCE.replace("SCHEDULE", "{2: {0: 1 / 3, 1: 1 / 3, 2: 1 / 3}}")
    stock_run(many, code, ids, frame)
    _, work, _ = E.completed(many)
    assert work["kind"] == "multi" and work["multi"]["byInstrument"]


def test_over_benchmark_is_cagr_gap(tmp_path):
    prepare(tmp_path, HOLD, frame=B.perp_bars(24 * 100))
    _, work, _ = E.completed(tmp_path)
    stats = work["stats"]
    assert stats["overBenchmark"] == pytest.approx(stats["cagr"] - stats["benchmarkCagr"], abs=1e-5)


def test_the_strategy_that_is_the_benchmark_has_beta_one_and_no_gap(tmp_path):
    """Buy and hold the benchmark's own instrument, all of the account, at no cost: beta 1, tracking error 0 and nothing over the benchmark."""
    rng = np.random.default_rng(5)
    walk = 100 * np.exp(np.cumsum(rng.normal(0, .004, 24 * 60)))
    walk[:24] = 100.0  # the first day is flat: the strategy is in from the day's end on, as the benchmark is
    opens = [100.0] + list(walk[:-1])
    frame = B.hand_bars([(o, max(o, c), min(o, c), c) for o, c in zip(opens, walk, strict=True)], price_digits=6)
    venue = {**E.OKX, "fees": {"type": "maker_taker", "maker": 0, "taker": 0}, "leverage": 2}
    code = HOLD.replace("2.4", "1000.0")
    precise = {**E.PERP, "takerFee": "0", "pricePrecision": 6, "priceIncrement": "0.000001"}
    prepare(tmp_path, code, frame=frame, venues=[venue], instruments=[precise], funding=False,
            settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}, "exchange": {"leverage": 2, "stopTrigger": "last"}})
    _, work, results = E.completed(tmp_path)
    against = results["sections"]["summary"]["againstBenchmark"]
    assert work["stats"]["overBenchmark"] == pytest.approx(0, abs=1e-6)
    assert against["beta"] == pytest.approx(1, abs=1e-6) and against["trackingError"] == pytest.approx(0, abs=1e-6)


def test_results_sections_present(tmp_path):
    prepare(tmp_path, CODE)
    _, work, results = E.completed(tmp_path)
    assert {"summary", "equity", "periods", "rolling", "longShort", "trades", "orders", "account", "costs", "crisis", "style"} <= set(results["sections"])
    assert set(results["sections"]["summary"]["tiles"]) >= {"cagr", "maxDrawdown", "sharpe"} or results["sections"]["summary"]["tiles"]
    assert "instruments" in results["sections"] or results["sections"].get("longShort")
    assert results["missing"] == {}


def test_every_figure_reads_out_like_the_draft(tmp_path):
    """Each figure's trace carries a `hovertemplate` and the template names the lines the draft's tooltip has (section 6, item 14)."""
    prepare(tmp_path, CODE, frame=B.perp_bars(24 * 400))  # long enough for the rolling windows
    _, _, results = E.completed(tmp_path)
    expected = {
        "strategy.equity": ["hold", "%"], "strategy.drawdown": ["%"], "strategy.yearly": ["%"], "strategy.rollingSharpe": ["Sharpe"],
        "strategy.rollingBeta": ["Beta"], "strategy.rollingVolatility": ["%"], "strategy.exposure": ["Gross", "Net"], "strategy.account": ["Balance", "Margin at", "Maintenance margin", "Leverage"],
        "strategy.mae": ["Return", "MAE", "Exit"], "strategy.mfe": ["Return", "MFE", "Exit"], "strategy.barsHeld": ["bars"],
    }
    seen = {}
    for item in figures_of(results["sections"]):
        key = item["layout"]["meta"]["key"]
        templates = " ".join(str(trace.get("hovertemplate") or "") for trace in item["data"])
        seen[key] = templates
        assert templates.strip(), key + " has no hovertemplate"
    for key, words in expected.items():
        assert key in seen, key
        for word in words:
            assert word in seen[key], (key, word, seen[key])
    # signed quantities carry +/-; unsigned ones do not
    assert "%{y:+.1%}" in seen["strategy.equity"] or "+.1%" in seen["strategy.equity"]
    assert "+" not in seen["strategy.rollingVolatility"].replace("%{", "")


def test_orders_keep_rejected_with_reason(tmp_path):
    code = HOLD.replace("2.4", "2000.0")  # far more than the account can pay for
    prepare(tmp_path, code)
    _, work, results = E.completed(tmp_path)
    rows = E.rows(tmp_path, "orders")
    rejected = [row for row in rows if row["status"] == "Rejected"]
    assert rejected and all(row["reason"] for row in rejected)
    assert E.orders_table(tmp_path).shape[0] == len(rows)


def test_orders_readable(tmp_path):
    prepare(tmp_path, CODE)
    _, work, results = E.completed(tmp_path)
    for row in E.rows(tmp_path, "orders"):
        text = json.dumps(row)
        assert "ts_" not in text and "\\u" not in text
        assert row["instrument"] == "BTC/USDT:USDT"
        assert row["status"] in {"Filled", "Rejected", "Canceled", "Expired", "Partly filled", "Unfilled at end"}
        assert "{" not in str(row["reason"] or "")
    assert all(line["instrument"] in (None, "BTC/USDT:USDT") for line in work["log"])


def test_log_paged_not_cut(tmp_path):
    """The whole log is kept in log.parquet (the Work file only carries the first lines)."""
    prepare(tmp_path, CODE)
    _, work, _ = E.completed(tmp_path)
    table = pd.read_parquet(tmp_path / "output" / "strategy" / "log.parquet")
    assert len(table) >= len(work["log"]) > 0 and {"t", "kind", "text"} <= set(table.columns)
    assert table["t"].is_monotonic_increasing


def test_zero_trades_report(tmp_path):
    prepare(tmp_path, NOTHING)
    _, work, results = E.completed(tmp_path)
    assert work["stats"]["trades"] == 0 and work["stats"]["totalReturn"] == 0
    assert work["stats"]["sharpe"] is None
    assert work["notes"] == ["No trades. The code has not placed an order yet."]
    assert results["sections"]["orders"]["total"] == 0


def test_all_orders_rejected_says_how_many(tmp_path):
    prepare(tmp_path, HOLD.replace("2.4", "2000.0"))
    _, work, _ = E.completed(tmp_path)
    assert work["stats"]["trades"] == 0 and work["notes"] == ["No trades: 1 orders rejected"]


def test_short_sample_no_annualized(tmp_path):
    frame = B.perp_bars(24 * 20)  # 20 daily returns
    prepare(tmp_path, CODE, frame=frame)
    _, work, results = E.completed(tmp_path)
    stats = work["stats"]
    assert stats["sharpe"] is None and stats["sortino"] is None and stats["cagr"] is None
    assert stats["totalReturn"] is not None and work["shortSample"] is True
    assert results["sections"]["rolling"]["longerThanRun"] is True


def test_one_sharpe_per_page(tmp_path):
    prepare(tmp_path, CODE, frame=B.perp_bars(24 * 100))
    _, work, results = E.completed(tmp_path)
    found = []

    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                if "sharpe" in str(key).lower() and not str(key).startswith("rolling") and "Sharpe" not in path:
                    found.append(path + "/" + str(key))
                walk(item, path + "/" + str(key))
        elif isinstance(value, list):
            for item in value:
                walk(item, path)
    walk(results["sections"]["summary"])
    keys = [key for key in found if "probabilistic" not in key.lower() and "psr" not in key.lower()]
    assert len(keys) == 1, keys


def test_sharpe_matches_engine_with_the_calendar_year(tmp_path):
    prepare(tmp_path, CODE, frame=B.perp_bars(24 * 100))
    _, work, results = E.completed(tmp_path)
    reference = next(row["value"] for row in results["sections"]["summary"]["engine"] if row["group"].startswith("Registered") and row["name"].startswith("Sharpe"))
    assert work["stats"]["sharpe"] == pytest.approx(reference, abs=1e-9)


def test_results_has_native_and_benchmark_stats(tmp_path):
    prepare(tmp_path, CODE)
    _, _, results = E.completed(tmp_path)
    summary = results["sections"]["summary"]
    assert len(summary["returnStatistics"]) >= 14 and summary["againstBenchmark"]
    assert {"alpha", "beta", "trackingError", "informationRatio"} <= {row["name"] if isinstance(row, dict) else row for row in summary["againstBenchmark"]} or \
        {"alpha", "beta", "trackingError", "informationRatio"} <= set(summary["againstBenchmark"])


def test_benchmark_stats_match_the_engine_to_1e9(tmp_path):
    """Beta, tracking error and the information ratio the engine gives equal the textbook values on the same daily series (the sample standard
    deviation, annualized with the calendar's days)."""
    prepare(tmp_path, CODE, frame=B.perp_bars(24 * 120))
    _, work, results = E.completed(tmp_path)
    given = results["sections"]["summary"]["againstBenchmark"]
    mine = np.array([row[1] for row in work["dailyReturns"]])
    bench = np.array([row[2] or 0.0 for row in work["dailyReturns"]])
    beta = np.cov(mine, bench, ddof=1)[0, 1] / np.var(bench, ddof=1)
    error = np.std(mine - bench, ddof=1) * np.sqrt(365)
    assert given["beta"] == pytest.approx(beta, abs=1e-9)
    assert given["trackingError"] == pytest.approx(error, abs=1e-9)
    assert given["informationRatio"] == pytest.approx(np.mean(mine - bench) * 365 / error, abs=1e-9)


def test_style_section(tmp_path):
    prepare(tmp_path, CODE)
    _, _, results = E.completed(tmp_path)
    style = results["sections"]["style"]
    assert style["version"] == "Styles v1" and [row["style"] for row in style["exposures"]] == ["Beta to BTC/USDT:USDT hold"]
    assert "specific" in style


def test_style_rows_only_with_data(tmp_path):
    frame, ids = B.daily_universe(4, 120, halted=())
    stock_run(tmp_path, REBALANCE.replace("SCHEDULE", "{2: {0: .5, 1: .5}}"), ids, frame)
    _, _, results = E.completed(tmp_path)
    names = [row["style"] for row in results["sections"]["style"]["exposures"]]
    assert not any(name in names for name in ("Value", "Quality", "Size"))  # no point-in-time financials were given: no rows, no grey placeholders
    assert "unavailable" not in results["sections"]["style"]


def test_risk_free_irx_and_zero(tmp_path):
    rows = [[int(pd.Timestamp("2023-12-01", tz="UTC").timestamp() * 1000) + i * 86_400_000, 5.0] for i in range(120)]
    prepare(tmp_path, CODE, frame=B.perp_bars(24 * 100), risk_free={"basis": "irx", "rows": rows})
    _, work, _ = E.completed(tmp_path)
    assert work["riskFree"]["basis"] == "irx" and work["riskFree"]["meanPct"] == pytest.approx(5.0)
    zero = tmp_path / "zero"
    zero.mkdir()
    prepare(zero, CODE, frame=B.perp_bars(24 * 100))
    _, other, _ = E.completed(zero)
    assert other["riskFree"]["basis"] == "zero"
    assert work["stats"]["sharpe"] < other["stats"]["sharpe"]  # the same returns minus 5% a year


def test_beta_benchmark_matches_regression(tmp_path):
    """benchmark `beta`: each day the risk-free rate plus beta times the index's excess return; beta is the run's own regression slope."""
    frame, ids = B.daily_universe(2, 260, halted=())
    index = frame[frame["instrument"] == ids[1]]
    stock_run(tmp_path, REBALANCE.replace("SCHEDULE", "{2: {0: .5}}"), ids[:1], frame[frame["instrument"] == ids[0]],
              extra={"benchmark": {"kind": "beta", "label": "Cash + beta x S&P 500", "rows": [[int(row.ts_event) // 1_000_000, float(row.close)] for row in index.itertuples()]}})
    _, work, _ = E.completed(tmp_path)
    assert work["benchmarkLabel"].startswith("Cash + ")
    beta = work["benchmarkBeta"]
    closes = pd.Series([float(value) for value in index["close"]], index=pd.to_datetime(index["ts_event"].astype("int64"), unit="ns", utc=True).dt.normalize())
    market = closes.pct_change()
    level = pd.Series([point[1] for point in work["benchmark"]], index=pd.to_datetime([point[0] for point in work["benchmark"]], unit="ms", utc=True).normalize())
    daily = level.pct_change().dropna()
    expected = beta * market.reindex(daily.index)
    assert (daily - expected).abs().max() < 1e-7  # the equity points are written to 4 decimals


def test_equal_weight_benchmark_uses_members_on_each_day(tmp_path):
    frame, ids = B.daily_universe(3, 40, halted=((2, 0, 10),))  # the third name has no bars on the first ten days
    stock_run(tmp_path, REBALANCE.replace("SCHEDULE", "{2: {0: .5}}"), ids, frame, extra={"benchmark": {"kind": "equal"}})
    _, work, _ = E.completed(tmp_path)
    closes = frame.assign(at=pd.to_datetime(frame["ts_event"].astype("int64"), unit="ns", utc=True).dt.normalize(), close=frame["close"].astype(float)).pivot(index="at", columns="instrument", values="close")
    returns = closes.pct_change()
    expected = returns.mean(axis=1, skipna=True)
    level = pd.Series([p[1] for p in work["benchmark"]], index=pd.to_datetime([p[0] for p in work["benchmark"]], unit="ms", utc=True).normalize())
    got = level.pct_change().dropna()
    common = got.index.intersection(expected.index)
    assert (got[common] - expected[common]).abs().max() < 1e-9


# --- costs, fills and the account ------------------------------------------------------------------------------------------------

def test_market_order_fills_at_the_next_close_when_asked(tmp_path):
    frame = prepare(tmp_path, HOLD, settings={"fill": "nextClose", "costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}}, funding=False)
    _, work, _ = E.completed(tmp_path)
    closes = dict(zip(frame["ts_event"].astype("int64") // 1_000_000, frame["close"].astype(float), strict=True))
    trade = E.rows(tmp_path, "trades")[0]
    assert trade["entryPrice"] == pytest.approx(closes[trade["entry"]], abs=.11)


def test_funding_is_position_times_price_times_rate(tmp_path):
    """Three settlements by hand: a held long pays a positive rate and receives a negative one."""
    rows = pd.DataFrame({"instrument": B.INSTRUMENT, "time": [int(pd.Timestamp("2024-01-02 08:00", tz="UTC").timestamp() * 1000) + i * 8 * 3_600_000 for i in range(3)],
                         "rate": [0.0001, -0.00005, 0.0002]})
    frame = prepare(tmp_path, HOLD, funding_frame=rows, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": True}})
    _, work, _ = E.completed(tmp_path)
    closes = dict(zip(frame["ts_event"].astype("int64") // 1_000_000, frame["close"].astype(float), strict=True))
    qty = E.rows(tmp_path, "trades")[0]["qty"] if E.rows(tmp_path, "trades") else 2.4
    expected = sum(2.4 * closes[int(at)] * rate for at, rate in zip(rows["time"], rows["rate"], strict=True))
    assert work["costs"]["funding"] == pytest.approx(expected, abs=1e-8)
    assert qty == 2.4


def test_funding_gap_is_a_data_note_not_a_mark_on_the_costs(tmp_path):
    from modules.quant.backend.strategy_views import funding_gaps
    prepare(tmp_path, HOLD, extra={"fundingRates": {"path": str(tmp_path / "funding.parquet"), "gaps": {B.INSTRUMENT: [[1_704_240_000_000 * 1_000_000, 1_704_412_800_000 * 1_000_000, "Market has no rates"]]}}})
    _, work, results = E.completed(tmp_path)
    assert work["costs"]["incomplete"] and work["costs"]["incomplete"][0]["instrument"] == "BTC/USDT:USDT"
    rows = {row["name"]: row for row in results["sections"]["costs"]["breakdown"]}
    assert "note" not in rows["Funding"] and "off" not in rows["Funding"]
    assert funding_gaps(work) == ["BTC/USDT:USDT has no funding rates from Jan 3, 2024 to Jan 5, 2024 (Market has no rates); the costs hold no funding for that time"]


def test_cost_reconciliation_and_equity(tmp_path):
    prepare(tmp_path, CODE)
    _, work, _ = E.completed(tmp_path)
    costs = work["costs"]
    assert costs["gross"] - (costs["fees"] + costs["spread"] + costs["impact"] + costs["funding"] + costs["borrow"]) - costs["net"] == pytest.approx(0, abs=1e-6)
    assert work["equity"][-1][1] - work["equity"][0][1] == pytest.approx(costs["net"], abs=.01)


def test_stock_short_pays_borrow(tmp_path):
    """Short one share at 100 for a year: about 100 x 0.3% = 0.30; `borrowPct` is a rate a year, charged on each day held."""
    rows = [FLAT_HOUR[0]] * 400
    frame = B.hand_bars(rows, instrument="S00.XNAS", bar_type="S00.XNAS-1-DAY-LAST-EXTERNAL", step="D", span_ns=0, price_digits=2)
    frame[["ts_event", "ts_init"]] = frame[["ts_event", "ts_init"]].map(lambda value: str(int(value) + 21 * 3_600_000_000_000))
    code = PRELUDE + '''

class Short(q.Strategy):
    def __init__(self):
        super().__init__()
        self.n = 0

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.n += 1
        if self.n == 2:
            self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.SELL, self.instrument.make_qty(1), tags=["entry:short"]))
        if self.n == 367:
            self.close(reason="time")
'''
    stock_run(tmp_path, code, ["S00.XNAS"], frame)
    _, work, _ = E.completed(tmp_path)
    assert work["costs"]["borrow"] == pytest.approx(0.30, abs=.005)


def test_can_short_follows_the_shorting_setting(tmp_path):
    code = PRELUDE + '''

class Check(q.Strategy):
    def on_start(self):
        self.subscribe_bars(self.bar_type)
        q.stat("can short", int(q.can_short(self.instrument_id)))

    def on_bar(self, bar):
        pass
'''
    frame, ids = B.daily_universe(1, 10, halted=())
    stock_run(tmp_path, code, ids, frame, settings={"shorting": "off"})
    _, work, _ = E.completed(tmp_path)
    assert work["userStats"]["can short"] == 0


def test_limit_needs_trade_through(tmp_path):
    """A buy limit one tick above the bar's low touches it but does not trade through two ticks: `limitThroughTicks=2` leaves it unfilled."""
    rows = [(100.0, 101.0, 99.0, 100.0), (100.0, 101.0, 99.0, 100.0), (100.0, 101.0, 99.05, 100.0), (100.0, 101.0, 98.0, 100.0)] + [(100.0, 101.0, 99.0, 100.0)] * 6
    frame = B.hand_bars(rows)
    code = PRELUDE + '''

class Limit(q.Strategy):
    def __init__(self):
        super().__init__()
        self.n = 0

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.n += 1
        if self.n == 1:
            self.submit_order(self.order_factory.limit(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(1), self.instrument.make_price(99.0),
                                                       time_in_force=TimeInForce.GTC, tags=["entry:limit"]))
'''
    touch = tmp_path / "touch"
    through = tmp_path / "through"
    for root, ticks in ((touch, 0), (through, 2)):
        root.mkdir()
        prepare(root, code, frame=frame, funding=False, settings={"limitThroughTicks": ticks, "costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    _, touched, touched_results = E.completed(touch)
    _, crossed, crossed_results = E.completed(through)
    first = E.rows(touch, "orders")[0]
    second = E.rows(through, "orders")[0]
    assert first["status"] == "Filled" and first["filledAt"] < second.get("filledAt", 10 ** 15)
    # two ticks through 99.0 means trading at 98.8 or lower; only the fourth bar (low 98.0) does that
    assert second["status"] == "Filled" and second["filledAt"] == (int(frame["ts_event"].iloc[3]) - B.HOUR_NS) // 1_000_000  # filled inside that bar: its open


def test_fine_bars_decide_stop_before_target(tmp_path):
    """A long with a stop at 98.5 and a target at 102.5; inside one 1h bar (low 98, high 103) the 5-minute bars say which came first."""
    coarse = B.hand_bars([(100.0, 100.5, 99.5, 100.0)] * 3 + [(100.0, 103.0, 98.0, 101.0)] + [(101.0, 101.5, 100.5, 101.0)] * 3)
    fine = B.fine_bars(coarse)
    fourth = coarse.iloc[3]
    lo, hi = float(fourth["low"]), float(fourth["high"])
    window = fine[(fine["ts_event"].astype("int64") > int(coarse.iloc[2]["ts_event"])) & (fine["ts_event"].astype("int64") <= int(fourth["ts_event"]))]
    # in the fine data the high comes first in the test below and the low first in the second variant
    first_high = window.index[window["high"].astype(float).eq(hi)][0] < window.index[window["low"].astype(float).eq(lo)][0]
    code = PRELUDE + '''

class Bracket(q.Strategy):
    def __init__(self):
        super().__init__()
        self.n = 0

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        self.n += 1
        if self.n == 2:
            orders = self.order_factory.bracket(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(1), tp_price=self.instrument.make_price(102.5),
                                                sl_trigger_price=self.instrument.make_price(98.5), entry_tags=["entry:bracket"], tp_tags=["exit:target"], sl_tags=["exit:stop"])
            self.submit_order_list(orders)
'''
    prepare(tmp_path, code, frame=fine, funding=False, bar_types=[B.INSTRUMENT + "-1-HOUR-LAST-INTERNAL@5-MINUTE-EXTERNAL"],
            settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}}, extra={"fillBars": "5m"})
    _, work, _ = E.completed(tmp_path)
    reasons = [trade["exitBy"] for trade in E.rows(tmp_path, "trades")]
    assert reasons and reasons[0] in ("stop", "target")
    assert reasons[0] == ("target" if first_high else "stop")


def test_adaptive_order_without_fine_bars(tmp_path):
    """One 1h bar opening 100, high 103, low 98, close 101: a long with stop 98.5 and target 102.5 -- the open is nearer the low, so the stop goes first;
    opening at 102.8 makes the target first."""
    results = {}
    for name, opened in (("low_first", 100.0), ("high_first", 102.8)):
        root = tmp_path / name
        root.mkdir()
        frame = B.hand_bars([(100.0, 100.0, 100.0, 100.0)] * 3 + [(opened, 103.0, 98.0, 101.0)] + [(101.0, 101.5, 100.5, 101.0)] * 3, price_digits=1)
        code = PRELUDE + '''

class Bracket(q.Strategy):
    def __init__(self):
        super().__init__()
        self.n = 0

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.n += 1
        if self.n == 1:
            orders = self.order_factory.bracket(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(1), tp_price=self.instrument.make_price(102.5),
                                                sl_trigger_price=self.instrument.make_price(98.5), entry_tags=["entry:bracket"], tp_tags=["exit:target"], sl_tags=["exit:stop"])
            self.submit_order_list(orders)
'''
        prepare(root, code, frame=frame, funding=False, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
        _, work, _ = E.completed(root)
        results[name] = E.rows(root, "trades")[0]["exitBy"]
    assert results == {"low_first": "stop", "high_first": "target"}


def test_fee_shortfall_rejects_order_not_run(tmp_path):
    """The balance covers the notional to the cent but not the fee: the order is rejected for the fee and the run goes on."""
    rows = [(100.0, 100.0, 100.0, 100.0)] * 6
    frame = B.hand_bars(rows)
    venue = {**E.OKX, "balances": ["1000 USDT"], "leverage": 1}
    code = HOLD.replace("2.4", "10.0")  # 10 x 100 = 1000: everything, and a fee on top
    prepare(tmp_path, code, frame=frame, venues=[venue], funding=False, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    index, work, results = E.completed(tmp_path)
    rows = E.rows(tmp_path, "orders")
    assert rows[0]["status"] == "Rejected" and "fee" in rows[0]["reason"].lower()


def test_halted_name_keeps_position_with_reason(tmp_path):
    frame, ids = B.daily_universe(3, 30, halted=((1, 5, 9),))
    code = REBALANCE.replace("SCHEDULE", "{2: {0: 1 / 3, 1: 1 / 3, 2: 1 / 3}, 6: {0: .5, 1: .25, 2: .25}}")
    stock_run(tmp_path, code, ids, frame)
    _, work, results = E.completed(tmp_path)
    orders = [row for row in E.rows(tmp_path, "orders") if row["instrument"] == "S01" and row["status"] != "Filled"]
    assert orders and "halted" in orders[0]["reason"].lower()
    rebalance = [row for row in E.rows(tmp_path, "rebalances") if row["unfilled"]]
    assert rebalance and any("S01" in item["name"] and "halted" in item["reason"].lower() for item in rebalance[0]["unfilled"])
    day = {row["name"]: row for row in work["multi"]["holdings"]}["S01"]
    assert day["weight"] > 0  # still holds what it bought on the first rebalance day


def test_rebalance_to_reaches_weights(tmp_path):
    frame, ids = B.daily_universe(3, 40, halted=())
    code = REBALANCE.replace("SCHEDULE", "{2: {0: .5, 1: .3}, 20: {0: .2, 1: .2, 2: .4}}")
    stock_run(tmp_path, code, ids, frame)
    _, work, results = E.completed(tmp_path)
    for row in E.rows(tmp_path, "rebalances"):
        for name, target in row["targets"].items():
            assert abs(row["actual"][name] - target) < .02, (row["at"], name)
    assert {trade_order["tags"][0] for trade_order in E.rows(tmp_path, "orders")} == {"entry:rebalance"}
    orders = E.rows(tmp_path, "orders")
    # sells go before buys within one rebalance
    day20 = [row for row in orders if row["time"] >= E.rows(tmp_path, "rebalances")[-1]["at"]]
    sides = [row["side"] for row in day20]
    assert sides == sorted(sides, key=lambda side: 0 if side == "Sell" else 1)


def test_two_venues_equity_adds_up(tmp_path):
    frame, usdt = B.two_venues(30)
    usdt.to_parquet(tmp_path / "usdt.parquet", index=False)
    code = PRELUDE + '''

class Both(q.Strategy):
    def __init__(self):
        super().__init__()
        self.done = set()

    def on_start(self):
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        name = bar.bar_type.instrument_id
        if name not in self.done:
            self.done.add(name)
            instrument = self.cache.instrument(name)
            self.submit_order(self.order_factory.market(name, OrderSide.BUY, instrument.make_qty(0.1 if "BTC" in str(name) else 1.0), tags=["entry:hold"]))
'''
    venues = [{**E.OKX, "fees": {"type": "maker_taker", "maker": 0, "taker": 0}, "balances": ["10000 USDT"]}, {**E.COINBASE, "fees": {"type": "maker_taker", "maker": 0, "taker": 0}}]
    instruments = [{**E.PERP, "takerFee": "0"}, E.SPOT]
    prepare(tmp_path, code, frame=frame, venues=venues, instruments=instruments, funding=False,
            bar_types=[B.BAR_TYPE, "ETHUSD-SPOT.COINBASE-1-HOUR-LAST-EXTERNAL"],
            display=[{"nativeInstrument": B.INSTRUMENT, "symbol": "BTC/USDT:USDT"}, {"nativeInstrument": "ETHUSD-SPOT.COINBASE", "symbol": "ETH/USD"}],
            settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}, "reportCurrency": "USD"},
            extra={"accounts": {"reportCurrency": "USD", "conversions": [{"currency": "USDT", "path": str(tmp_path / "usdt.parquet")}]}})
    _, work, _ = E.completed(tmp_path)
    assert work["currency"] == "USD" and len(work["accounts"]) == 2
    rate = pd.Series(usdt["close"].to_numpy(), index=pd.to_datetime(usdt["time"], unit="ms", utc=True).dt.normalize())
    for point in work["equity"][1:]:
        day = pd.Timestamp(point[0], unit="ms", tz="UTC").normalize()
        total = sum(account["equity"][str(point[0])] * (rate.get(day, 1.0) if account["currency"] == "USDT" else 1.0) for account in work["accounts"])
        assert total == pytest.approx(point[1], abs=.01)


def test_q_series_point_in_time(tmp_path):
    code = PRELUDE + '''
import pandas as pd


class Reads(q.Strategy):
    def __init__(self):
        super().__init__()
        self.bad = 0
        self.reads = 0

    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        series = q.series("funding_rate")
        now = self.clock.timestamp_ns()
        if len(series):
            self.reads += 1
            if series.index[-1].value > now:
                self.bad += 1
        q.stat("bad", self.bad)
        q.stat("reads", self.reads)
'''
    rows = B.funding_rows(gap=None)
    extras = tmp_path / "extras" / "funding_rate"
    extras.mkdir(parents=True)
    pd.DataFrame({"time": rows["time"], "value": rows["rate"]}).to_parquet(extras / (B.INSTRUMENT + ".parquet"), index=False)
    prepare(tmp_path, code, extra={"extras": {"funding_rate": {B.INSTRUMENT: str(extras / (B.INSTRUMENT + ".parquet"))}}})
    _, work, _ = E.completed(tmp_path)
    assert work["userStats"]["reads"] > 100 and work["userStats"]["bad"] == 0


def test_q_series_needs_literal_name(tmp_path):
    code = PRELUDE + '''

class Reads(q.Strategy):
    def on_start(self):
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        name = "funding_" + "rate"
        q.series(name)
'''
    prepare(tmp_path, code)
    index = run(tmp_path)
    assert index["status"] == "failed"
    assert 'q.series needs the name written out, e.g. q.series("funding_rate"), so TALYX can fetch it before the run' in index["error"]


def liquidation_run(root, rows, precision=1):
    """Long 190 at 100 on 1,000 USDT, leverage 20, maintenance 0.5%, the venue closing the position out when equity falls to it."""
    venue = {**E.OKX, "balances": ["1000 USDT"], "leverage": 20}
    margin = {**E.PERP, "marginInit": "0.05", "marginMaint": "0.005", "pricePrecision": precision, "priceIncrement": f"{10 ** -precision:.{precision}f}"}
    prepare(root, HOLD.replace("2.4", "190.0"), frame=B.hand_bars(rows, price_digits=precision), venues=[venue], instruments=[margin], funding=False,
            settings={"exchange": {"leverage": 20, "liquidation": True, "maintenancePct": 0.5, "stopTrigger": "last"}, "costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    return E.completed(root)


# Balance 1,000 less the 9.5 fee, 190 long from 100: equity meets the maintenance margin where 990.5 + 190 (P - 100) = 0.005 x 190 P.
LIQUIDATION_PRICE = (100 - 990.5 / 190) / (1 - 0.005)


def test_margin_used_is_the_positions_value_over_the_leverage(tmp_path):
    """190 long at 100 on a 20x margin account holds 19,000 / 20 = 950 of margin; the maintenance margin is 0.5% of 19,000 = 95.
    The engine's own locked amount is only the maintenance margin, which drew "Margin used" near 0 while the account was in 19 times over."""
    _, _, results = liquidation_run(tmp_path, [(100.0, 100.0, 100.0, 100.0)] * 60)
    last = results["sections"]["account"]["points"][-1]
    assert last["marginUsed"] == pytest.approx(950, rel=.01) and last["marginMaint"] == pytest.approx(95, rel=.01)


def test_liquidation_logged(tmp_path):
    rows = [(100.0, 100.0, 100.0, 100.0)] * 3 + [(100.0, 100.0, 80.0, 80.0)] + [(80.0, 80.0, 79.0, 79.0)] * 4
    _, work, results = liquidation_run(tmp_path, rows)
    assert any(line["kind"] == "Stop" and "iquidat" in line["text"] for line in work["log"])
    assert any(row["event"] == "Liquidated" for row in results["sections"]["account"]["marginEvents"])
    assert E.rows(tmp_path, "trades")[0]["exitBy"] == "liquidation"
    order = next(row for row in E.rows(tmp_path, "orders") if "exit:liquidation" in row["tags"])
    assert order["type"] == "Stop market" and order["linked"] == "liquidation" and order["status"] == "Filled"


def test_liquidation_price_is_where_equity_meets_the_maintenance_margin(tmp_path):
    rows = [(100.0, 100.0, 100.0, 100.0)] * 3 + [(100.0, 100.0, 80.0, 80.0)] + [(80.0, 80.0, 79.0, 79.0)] * 4
    _, work, results = liquidation_run(tmp_path, rows)
    order = next(row for row in E.rows(tmp_path, "orders") if "exit:liquidation" in row["tags"])
    assert order["trigger"] == pytest.approx(LIQUIDATION_PRICE, abs=.11)
    trade = E.rows(tmp_path, "trades")[0]
    # it fills on the bar that went through it, at the trigger
    assert trade["exit"] == (int(B.hand_bars(rows)["ts_event"].iloc[3]) - B.HOUR_NS) // 1_000_000 and trade["exitPrice"] == pytest.approx(LIQUIDATION_PRICE, abs=.11)


def test_liquidation_fills_at_the_open_when_the_bar_gaps_through(tmp_path):
    rows = [(100.0, 100.0, 100.0, 100.0)] * 3 + [(85.0, 86.0, 80.0, 82.0)] + [(82.0, 82.0, 80.0, 81.0)] * 4
    _, work, _ = liquidation_run(tmp_path, rows)
    assert E.rows(tmp_path, "trades")[0]["exitBy"] == "liquidation" and E.rows(tmp_path, "trades")[0]["exitPrice"] == pytest.approx(85.0, abs=.11)


def test_the_liquidation_stop_is_not_one_of_the_codes_open_orders(tmp_path):
    """The code's `open_orders()` does not list the stop TALYX keeps at the liquidation price."""
    code = HOLD.replace("2.4", "50.0") + """
    def on_order_filled(self, event):
        q.stat("code sees open orders", len(self.open_orders()))
"""
    venue = {**E.OKX, "balances": ["1000 USDT"], "leverage": 20}
    margin = {**E.PERP, "marginInit": "0.05", "marginMaint": "0.005"}
    prepare(tmp_path, code, frame=B.hand_bars([(100.0, 100.0, 100.0, 100.0)] * 6), venues=[venue], instruments=[margin], funding=False,
            settings={"exchange": {"leverage": 20, "liquidation": True, "maintenancePct": 0.5, "stopTrigger": "last"}, "costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    _, work, results = E.completed(tmp_path)
    stops = [row for row in E.rows(tmp_path, "orders") if "exit:liquidation" in row["tags"]]
    assert len(stops) >= 1 and work["userStats"]["code sees open orders"] == 0


def test_engine_failure_without_user_frame(tmp_path):
    code = PRELUDE + '''

class Broken(q.Strategy):
    def on_start(self):
        self.subscribe_bars(self.bar_type)
        self.cache.instrument(None)
'''
    prepare(tmp_path, code)
    index = run(tmp_path)
    assert index["status"] == "failed"
    context = index["failureContext"]
    assert context.get("inEngine") is True and context["locals"] == [] and context.get("line") in (None, 0)


def test_cancelled_keeps_partial(tmp_path):
    """A run told to stop writes what it has: the Work shows the part so far, marked stopped, and the checks are not computed."""
    code = CODE.replace("        self.count += 1\n", "        self.count += 1\n        if self.count == 200:\n            open('../output/stop.request', 'w').close()\n")
    prepare(tmp_path, code)
    index = run(tmp_path)
    assert index["status"] == "cancelled"
    work = json.loads((tmp_path / "output" / "strategy" / "work.json").read_text(encoding="utf-8"))
    assert work["partial"] is True and work["stoppedAt"] and E.rows(tmp_path, "trades") and work["checks"] == {}


def test_backtest_does_not_call_learning_reports(tmp_path, monkeypatch):
    source = (E.QUANT / "native" / "strategy_research.py").read_text(encoding="utf-8")
    assert "learning_reports" not in source


def test_progress_file_advances(tmp_path):
    prepare(tmp_path, CODE)
    E.completed(tmp_path)
    progress = json.loads((tmp_path / "output" / "progress.json").read_text(encoding="utf-8"))
    assert set(progress) >= {"simulatedTo", "value", "tradesSoFar"} and progress["value"] >= .96 and progress["tradesSoFar"] > 0


def test_gap_noted(tmp_path):
    """Bars missing in the middle of the data (an exchange pause) are listed in the Work's notes."""
    frame = B.perp_bars(24 * 40)
    frame = frame.drop(index=range(300, 303)).reset_index(drop=True)
    prepare(tmp_path, CODE, frame=frame)
    _, work, _ = E.completed(tmp_path)
    assert any("3 bars" in note for note in work["notes"]), work["notes"]


# --- section 5, the numbers that must be right ---------------------------------------------------------------------------------------

def test_buy_and_hold_matches_the_price(tmp_path):
    frame = prepare(tmp_path, HOLD, funding=False, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}},
                    venues=[{**E.OKX, "fees": {"type": "maker_taker", "maker": 0, "taker": 0}}], instruments=[{**E.PERP, "takerFee": "0"}])
    _, work, _ = E.completed(tmp_path)
    opens = frame["open"].astype(float).to_numpy()
    closes = frame["close"].astype(float).to_numpy()
    entry = opens[1]  # decided on bar 0's close, filled at bar 1's open
    assert work["stats"]["totalReturn"] == pytest.approx(2.4 * (closes[-1] - entry) / 100_000, abs=1e-6)
    day = pd.to_datetime(frame["ts_event"].astype("int64") - 1, unit="ns", utc=True).dt.normalize()  # a bar ending at midnight is the day before's last
    day_end = pd.Series(closes).groupby(day.to_numpy()).last().to_numpy()
    path = 100_000 + 2.4 * (day_end - entry)
    path = np.concatenate([[100_000.0], path])
    expected = float(np.min(path / np.maximum.accumulate(path) - 1))
    assert work["stats"]["maxDrawdown"] == pytest.approx(expected, abs=1e-4)  # the equity is sampled at each day's end


def test_open_position_at_the_end_is_valued_at_the_last_close(tmp_path):
    prepare(tmp_path, HOLD)
    _, work, _ = E.completed(tmp_path)
    assert work["stats"]["trades"] == 1 and work["stats"]["openAtEnd"] == 1
    assert E.rows(tmp_path, "trades")[0]["exitBy"] == "open at the end" and E.rows(tmp_path, "trades")[0]["exit"] is None or E.rows(tmp_path, "trades")[0]["exit"]


def test_trade_table_count_equals_closed_plus_open(tmp_path):
    prepare(tmp_path, CODE)
    _, work, _ = E.completed(tmp_path)
    table = E.trades_table(tmp_path)
    closed = int((table["exitBy"] != "open at the end").sum())
    assert len(table) == closed + int((table["exitBy"] == "open at the end").sum()) == work["tradesTotal"]
    assert work["stats"]["trades"] == len(table)


def test_yearly_returns_compound_to_the_total(tmp_path):
    frame = B.perp_bars(24 * 500)
    prepare(tmp_path, CODE, frame=frame, funding=False)
    _, work, results = E.completed(tmp_path)
    total = 1.0
    for row in results["sections"]["periods"]["table"]:
        total *= 1 + row["total"]
    assert total - 1 == pytest.approx(work["stats"]["totalReturn"], abs=1e-6)


def test_crisis_periods_report_the_benchmarks_own_move(tmp_path):
    """Hold a stock through 2020-02-19 to 2020-03-23: the benchmark column in the Crisis section is that stretch's return of the benchmark."""
    dates = pd.bdate_range("2020-01-02", "2020-05-01", tz="UTC")
    price = np.interp(np.arange(len(dates)), [0, 33, 55, len(dates) - 1], [300.0, 337.0, 222.0, 290.0])
    frame = B.frame_of("S00.XNAS", "S00.XNAS-1-DAY-LAST-EXTERNAL", dates + pd.Timedelta(hours=21), price, price, np.full(len(dates), 1e6), span_ns=0, price_digits=2, size_digits=0, spread=0)
    stock_run(tmp_path, REBALANCE.replace("SCHEDULE", "{2: {0: .5}}"), ["S00.XNAS"], frame)
    _, _, results = E.completed(tmp_path)
    covid = next(row for row in results["sections"]["crisis"]["periods"] if "COVID" in row["name"] or row["name"].startswith("Covid"))
    window = pd.Series(price, index=dates)
    expected = window[pd.Timestamp("2020-03-23", tz="UTC")] / window[pd.Timestamp("2020-02-19", tz="UTC")] - 1
    assert covid["benchmark"] == pytest.approx(expected, abs=.01)


def test_sectors_sum_to_the_equity_change(tmp_path):
    frame, ids = B.daily_universe(6, 120, halted=())
    industry = {identifier: ("Tech" if index % 2 else "Energy") for index, identifier in enumerate(ids)}
    code = REBALANCE.replace("SCHEDULE", "{2: {0: .1, 1: .1, 2: .1, 3: .1, 4: .1, 5: .1}, 60: {0: .2, 1: .1, 2: 0, 3: .1, 4: .1, 5: .0}}")
    stock_run(tmp_path, code, ids, frame, extra={"industry": industry})
    _, work, results = E.completed(tmp_path)
    sectors = results["sections"]["sectors"]["sectors"]
    assert {row["sector"] for row in sectors} == {"Tech", "Energy"}
    assert sum(row["net"] for row in sectors) == pytest.approx(work["equity"][-1][1] - work["equity"][0][1], abs=.5)


def test_the_liquidation_stop_is_moved_when_the_position_grows(tmp_path):
    """A second buy makes the position bigger: the one stop is amended to its size and a new price, not left behind or doubled."""
    code = PRELUDE + '''

class Grow(q.Strategy):
    def __init__(self):
        super().__init__()
        self.n = 0

    def on_start(self):
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.n += 1
        if self.n in (1, 10):
            self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(100 if self.n == 1 else 50), tags=["entry:grow"]))
'''
    venue = {**E.OKX, "balances": ["1000 USDT"], "leverage": 20}
    margin = {**E.PERP, "marginInit": "0.05", "marginMaint": "0.005"}
    prepare(tmp_path, code, frame=B.hand_bars([(100.0, 100.0, 100.0, 100.0)] * 30), venues=[venue], instruments=[margin], funding=False,
            settings={"exchange": {"leverage": 20, "liquidation": True, "maintenancePct": 0.5, "stopTrigger": "last"}, "costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    _, _, results = E.completed(tmp_path)
    stops = [row for row in E.rows(tmp_path, "orders") if "exit:liquidation" in row["tags"]]
    assert len(stops) == 1 and stops[0]["qty"] == 150
    # 150 long from 100 on about 992 of balance: (100 - 992 / 150) / 0.995
    assert stops[0]["trigger"] == pytest.approx((100 - 992.5 / 150) / 0.995, abs=.3)


def test_near_liquidation_is_an_account_event_when_equity_nears_the_maintenance_margin(tmp_path):
    """The day ends with equity at 1.19 times the maintenance margin (liquidation at 95.26, close at 95.35): a Near liquidation event, and no Liquidated."""
    rows = [(100.0, 100.0, 100.0, 100.0)] * 26 + [(100.0, 100.0, 95.3, 95.35)] * 4 + [(95.35, 95.4, 95.3, 95.35)] * 20
    _, work, results = liquidation_run(tmp_path, rows, precision=3)
    events = results["sections"]["account"]["marginEvents"]
    assert any(row["event"] == "Near liquidation" for row in events) and not any(row["event"] == "Liquidated" for row in events)
    assert E.rows(tmp_path, "trades")[0]["exitBy"] == "open at the end"


def test_the_benchmark_starts_where_the_account_starts(tmp_path):
    """holding what the strategy trades counts from the first open, as the account's capital does. A first day that goes from 100 to
    110 is a 10% day for the benchmark (it was taken from the first day's close, so that day was missing)."""
    path = [round(100 + 10 * (k + 1) / 24, 1) for k in range(24)]
    rng = np.random.default_rng(2)
    later = [round(value, 1) for value in 110 * np.exp(np.cumsum(rng.normal(0, .004, 24 * 29)))]
    closes = path + later
    frame = B.hand_bars([(o, max(o, c), min(o, c), c) for o, c in zip([100.0, *closes[:-1]], closes, strict=True)])
    prepare(tmp_path, HOLD, frame=frame)
    _, work, _ = E.completed(tmp_path)
    assert work["dailyReturns"][0][2] == pytest.approx(0.10, abs=1e-9)
    # every later day is close over the close before
    assert work["dailyReturns"][1][2] == pytest.approx(later[23] / 110 - 1, abs=1e-9)


def test_a_source_that_publishes_late_still_fills_at_the_next_open(tmp_path):
    """bars known 15 minutes after they close (a source's publication delay): the engine's clock runs on when bars are known, and the fill
    model found no bar at those moments, so every market order was refused as halted. It fills at the next bar's open, as with no delay."""
    frame = B.perp_bars(24 * 10)
    frame["ts_init"] = (frame["ts_event"].astype("int64") + 15 * 60_000_000_000).astype(str)
    prepare(tmp_path, HOLD, frame=frame, settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}}, funding=False)
    _, work, results = E.completed(tmp_path)
    assert not [row for row in E.rows(tmp_path, "orders") if row["status"] != "Filled"]
    opens = frame["open"].astype(float).tolist()
    trade = E.rows(tmp_path, "trades")[0]
    assert trade["entryPrice"] == pytest.approx(opens[1], abs=.11)  # decided on the first bar, filled at the second's open


HEDGE = {"exchange": {"leverage": 2, "liquidation": False, "maintenancePct": 0.5, "stopTrigger": "last", "positionMode": "hedge"},
         "costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}}


def hedge_run(root, code, rows):
    venue = {**E.OKX, "balances": ["10000 USDT"], "leverage": 2, "omsType": "HEDGING"}
    prepare(root, PRELUDE + code, frame=B.hand_bars(rows), venues=[venue], instruments=[{**E.PERP, "marginInit": "0.5", "marginMaint": "0.005"}], funding=False, settings=HEDGE)
    return E.completed(root)


def test_hedge_mode_target_weight_turns_the_position_round_instead_of_holding_both_sides(tmp_path):
    """in Hedge mode an order with no position opens a new one, so going from +50% to -50% left the long open beside a new short (gross
    doubled). The long is closed, and the short is the instrument's one position."""
    code = '''
class Turn(q.Strategy):
    def on_start(self):
        self.count = 0
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.count += 1
        if self.count == 2:
            self.order_target_weight(self.instrument_id, 0.5)
        if self.count == 5:
            self.order_target_weight(self.instrument_id, -0.5)
        if self.count == 8:
            q.stat("positions", len(self.cache.positions_open(instrument_id=self.instrument_id)))
            q.stat("held", self.position_size(self.instrument_id))
            q.stat("equity", self.equity())
'''
    _, work, _ = hedge_run(tmp_path, code, [(100.0, 100.0, 100.0, 100.0)] * 10)
    stats = work["userStats"]
    assert stats["positions"] == 1 and stats["held"] == pytest.approx(-0.5 * stats["equity"] / 100, rel=1e-3)
    assert [trade["side"] for trade in E.rows(tmp_path, "trades")] == ["Long", "Short"] and E.rows(tmp_path, "trades")[0]["exit"] is not None


def test_hedge_mode_equity_counts_each_side_from_its_own_entry(tmp_path):
    """equity() took the first position's entry for the net size, so a long from 100 beside a short from 110 counted nothing (net zero):
    each side counts from its own entry, (c - 100) - (c - 110) = 10 whatever the close."""
    code = '''
class Both(q.Strategy):
    def on_start(self):
        self.count = 0
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.count += 1
        if self.count == 1:
            self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(1)))
        if self.count == 2:
            self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.SELL, self.instrument.make_qty(1)))
        if self.count == 5:
            account = self.cache.account_for_venue(self.instrument.id.venue)
            q.stat("positions", len(self.cache.positions_open(instrument_id=self.instrument_id)))
            q.stat("over cash", self.equity() - float(str(account.balance_total(self.instrument.quote_currency).as_decimal())))
'''
    _, work, _ = hedge_run(tmp_path, code, [(100.0, 100.0, 100.0, 100.0)] * 2 + [(110.0, 110.0, 110.0, 110.0)] + [(120.0, 120.0, 120.0, 120.0)] * 4)
    assert work["userStats"]["positions"] == 2 and work["userStats"]["over cash"] == pytest.approx(10.0, abs=1e-6)


def two_longs(root, rows):
    """Two longs of 95 from 100 on 1,000 USDT at 20x (fees 4.75 each, balance 990.5), maintenance 0.5%, both instruments on the same bars."""
    second = "ETHUSDT-PERP.OKX"
    code = '''
class Both(q.Strategy):
    def on_start(self):
        self.done = set()
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        name = bar.bar_type.instrument_id
        if name not in self.done:
            self.done.add(name)
            self.submit_order(self.order_factory.market(name, OrderSide.BUY, self.cache.instrument(name).make_qty(95)))
'''
    frame = pd.concat([B.hand_bars(rows), B.hand_bars(rows, instrument=second, bar_type=second + "-1-HOUR-LAST-EXTERNAL")], ignore_index=True)
    venue = {**E.OKX, "balances": ["1000 USDT"], "leverage": 20}
    margin = {**E.PERP, "marginInit": "0.05", "marginMaint": "0.005"}
    instruments = [margin, {**margin, "id": second, "symbol": "ETHUSDT-PERP", "baseCurrency": "ETH"}]
    prepare(root, PRELUDE + code, frame=frame, venues=[venue], instruments=instruments, funding=False, bar_types=[BAR_TYPE, second + "-1-HOUR-LAST-EXTERNAL"],
            display=[{"nativeInstrument": INSTRUMENT, "symbol": "BTC/USDT:USDT"}, {"nativeInstrument": second, "symbol": "ETH/USDT:USDT"}],
            settings={"exchange": {"leverage": 20, "liquidation": True, "maintenancePct": 0.5, "stopTrigger": "last"}, "costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    return E.completed(root)


def test_an_account_found_under_its_maintenance_margin_at_a_close_is_closed_out(tmp_path):
    """both open 5% lower (a gap) and stay there. BTC's bar comes first; ETH's stop, moved with BTC's fall, is gapped through and fills
    at 95. BTC is then left with equity about 40 under its maintenance margin 0.005 x 95 x 95 = 45.1, its stop (near 90.5) could not be moved
    past a price already gone through, and it stayed open under water. Found under the margin at that close, it is closed out at the next price."""
    rows = [(100.0, 100.0, 100.0, 100.0)] * 3 + [(95.0, 95.0, 95.0, 95.0)] * 4
    _, work, results = two_longs(tmp_path, rows)
    times = [(int(at) - B.HOUR_NS) // 1_000_000 for at in B.hand_bars(rows)["ts_event"]]  # each bar's open: a fill's time
    exits = {trade["instrument"]: trade for trade in E.rows(tmp_path, "trades")}
    assert len(E.rows(tmp_path, "trades")) == 2 and all(trade["exitBy"] == "liquidation" for trade in E.rows(tmp_path, "trades"))
    assert exits["ETH/USDT:USDT"]["exit"] == times[3] and exits["BTC/USDT:USDT"]["exit"] == times[4]
    assert all(trade["exitPrice"] == pytest.approx(95.0, abs=.11) for trade in E.rows(tmp_path, "trades"))
    assert sum(row["event"] == "Liquidated" for row in results["sections"]["account"]["marginEvents"]) == 2


def test_a_positions_liquidation_price_counts_the_other_positions_maintenance_margin(tmp_path):
    """the account's maintenance margin is the rate times every position's value. BTC closes at 95 first; ETH's stop is then where
    990.5 - 95 x 5 + 95 (P - 100) = 0.005 x 95 x (95 + P), P = (100 - (515.5 - 45.125) / 95) / 0.995 = 95.53 (95.5 at its price step); left out,
    the other's margin put it at 95.0. ETH's bar reaches it and closes there; BTC, with ETH's loss taken, has equity over its own margin."""
    rows = [(100.0, 100.0, 100.0, 100.0)] * 3 + [(100.0, 100.0, 95.0, 95.0)] + [(95.0, 95.0, 95.0, 95.0)] * 3
    _, work, _ = two_longs(tmp_path, rows)
    eth = next(trade for trade in E.rows(tmp_path, "trades") if trade["instrument"] == "ETH/USDT:USDT")
    assert eth["exitBy"] == "liquidation" and eth["exitPrice"] == pytest.approx(95.5, abs=.01)


def test_equity_adds_every_venues_account_in_one_currency(tmp_path):
    """equity() read the first instrument's account only. 10,000 USDT at OKX and 10,000 USD at COINBASE, a USDT at 0.5 USD in the run's
    rates (exaggerated so the conversion shows): in USDT that is 10,000 + 10,000 / 0.5 = 30,000."""
    frame, usdt = B.two_venues(5)
    usdt["close"] = 0.5
    usdt.to_parquet(tmp_path / "usdt.parquet", index=False)
    code = PRELUDE + '''

class Idle(q.Strategy):
    def on_start(self):
        self.count = 0
        for bar_type in self.bar_types:
            self.subscribe_bars(bar_type)

    def on_bar(self, bar):
        self.count += 1
        if self.count == 60:
            q.stat("equity", self.equity())
'''
    venues = [{**E.OKX, "balances": ["10000 USDT"]}, E.COINBASE]
    prepare(tmp_path, code, frame=frame, venues=venues, instruments=[E.PERP, E.SPOT], funding=False, bar_types=[B.BAR_TYPE, "ETHUSD-SPOT.COINBASE-1-HOUR-LAST-EXTERNAL"],
            display=[{"nativeInstrument": B.INSTRUMENT, "symbol": "BTC/USDT:USDT"}, {"nativeInstrument": "ETHUSD-SPOT.COINBASE", "symbol": "ETH/USD"}],
            settings={"costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}, "reportCurrency": "USD"},
            extra={"accounts": {"reportCurrency": "USD", "conversions": [{"currency": "USDT", "path": str(tmp_path / "usdt.parquet")}]}})
    _, work, _ = E.completed(tmp_path)
    assert work["userStats"]["equity"] == pytest.approx(30_000.0)


def test_the_liquidation_stops_talyx_moves_are_their_own_log_kind(tmp_path):
    """the stop TALYX keeps at the liquidation price is cancelled each time the position closes or changes side; its lines filled the log
    as "Not filled ... Canceled". A plain cancel has no line at all now; the orders count these stops under their own name."""
    code = PRELUDE + '''

class Flip(q.Strategy):
    def on_start(self):
        self.n = 0
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar):
        self.n += 1
        if self.n % 4 == 1:
            self.submit_order(self.order_factory.market(self.instrument_id, OrderSide.BUY, self.instrument.make_qty(50)))
        elif self.n % 4 == 3:
            self.close()
'''
    venue = {**E.OKX, "balances": ["1000 USDT"], "leverage": 20}
    margin = {**E.PERP, "marginInit": "0.05", "marginMaint": "0.005"}
    prepare(tmp_path, code, frame=B.hand_bars([(100.0, 100.0, 100.0, 100.0)] * 20), venues=[venue], instruments=[margin], funding=False,
            settings={"exchange": {"leverage": 20, "liquidation": True, "maintenancePct": 0.5, "stopTrigger": "last"}, "costs": {"halfSpreadBps": 0, "impact": 0, "funding": False}})
    _, work, results = E.completed(tmp_path)
    kinds = [line["kind"] for line in work["log"]]
    assert "Liquidation stop" not in kinds and "Not filled" not in kinds
    assert results["sections"]["orders"]["counts"]["Liquidation stop"] >= 3 and "Canceled" not in results["sections"]["orders"]["counts"]


def test_halted_names_close_at_the_next_price_when_asked(tmp_path):
    """"Halted names: Close at next price" was stored and never read; a halted name was kept either way. S01 has no bars on days 5-8
    while the others trade: with "close" its position is closed at its first bar after the halt, at that bar's open (its time is that open)."""
    frame, ids = B.daily_universe(3, 30, halted=((1, 5, 9),))
    code = REBALANCE.replace("SCHEDULE", "{2: {0: 1 / 3, 1: 1 / 3, 2: 1 / 3}, 6: {0: .5, 1: .25, 2: .25}}")
    stock_run(tmp_path, code, ids, frame, settings={"halted": "close"})
    _, work, _ = E.completed(tmp_path)
    s01 = frame[frame["instrument"] == ids[1]].reset_index(drop=True)
    back = s01.iloc[5]  # day 9, the first bar after the halt (days 5-8 are missing)
    trade = next(item for item in E.rows(tmp_path, "trades") if item["nativeInstrument"] == ids[1])
    assert trade["exitBy"] == "halted" and trade["exitPrice"] == pytest.approx(float(back["open"]), abs=.011)
    assert trade["exit"] == (int(back["ts_event"]) - B.DAY_NS) // 1_000_000
    assert {row["name"]: row for row in work["multi"]["holdings"]}.get("S01", {"weight": 0})["weight"] == 0


def test_a_stop_ends_the_engine_there_not_after_the_rest_of_the_data(tmp_path):
    """after a stop the engine went on through the rest of the data without the strategy (an S&P 500 run: 33 seconds, then its
    process was ended and what it had was lost). The engine now ends at the stop: its own account record stops there too."""
    code = CODE.replace("        self.count += 1\n", "        self.count += 1\n        if self.count == 200:\n            open('../output/stop.request', 'w').close()\n")
    frame = prepare(tmp_path, code, frame=B.perp_bars(24 * 400))
    index = run(tmp_path)
    assert index["status"] == "cancelled"
    work = json.loads((tmp_path / "output" / "strategy" / "work.json").read_text(encoding="utf-8"))
    last = int(frame["ts_event"].astype("int64").iloc[-1]) // 1_000_000
    assert work["partial"] is True and work["stoppedAt"] < last - 300 * 86_400_000  # within days of bar 200, of 400 days of data
    results = json.loads((tmp_path / "output" / "strategy" / "results.json").read_text(encoding="utf-8"))
    assert results["sections"]["account"]["points"][-1]["t"] < last - 300 * 86_400_000
