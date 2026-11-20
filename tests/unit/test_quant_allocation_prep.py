"""An allocation's checks before a run: the pool, limits that cannot add up, the numbers, the risk budget; and the pool read from the data settings."""
import pytest
from talyx_sdk import ContractError

from modules.quant.backend import allocation_prep as P

POOL = [{"ticker": name, "group": group} for name, group in (("A", "Stocks"), ("B", "Stocks"), ("C", "Bonds"), ("D", "Bonds"))]


def refused(params, pool=POOL):
    with pytest.raises(ContractError) as error:
        P.check_values_before_run(params, pool)
    return error.value


def test_new_allocation_empty():
    error = refused({}, [])
    assert error.message == "The pool is empty" and error.details["fields"] == ["pool"]


def test_infeasible_limits_before_first_period():
    error = refused({"max_weight": "20", "short_cap": 0})
    assert "Limits can't add up to 100%" in error.message and "4 assets x 20% = 80%" in error.message and error.details["fields"] == ["max_weight"]
    error = refused({"group_limits": [{"group": "Stocks", "max": "30"}, {"group": "Bonds", "max": "30"}]})
    assert "the group limits allow 60% (Stocks 30%, Bonds 30%)" in error.message and "group_limits" in error.details["fields"]
    assert P.check_values_before_run({"max_weight": "25"}, POOL)  # four at 25% is exactly 100%: it adds up
    assert P.check_values_before_run({"max_weight": "20", "short_cap": 30}, POOL)  # with shorts the long side can reach past the cap


def test_a_limit_that_is_not_a_number_is_named():
    assert refused({"cardinality": "three"}).details["fields"] == ["cardinality"]


def test_risk_budget_shares_add_up():
    assert "Shares add up to 90%" in refused({"objective": "risk_budget", "budget": [{"group": "Stocks", "share": 60}, {"group": "Bonds", "share": 30}]}).message


def test_the_pool_comes_from_the_data_settings_with_the_users_groups():
    instrument = lambda symbol, asset_class: {"instrument": {"instrumentId": "vi1_" + symbol, "symbol": symbol, "displaySymbol": symbol, "assetClass": asset_class, "name": symbol, "currency": "USD"}}
    obj = {"settings": {"data": {"symbols": [instrument("SPY", "equity"), instrument("BTC/USDT", "crypto")]}}}
    pool = P.pool_of(obj, {"pool": [{"ticker": "SPY", "group": "Core"}]})
    assert [(row["ticker"], row["group"]) for row in pool] == [("SPY", "Core"), ("BTC/USDT", "Crypto")]
    assert P.pool_of(obj, {}, {"vi1_SPY": "SPY.US"})[0]["ticker"] == "SPY.US"  # the frozen bars' own column name wins


def test_the_standard_file_declares_what_the_page_shows():
    names = {item["name"] for item in P.standard_declarations()["parameters"]}
    assert {"objective", "risk_measure", "lookback", "max_weight", "group_limits", "long_cap", "cost_aware", "cost_multiple", "objective_parameters"} <= names
    assert not P.standard_declarations()["errors"]



def test_a_pool_saved_before_rows_said_what_they_are_still_reads():
    old = {"ticker": "SPY", "group": "Stocks", "currency": "USD", "from": "", "proxy": ""}
    params = P.values_of(None, {"settings": {"mode": "standard", "params": {"pool": [old]}}})
    assert params["pool"] == [{**old, "kind": "", "runId": ""}]
    assert [row["target"] for row in params["shocks"]] == ["Stocks", "Bonds", "BTC"]


def test_market_classes_give_the_default_groups_market_can_tell():
    """Market calls an ETF a `fund` (securityType `etf`) and has no bond class: a fund goes to Funds, crypto to Crypto, a stock to Stocks (checkup MK-01)."""
    instrument = lambda symbol, asset_class, security=None: {"instrument": {"instrumentId": "vi1_" + symbol, "symbol": symbol, "displaySymbol": symbol, "assetClass": asset_class,
                                                                          "securityType": security, "name": symbol, "currency": "USD"}}
    obj = {"settings": {"data": {"symbols": [instrument("SPY", "fund", "etf"), instrument("AAPL", "equity", "common_stock"), instrument("BTC/USDT", "crypto"), instrument("GC=F", "commodity")]}}}
    assert [row["group"] for row in P.pool_of(obj, {})] == ["Funds", "Stocks", "Crypto", "Commodities"]
    groups = {row["group"] for row in P.standard_declarations()["parameters"][[item["name"] for item in P.standard_declarations()["parameters"]].index("costs")]["default"]}
    assert {"Stocks", "Funds", "Crypto", "Commodities"} <= groups  # every default group has a default cost


def test_the_costs_are_the_pools_groups_as_charged():
    """The costs a run records are one row for each group of the pool, at the bps the backtest charges: a group without a row of its own costs what Other costs."""
    pool = [{"ticker": "A", "group": "Stocks"}, {"ticker": "B", "group": "Gold"}]
    rows = P.costs_of({"costs": [{"group": "Stocks", "bps": 5}, {"group": "Bonds", "bps": 5}, {"group": "Other", "bps": 10}]}, pool)
    assert rows == [{"group": "Stocks", "bps": 5.0}, {"group": "Gold", "bps": 10.0}]
    assert P.costs_of({"costs": [{"group": "Stocks", "bps": 5}]}, pool) == [{"group": "Stocks", "bps": 5.0}, {"group": "Gold", "bps": 0.0}]


def test_a_tracking_limit_or_objective_needs_the_60_40_in_the_pool():
    error = refused({"max_tracking": "4"})
    assert error.details["fields"] == ["max_tracking"] and "SPY and IEF" in error.message
    error = refused({"objective": "benchmark_tracking"})
    assert error.details["fields"] == ["objective"] and "SPY and IEF" in error.message
    pool = POOL + [{"ticker": "SPY", "group": "Funds"}, {"ticker": "IEF", "group": "Funds"}]
    assert P.check_values_before_run({"max_tracking": "4", "objective": "benchmark_tracking"}, pool)
