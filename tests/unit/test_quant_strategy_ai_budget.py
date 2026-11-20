"""Every strategy-run read the AI may call fits the Host's AI response budget on a realistic run, and still says what the AI acts on.

On 70 names over five months each read was 30 to 770 KB ("Response exceeds AI budget": the Agent could read nothing of a backtest). Here: a
portfolio strategy over 600 names and five years of days, 12,000 trades, 20,000 orders, 260 rebalances (weekly), a 1,000-line log and the code's own
series, its output files in the report's shape (scaled up from the synthetic multi-instrument run), called as the AI through the same action
handlers the platform calls. The page in the app keeps the full answers."""
from __future__ import annotations

import asyncio
import json
import random
from types import SimpleNamespace

import pandas as pd
import pytest
from talyx_sdk import wire

from host.ai import ANSWER_LIMIT as RESPONSE_LIMIT
from modules.quant.backend.strategy_views import SECTIONS
from tests.fixtures.strategy.fakes import RUNS, insert_run, services, strategy

N = 600
NAMES = [f"S{number:04d}" for number in range(N)]
DAYS = [int(moment.timestamp() * 1000) for moment in pd.bdate_range("2021-09-01", "2026-09-01", tz="UTC")]
TRADES, ORDERS, REBALANCES, HELD = 12_000, 20_000, 260, 50  # a weekly rebalance
AI = SimpleNamespace(caller=SimpleNamespace(origin="ai", module=None, workspace_id="ws", ai={"client": "Agent"}, run=None))
PERSON = SimpleNamespace(caller=SimpleNamespace(origin="user", module=None, workspace_id="ws", ai=None, run=None))


def instrument(number):
    """A data-settings symbol as the store keeps it: the whole instrument object."""
    symbol = NAMES[number]
    return {"instrument": {"instrumentId": f"vi2_{number:032x}", "identityVersion": 2, "venueId": "US", "mic": "XNYS", "currency": "USD", "securityType": "common_stock", "symbol": symbol, "displaySymbol": symbol,
                           "marketType": "spot", "assetClass": "equity", "base": symbol, "quote": "USD", "settle": None, "contractOrientation": "none", "contractSize": None,
                           "expiry": None, "strike": None, "optionSide": None, "underlyingSymbol": None, "calendarId": "XNYS", "adjustmentEligible": True}}


def walk(rng, start, step=.01):
    level, out = start, []
    for _ in DAYS:
        level *= 1 + rng.gauss(.0003, step)
        out.append(round(level, 6))
    return out


