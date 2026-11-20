"""A strategy run in the asset pool: its daily equity is the asset's price in the frozen bars; the pool keeps the row as the user put it."""
import asyncio

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import allocation_prep as P
from modules.quant.backend.allocation_runs import AllocationRuns

DAY = 86_400_000


def runs_with(equity):
    shell = AllocationRuns.__new__(AllocationRuns)
    shell.strategy_equity = lambda run_id: asyncio.sleep(0, equity.get(run_id, []))
    return shell


def frozen(tmp_path):
    path = tmp_path / "bars.parquet"
    pd.DataFrame({"label": [0, DAY, 2 * DAY], "column": "SPY", "close": [100.0, 101.0, 102.0]}).to_parquet(path)
    return {"barsPath": str(path), "info": {}}


def test_strategy_run_row_stays_in_the_pool():
    obj = {"settings": {"data": {"symbols": []}}}
    pool = P.pool_of(obj, {"pool": [{"ticker": "Breakout BTC #14", "kind": "run", "runId": "r14", "group": "Strategies"}, {"ticker": "stray", "kind": ""}]})
    assert [(row["ticker"], row["kind"], row["runId"]) for row in pool] == [("Breakout BTC #14", "run", "r14")]


def test_strategy_run_as_asset(tmp_path):
    shell = runs_with({"r14": [[0, 1.0], [DAY, 1.1], [2 * DAY, 1.05]]})
    pool = [{"ticker": "SPY"}, {"ticker": "Breakout BTC #14", "name": "Breakout BTC #14", "kind": "run", "runId": "r14"}]
    ready = asyncio.run(shell.with_links(frozen(tmp_path), pool, tmp_path))
    bars = pd.read_parquet(ready["barsPath"]).pivot(index="label", columns="column", values="close")
    assert list(bars["Breakout BTC #14"]) == [1.0, 1.1, 1.05] and list(bars["SPY"]) == [100.0, 101.0, 102.0]
    assert asyncio.run(shell.with_links(frozen(tmp_path), [{"ticker": "SPY"}], tmp_path))["barsPath"].endswith("bars.parquet")


def test_a_run_without_equity_is_named(tmp_path):
    with pytest.raises(ContractError, match="no daily equity"):
        asyncio.run(runs_with({}).with_links(frozen(tmp_path), [{"ticker": "x", "name": "x #1", "kind": "run", "runId": "gone"}], tmp_path))


def test_library_asset_reads_the_net_path_only_through_the_last_backtested_day(tmp_path):
    from modules.quant.native.allocation_holdings import hold_period

    dates = pd.date_range("1970-01-01", periods=4)
    changes = pd.Series([100, 110, 121, 133.1, 200]).pct_change()
    _, earlier, _ = hold_period([1], changes.iloc[1:3].to_frame())
    _, later, _ = hold_period([1], changes.iloc[3:4].to_frame())
    net = pd.Series([0, *earlier, *later], index=dates)
    shell = AllocationRuns.__new__(AllocationRuns)
    shell.records = type("R", (), {"root": tmp_path})()
    output = shell.directory("r1") / "output"
    output.mkdir(parents=True)
    (1 + net).cumprod().rename("equity").to_frame().to_parquet(output / "equity.parquet")
    series = shell.library_equity("r1")
    assert [day for day, _ in series] == [0, DAY, 2 * DAY, 3 * DAY]
    assert [level for _, level in series] == pytest.approx([1, 1.10, 1.21, 1.331])
    _, flat, _ = hold_period([.5, -.5], [[0, 0], [0, 0]])
    assert list(flat) == [0, 0]


def test_pool_candidates_await_strategy_equity(tmp_path):
    from tests.fixtures.strategy.fakes import services
    _, records, shell = services(tmp_path)
    try:
        records.put("qrun", {"id": "r", "objectId": "s", "workspaceId": shell.store.workspace(), "objectKind": "strategy", "runKind": "run", "state": "completed", "name": "Trend", "number": 2})
        shell.allocation.strategy_equity = runs_with({"r": [[0, 1], [DAY, 1.1]]}).strategy_equity
        assert asyncio.run(shell.allocation.link_candidates()) == [
            {"kind": "run", "runId": "r", "name": "Trend #2", "from": "1970-01-01", "to": "1970-01-02"}]
    finally:
        records.close()
