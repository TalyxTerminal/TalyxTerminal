import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import object_data, public_runs, strategy_inputs

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
from native.strategy_costs import Ledger  # noqa: E402
from native.strategy_report import Missing, Report  # noqa: E402

DAYS = pd.date_range("2025-04-01", periods=3, tz="UTC")
MS = 1_000_000


def report(tmp_path, *, fx=None, incomplete=False):
    dates = DAYS + pd.Timedelta(hours=20)
    names = [("A", "USD")] if fx is None else [("A", "USD"), ("B", "JPY")]
    rows = [{"ts_event": date.value, "instrument": name, "open": 100, "close": 100}
            for name, _ in names for date in dates]
    account = [{"timestampNs": date.value, "account": name, "currency": unit, "equityNumeric": value,
                "valuation": "incomplete" if incomplete and i == 1 else "complete"}
               for name, unit in names for i, (date, value) in enumerate(zip(dates, [100, 30, 105], strict=True))]
    config = {"venues": [{"balances": [f"100 {unit}"]} for _, unit in names],
              "instruments": [{"id": name, "type": "equity", "currency": unit} for name, unit in names]}
    invocation = {}
    if fx is not None:
        path = tmp_path / "JPY.parquet"
        pd.DataFrame(fx, columns=["time", "close"]).to_parquet(path)
        invocation["accounts"] = {"reportCurrency": "USD", "conversions": [{"currency": "JPY", "path": str(path)}]}
    return Report(SimpleNamespace(), None, config, invocation, pd.DataFrame(rows), {"Account equity": pd.DataFrame(account)},
                  Ledger(), None, 252, dict(names))


def test_jpy_fees_turnover_and_exposure_in_usd(tmp_path):
    made = report(tmp_path, fx=[((DAYS[0] - pd.Timedelta(days=1)).value // MS, .01)])
    equity = made.equity_series()
    t = (DAYS[0] + pd.Timedelta(hours=10)).value
    made.engine.generate_fills_report = lambda: pd.DataFrame([
        {"instrument_id": "A", "client_order_id": "1", "commission": "1 USD", "last_px": 100, "last_qty": 1, "ts_event": t},
        {"instrument_id": "B", "client_order_id": "2", "commission": "100 JPY", "last_px": 10000, "last_qty": 1, "ts_event": t}])
    fills = made.fill_rows()
    assert made.cost_totals(fills, equity)["fees"] == 2
    assert made.daily_costs(DAYS).iloc[0] == 2
    held = pd.DataFrame({"A": 1., "B": 1.}, index=DAYS)
    closes = pd.DataFrame({"A": 100., "B": 10000.}, index=DAYS)
    assert made.exposure(held, closes, pd.Series(1000., index=DAYS), fills)["gross"].iloc[0] == pytest.approx(.2)
    assert sum(row["notional"] for row in fills) == 200


def test_initial_capital_cannot_use_day_three_fx(tmp_path):
    made = report(tmp_path, fx=[(DAYS[-1].value // MS, .01)])
    with pytest.raises(Missing, match="JPY.*USD"):
        made.equity_series()


def test_incomplete_mark_is_unknown_in_equity_and_drawdown(tmp_path):
    made = report(tmp_path, incomplete=True)
    equity = made.equity_series()
    assert pd.isna(equity.loc[DAYS[1]])  # USD 30 omits an unpriced holding
    stats = made.statistics(equity, None, None, [], None, [])
    assert stats["maxDrawdown"] is None


def test_equal_weight_counts_reopening_return(tmp_path):
    made = report(tmp_path)
    made.invocation["benchmark"] = {"kind": "equal"}
    made.closes = lambda: pd.DataFrame({"A": [100, None, 120], "B": [100., 100., 100.]}, index=DAYS)
    made.first_opens = lambda: pd.Series({"A": 100., "B": 100.})
    level, _ = made.benchmark_series(DAYS.insert(0, DAYS[0] - pd.Timedelta(days=1)))
    assert level.tolist() == pytest.approx([1, 1, 1, 1.1])


def test_heldout_beta_is_fixed_before_scoring(tmp_path):
    made = report(tmp_path)
    days = pd.date_range(DAYS[0], periods=7, tz="UTC")
    daily = pd.Series([0, .01, -.01, .02, -.02, .03, -.03], index=days)
    market = (1 + daily).cumprod()
    made.invocation["heldOutFrom"] = days[4].value // MS
    daily.iloc[4:] *= 4
    level, beta = made.beta_benchmark(market, daily, daily * 0)
    assert beta == pytest.approx(1)
    assert level.tolist() == pytest.approx(market.tolist())


def test_risk_free_is_unknown_before_first_observation(tmp_path):
    made = report(tmp_path)
    days = pd.date_range(DAYS[0], periods=80, tz="UTC")
    made.invocation["riskFree"] = {"basis": "irx", "rows": [[days[40].value // MS, 5.04]]}
    rf, metadata = made.risk_free(days)
    assert rf.iloc[:40].isna().all() and rf.iloc[40:].tolist() == pytest.approx([.0002] * 40)
    assert metadata.get("meanPct") is None
    equity = pd.Series([100 + i + (i % 2) for i in range(81)])
    stats = made.statistics(equity, rf, None, [{"pnl": 1, "exit": 1}], None, [])
    assert stats["sharpe"] is None and stats["sortino"] is None

def test_conversion_preparation_rejects_future_only_quotes(tmp_path, monkeypatch):
    since = DAYS[0].value // MS
    instrument = {"instrumentId": "JPYUSD"}
    async def resolve(ctx, name):
        return instrument
    async def series(ctx, item, timeframe):
        return item, {}
    async def closes(ctx, item, selected, start, until, owner, *, clock):
        assert start < since and clock == "knownAt"
        return [[since + 86_400_000, .01]]
    monkeypatch.setattr(object_data, "resolve_text", resolve)
    monkeypatch.setattr(object_data, "resolve_series", series)
    monkeypatch.setattr(strategy_inputs, "daily_closes", closes)
    with pytest.raises(ContractError, match="JPY/USD") as exc:
        asyncio.run(strategy_inputs.conversions(None, ["USD", "JPY"], "USD", since, since + 2 * 86_400_000, tmp_path, "run"))
    assert exc.value.code == "no_conversion" and exc.value.details["at"] == since


def test_daily_report_keeps_equity_when_no_irx_observation_exists(tmp_path):
    (tmp_path / "work.json").write_text(json.dumps({"dailyReturns": [[1, .02, None]], "equity": [[1, 102.0]],
                                                   "dailyRiskFree": None}), encoding="utf-8")
    services = SimpleNamespace(runs=SimpleNamespace(directory=lambda _: tmp_path),
                               strategy=SimpleNamespace(folder=lambda _: tmp_path, shown=lambda r: r))
    row, = public_runs.daily_records(services, {"id": "run", "objectKind": "strategy"})
    assert (row["equity"], row["return"], row["riskFree"]) == (102, .02, None)