def day(moment):
    return pd.Timestamp(moment, unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S+00:00")


def trades(rng):
    rows = []
    for number in range(TRADES):
        entry = DAYS[number * (len(DAYS) - 10) // TRADES] + 72_000_000
        still = number >= TRADES - HELD
        price, pnl = 20 + 200 * rng.random(), round(rng.gauss(10, 400), 2)
        rows.append({"id": number + 1, "instrument": NAMES[number % N], "nativeInstrument": NAMES[number % N] + "-XNYS.SIM_USD", "side": "Long", "entry": entry,
                     "exit": None if still else entry + 5 * 86_400_000, "entryPrice": price, "exitPrice": None if still else price * (1 + pnl / 20_000),
                     "markPrice": price if still else None, "exitBy": "open at the end" if still else "signal", "qty": float(rng.randint(5, 400)), "pnl": pnl,
                     "ret": round(pnl / 20_000, 6), "fees": 2.0, "spread": round(rng.random() * 5, 6), "impact": round(rng.random(), 6), "funding": 0.0,
                     "mae": -round(rng.random() / 10, 6), "mfe": round(rng.random() / 10, 6), "bars": 5, "days": 5.0})
    return rows


def orders(rng):
    rows = []
    for number in range(ORDERS):
        moment = DAYS[number * (len(DAYS) - 1) // ORDERS] + 72_000_000
        missed = number % 97 == 0
        rows.append({"id": f"O-{number:06d}", "time": moment, "instrument": NAMES[number % N], "side": "Buy" if number % 2 else "Sell", "type": "Market",
                     "qty": float(rng.randint(5, 400)), "filled": 0.0 if missed else 1.0, "price": round(20 + 200 * rng.random(), 10), "trigger": None, "tif": "GTC",
                     "status": "Rejected" if missed else "Filled", "filledAt": None if missed else float(moment + 86_400_000),
                     "reason": "No bar: halted or not trading, the position is kept." if missed else None, "linked": None, "tags": '["entry:rebalance"]'})
    return rows


def rebalance(rng, at, number):
    held = {NAMES[(number * 7 + offset) % N] for offset in range(HELD)}
    orders_ = [{"name": NAMES[(number * 7 + offset) % N], "side": "Buy", "qty": float(rng.randint(5, 400)), "filled": 10.0, "price": 50.0,
                "status": "Rejected" if offset == 0 and number % 9 == 0 else "Filled", "reason": None} for offset in range(100)]
    return {"at": at, "targets": {name: (.019 if name in held else 0.0) for name in NAMES}, "ranks": {}, "values": {}, "actual": {name: (round(.019 + rng.gauss(0, .001), 6) if name in held else 0.0) for name in NAMES},
            "orders": orders_, "count": len(orders_), "unfilled": [{"name": order["name"], "reason": "halted"} for order in orders_ if order["status"] != "Filled"], "skipped": None}


def line(x, y, name, role="main"):
    return {"type": "scatter", "mode": "lines", "x": x, "y": y, "name": name, "meta": {"role": role}, "hovertemplate": "<b>%{x}</b><br>" + name + ": %{y}<extra></extra>"}


def figure(key, *traces):
    return {"data": list(traces), "layout": {"meta": {"key": key}, "xaxis": {}, "yaxis": {}}}


def write_run(target, rng, trade_rows, order_rows):
    """The run's output files in the report's shape, scaled up: the synthetic multi-instrument run's files with the long parts five years and 600 names long."""
    work = json.loads((RUNS / "multi" / "work.json").read_text(encoding="utf-8"))
    results = json.loads((RUNS / "multi" / "results.json").read_text(encoding="utf-8"))
    level, bench = walk(rng, 100_000), walk(rng, 100_000, .008)
    peak, fall = 0.0, []
    for value in level:
        peak = max(peak, value)
        fall.append(round(value / peak - 1, 6))
    stamps = [day(moment) for moment in DAYS]
    plots = [{"name": f"signal {number}", "pane": "own", "style": "line", "t": DAYS, "values": walk(rng, 1, .02)} for number in range(3)]
    rebalance_days = DAYS[-REBALANCES * 5::5]
    work.update({
        "equity": [[moment, value] for moment, value in zip(DAYS, level, strict=True)], "drawdown": [[moment, value] for moment, value in zip(DAYS, fall, strict=True)],
        "benchmark": [[moment, value] for moment, value in zip(DAYS, bench, strict=True)], "dailyReturns": [[moment, rng.gauss(0, .01), rng.gauss(0, .008)] for moment in DAYS],
        "exposure": [[moment, .95, .95] for moment in DAYS], "tradesTotal": TRADES,
        "log": [{"t": row["entry"], "instrument": row["instrument"], "kind": "Trade", "trade": row["id"], "text": f"Long {row['qty']} at {row['entryPrice']}"} for row in trade_rows[:950]]
        + [{"t": DAYS[number * 20], "instrument": NAMES[number], "kind": "Not filled", "text": "Sell 10 market: No bar: halted or not trading, the position is kept."} for number in range(50)],
        "logTotal": 30_000, "instruments": [{"id": name + "-XNYS.SIM_USD", "symbol": name, "type": "equity"} for name in NAMES], "plots": plots,
        "userStats": {f"stat {number}": rng.random() for number in range(40)}})
    work["log"].sort(key=lambda row: row["t"])
    by_name = sorted(({"name": name, "nativeInstrument": name + "-XNYS.SIM_USD", "sector": "Technology", "pnl": pnl, "fees": 30.0, "trades": 20, "gross": pnl + 30}
                      for name, pnl in ((name, round(rng.gauss(0, 2000), 2)) for name in NAMES)), key=lambda row: -row["pnl"])
    rebalances = [rebalance(rng, at, number) for number, at in enumerate(rebalance_days)]
    decisions = [{"t": at, "state": "skipped" if number % 25 == 0 else "rebalanced", "reason": "no signal yet" if number % 25 == 0 else None} for number, at in enumerate(rebalance_days)]
    work["multi"] = {"rebalancesTotal": REBALANCES,
                     "turnover": [[moment, .3] for moment in DAYS], "unfilled": [[at, 1] for number, at in enumerate(rebalance_days) if number % 9 == 0],
                     "holdings": [{"name": NAMES[offset], "nativeInstrument": NAMES[offset] + "-XNYS.SIM_USD", "sector": "Technology", "weight": .019, "pnl": 10.0, "days": 3.0} for offset in range(HELD)],
                     "holdingsTotal": HELD, "contributions": {"top": by_name[:10], "bottom": by_name[-10:][::-1], "all": len(by_name)}, "byInstrument": by_name}
    sections = results["sections"]
    sections["equity"].update({"figures": {"equity": figure("strategy.equity", line(stamps, [value / 100_000 for value in level], "#1"), line(stamps, [value / 100_000 for value in bench], "S&P 500", "benchmark")),
                                           "drawdown": figure("strategy.drawdown", line(stamps, fall, "#1"))},
                               "drawdowns": [{"depth": -.3 + number / 200, "peak": stamps[number * 30][:10], "trough": stamps[number * 30 + 10][:10], "recovered": stamps[number * 30 + 20][:10], "days": 20}
                                             for number in range(40)]})
    sections["rolling"]["figures"] = {name: figure("strategy.rolling" + name.title(), line(stamps, walk(rng, 1), "#1"), line(stamps, walk(rng, 1), "S&P 500", "benchmark")) for name in ("sharpe", "beta", "volatility")}
    sections["longShort"]["figures"] = {"exposure": figure("strategy.exposure", line(stamps, [.95] * len(DAYS), "Gross", "group"), line(stamps, [.95] * len(DAYS), "Net")),
                                        "turnover": figure("strategy.turnover", line(stamps, [30.0] * len(DAYS), "Turnover"))}
    sections["periods"]["table"] = [{"year": year, "months": [round(rng.gauss(.01, .04), 6) for _ in range(12)], "total": .1, "benchmark": .08} for year in range(2021, 2027)]
    sections["trades"] = {"statistics": {"averageWin": 300.0, "averageLoss": -280.0, "expectancy": 10.0}, "byExit": [{"exit": "signal", "trades": TRADES - HELD, "pnl": 1000.0}], "count": TRADES - HELD,
                          "figures": {key: {"data": [{"type": "scatter", "mode": "markers", "x": [row["ret"] for row in trade_rows], "y": [row[key] for row in trade_rows], "name": key.upper()}],
                                            "layout": {"meta": {"key": "strategy." + key}}} for key in ("mae", "mfe")}}
    results["missing"] = {}
    sections["orders"] = {"counts": {"Filled": sum(row["status"] == "Filled" for row in order_rows), "Rejected": sum(row["status"] == "Rejected" for row in order_rows)}, "total": ORDERS}
    sections["account"].update({"points": [{"t": moment, "balance": value, "marginUsed": 0.0, "equity": value, "leverage": .95} for moment, value in zip(DAYS, level, strict=True)],
                                "figures": {"balance": figure("strategy.account", line(stamps, level, "Balance")), "leverage": figure("strategy.leverage", line(stamps, [.95] * len(DAYS), "Leverage"))}})
    sections["plots"] = {"series": plots, "stats": work["userStats"]}
    sections["sectors"] = {"mostMade": by_name[:10], "mostLost": by_name[-10:], "sectors": [{"sector": f"Sector {number}", "net": 100.0 * number, "gross": 200.0, "costs": 100.0, "names": 50} for number in range(11)]}
    folder = target / "output" / "strategy"
    (folder / "work.json").write_text(json.dumps(work), encoding="utf-8")
    (folder / "results.json").write_text(json.dumps(results), encoding="utf-8")
    pd.DataFrame(trade_rows).to_parquet(folder / "trades.parquet")
    pd.DataFrame(order_rows).to_parquet(folder / "orders.parquet")
    # As the report writes a table's lists and dicts: JSON text.
    pd.DataFrame([{key: json.dumps(value) if isinstance(value, (list, dict)) else value for key, value in row.items()} for row in rebalances]).to_parquet(folder / "rebalances.parquet")
    pd.DataFrame(decisions).to_parquet(folder / "decisions.parquet")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from modules.quant.backend.object_routes import handle_actions
    rng = random.Random(11)
    ctx, records, shell = services(tmp_path_factory.mktemp("strategy"))
    handlers = {}
    ctx.handlers = handlers
    handle_actions(ctx, shell)
    data = {"symbols": [instrument(number) for number in range(N)], "timeframe": "1d", "since": "2021-09-01", "until": "2026-09-01"}
    obj = asyncio.run(strategy(shell, data=data, name="Momentum 600"))
    run = insert_run(shell, obj, "multi", params={"top": HELD})
    trade_rows, order_rows = trades(rng), orders(rng)
    write_run(shell.runs.directory(run["id"]), rng, trade_rows, order_rows)
    yield SimpleNamespace(shell=shell, handlers=handlers, run=run["id"], trades=trade_rows, orders=order_rows)
    records.close()


def call(world, action, args, caller=AI):
    return asyncio.run(world.handlers[action](args, caller))


def fits(out):
    """What the Host measures: the whole envelope of the answer, as the Host counts it (the characters of the encoded answer)."""
    return len(wire.encode(out).decode("utf-8")) < RESPONSE_LIMIT


def figures_in(value):
    if isinstance(value, dict):
        return any(key in ("figure", "figures") or (key == "data" and "layout" in value) or figures_in(item) for key, item in value.items())
    return isinstance(value, list) and any(figures_in(item) for item in value)


def test_all_symbol_settings_fit_and_keep_frozen_order(world):
    summary = call(world,"read_run",{"runId":world.run})
    assert fits(summary) and summary == call(world,"read_run",{"runId":world.run},PERSON)
    assert summary["settings"]["data"]["symbols"] == NAMES


def test_every_strategy_report_section_fits_separately(world):
    for section in SECTIONS:
        part = {"summary":"statistics","trades":"tradeStats","orders":"orderStats"}.get(section,section)
        found = call(world,"read_run",{"runId":world.run,"part":part})
        assert fits(found) and not figures_in(found), section


def test_strategy_record_pages_preserve_rows_filters_and_continuations(world):
    first = call(world,"read_run_table",{"runId":world.run,"table":"trades"})
    assert fits(first) and first["total"] == TRADES and first["nextCursor"] == str(len(first["rows"]))
    after = call(world,"read_run_table",{"runId":world.run,"table":"trades","cursor":first["nextCursor"]})
    columns = [c["name"] for c in after["columns"]]
    assert after["rows"][0][columns.index("id")] == len(first["rows"])+1
    for name,filters in [("trades",{"losing":True}),("orders",{"status":"Rejected"}),("rebalances",{"only":"unfilled"})]:
        query = {"runId":world.run,"table":name,"filter":filters}
        page = call(world,"read_run_table",query)
        assert fits(page) and page == call(world,"read_run_table",query,PERSON)
        if name == "trades":
            pnl = [c["name"] for c in page["columns"]].index("pnl")
            assert all(row[pnl]<0 for row in page["rows"])

def test_the_page_opens_a_run_without_its_long_series_and_tables(world):
    """The page's own read (the private route) of the 600-name run: the numbers, checks and main figure only; it was 11.8 MB on an S&P 500 run,
    nearly all of it the rebalances, trades and orders the page reads a page at a time."""
    views = world.shell.strategy
    page = views.page_run(world.run)
    work = page["work"]
    assert len(json.dumps(work)) < 300_000  # the run beside it carries its data settings: here 600 symbols, each its whole instrument
    assert work["span"] == [DAYS[0], DAYS[-1]] and work["instrument"]["symbol"] == NAMES[0] and work["instrumentCount"] == N
    assert work["multi"]["rebalancesTotal"] == REBALANCES and work["figures"]["equity"]["data"] and work["stats"] == views.run(world.run)["work"]["stats"]
    assert not {"equity", "trades", "log", "dailyReturns", "exposure", "instruments"} & set(work)
    assert views.trades(world.run, 0, 500, id=7)["rows"][0]["id"] == 7


def test_full_figures_still_reach_the_private_page(world):
    views = world.shell.strategy
    assert views.run(world.run)["work"]["figures"]["equity"]["data"]
    assert views.results(world.run,"orders")["sections"]["orders"]
    assert len(views.trades(world.run,0,500)["rows"])==500

def test_a_sweep_that_shows_a_combination_reads_its_light_report(tmp_path):
    """A sweep's combination writes a light report (key numbers, equity, trades: no drawdown, no benchmark label); once one is selected the sweep
    shows it, and the read failed on the missing keys for the page and the AI alike."""
    from modules.quant.backend.object_routes import handle_actions
    ctx, records, shell = services(tmp_path)
    try:
        handlers = {}
        ctx.handlers = handlers
        handle_actions(ctx, shell)
        run = insert_run(shell, asyncio.run(strategy(shell)), "multi")
        path = shell.runs.directory(run["id"]) / "output" / "strategy" / "work.json"
        work = json.loads(path.read_text(encoding="utf-8"))
        light = {key: work[key] for key in ("kind", "currency", "capital", "annualization", "riskFree", "stats", "sharpeInputs", "equity", "benchmark", "tradesTotal", "costs", "notes")}
        path.write_text(json.dumps({**light, "light": True, "checks": {}}), encoding="utf-8")
        page = shell.strategy.run(run["id"])
        assert [trace["name"] for trace in page["work"]["figures"]["equity"]["data"]] == ["#1","Benchmark"]
        public = asyncio.run(handlers["read_run"]({"runId":run["id"]},AI))
        assert fits(public) and public["runId"] == run["id"]
    finally:
        records.close()
