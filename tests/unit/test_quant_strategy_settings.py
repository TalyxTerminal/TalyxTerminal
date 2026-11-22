"""A strategy's two layers of settings: what the code declares, and what the platform adds (capital, exchange conditions, costs, fill, shorting,
benchmark, risk-free rate, held-out end). Defaults by what is traded, what applies to what, and the accounts of each venue."""

import pytest
from talyx_sdk import ContractError

from modules.quant.backend import strategy_settings as S

ACCOUNT = {}


def test_code_params_come_from_declarations():
    from modules.quant.backend.object_params import read_parameters
    source = 'import talyx_quant as q\n\nclass S(q.Strategy):\n    def __init__(self):\n        super().__init__()\n        self.a = q.param("a", 5, min=1, max=9, label="A")\n        self.b = q.param("b", True, label="B")\n'
    found = read_parameters(source)["parameters"]
    assert [(item["name"], item["default"], item["label"]) for item in found] == [("a", 5, "A"), ("b", True, "B")]
    # the platform layer holds none of the code's names: a parameter written there is refused
    with pytest.raises(ContractError):
        S.check({"a": 5})


@pytest.mark.parametrize("kind, capital, currency", [("perpetual", 10_000, "USDT"), ("spot", 10_000, "USDT"), ("stock", 100_000, "USD")])
def test_defaults_by_kind(kind, capital, currency):
    value = S.defaults(kind, {}, market="US" if kind == "stock" else None)
    assert (value["capital"], value["currency"]) == (capital, currency)


def test_exchange_conditions_by_kind():
    assert S.exchange_fields("perpetual") == ["leverage", "marginModel", "liquidation", "maintenancePct", "stopTrigger", "positionMode"]
    # a stock has one last price and one net position: no stop trigger, no position mode, and the margin line is read-only text
    assert S.exchange_fields("stock") == ["margin"] and S.margin_label("stock") == "Reg T, 2× overnight"
    assert S.exchange_fields("spot") == []
    stock = S.effective("stock", {"exchange": {"stopTrigger": "mark", "positionMode": "hedge"}}, ACCOUNT, market="US")
    assert stock["exchange"]["stopTrigger"] == "last" and stock["exchange"]["positionMode"] == "oneway"


def test_leverage_above_max_refused():
    with pytest.raises(ContractError, match=r"Max 100× for BTC perpetual on OKX \(Market\)"):
        S.check({"exchange": {"leverage": 150}}, limits={"maxLeverage": 100, "label": "BTC perpetual on OKX"})
    assert S.check({"exchange": {"leverage": 20}}, limits={"maxLeverage": 100, "label": "BTC perpetual on OKX"})["exchange"]["leverage"] == 20


def test_fill_has_no_same_bar_close_and_no_latency_or_queue_settings():
    with pytest.raises(ContractError, match="never the same one"):
        S.check({"fill": "sameClose"})
    for name in ("latency", "queue", "queuePosition", "liquidity", "dataType"):
        with pytest.raises(ContractError):
            S.check({name: 1})


def test_limit_orders_option_and_stock_shorting_borrow_default():
    assert S.check({"limitThroughTicks": 2})["limitThroughTicks"] == 2
    with pytest.raises(ContractError):
        S.check({"limitThroughTicks": -1})
    stock = S.defaults("stock", {}, market="US")
    assert stock["shorting"] == "allowed" and stock["costs"]["borrowPct"] == 0.3
    assert S.defaults("perpetual", {})["limitThroughTicks"] == 0 and "makerPct" in S.defaults("perpetual", {})["costs"]


def test_volume_cap_is_on_both_costs_and_defaults_to_none():
    for kind in ("perpetual", "stock"):
        assert "volumeCapPct" in S.defaults(kind, {}, market="US")["costs"] and S.defaults(kind, {}, market="US")["costs"]["volumeCapPct"] is None


def stock(venue, asset="equity"):
    return {"instrument": {"venueId": venue, "assetClass": asset}}


def test_benchmark_and_risk_free_follow_the_market_traded():
    """A shares measure against CSI 300 over a fixed 4% a year, US stocks against SPY over the 13-week T-bill, crypto against holding what it trades over zero."""
    cn = S.effective("stock", None, ACCOUNT, market=S.market_of({"symbols": [stock("CN"), stock("CN")]}))
    assert cn["benchmark"] == {"kind": "symbol", "symbol": "CN:000300.SH"} and cn["riskFree"] == {"basis": "fixed", "pct": 4.0}
    us = S.effective("stock", None, ACCOUNT, market=S.market_of({"symbols": [stock("US", "fund")]}))
    assert us["benchmark"] == {"kind": "symbol", "symbol": "US:SPY"} and us["riskFree"] == {"basis": "irx"}
    indexed = S.market_of({"index": stock("US", "index")})
    assert indexed == "US"
    crypto = S.effective("perpetual", None, ACCOUNT, market=S.market_of({"symbols": [{"instrument": {"venueId": "okx", "assetClass": "crypto"}}]}))
    assert crypto["benchmark"] == {"kind": "hold"} and crypto["riskFree"] == {"basis": "zero"}
    assert S.effective("spot", None, ACCOUNT)["riskFree"] == {"basis": "zero"}
    # what the user set wins: any Market symbol, and the A-share rate can be changed
    mine = S.effective("stock", {"benchmark": {"kind": "symbol", "symbol": "US:QQQ"}, "riskFree": {"basis": "fixed", "pct": 2.5}}, ACCOUNT, market="CN")
    assert mine["benchmark"]["symbol"] == "US:QQQ" and mine["riskFree"]["pct"] == 2.5


def test_benchmark_kinds_by_instrument():
    assert S.benchmark_options("perpetual", 1) == ["symbol", "hold", "none"]
    assert S.benchmark_options("stock", 20) == ["symbol", "equal", "none"]
    assert S.benchmark_options("stock", 1) == ["symbol", "hold", "none"]
    with pytest.raises(ContractError):
        S.check({"benchmark": {"kind": "beta"}})  # cash plus beta times the S&P 500 is gone
    with pytest.raises(ContractError):
        S.check({"benchmark": {"kind": "nonsense"}})
    with pytest.raises(ContractError):
        S.check({"benchmark": {"kind": "symbol"}})  # a symbol benchmark names its symbol
    assert S.check({"benchmark": {"kind": "symbol", "symbol": "CN:000300.SH"}})["benchmark"] == {"kind": "symbol", "symbol": "CN:000300.SH"}


def test_risk_free_basis_and_fixed_rate():
    assert S.defaults("stock", {}, market="US")["riskFree"] == {"basis": "irx"} and S.defaults("perpetual", {})["riskFree"] == {"basis": "zero"}
    assert S.check({"riskFree": {"basis": "fixed", "pct": 4.0}})["riskFree"] == {"basis": "fixed", "pct": 4.0}
    with pytest.raises(ContractError):
        S.check({"riskFree": {"basis": "fixed"}})


def test_accounts_per_venue():
    venues = [{"name": "OKX", "kind": "perpetual"}, {"name": "XNAS", "kind": "stock"}]
    value = S.effective("mixed", None, ACCOUNT, venues=venues)
    assert value["accounts"] == [{"venue": "OKX", "balances": ["10000 USDT"]}, {"venue": "XNAS", "balances": ["100000 USD"]}]
    assert value["reportCurrency"] == "USDT"
    stored = S.effective("mixed", {"accounts": [{"venue": "OKX", "balances": ["5000 USDT", "0.5 BTC"]}, {"venue": "XNAS", "balances": ["20000 USD"]}]}, ACCOUNT, venues=venues)
    assert stored["accounts"][0]["balances"] == ["5000 USDT", "0.5 BTC"]
    # a venue holding two currencies is a multi-currency cash account: no base currency
    venue = S.native_venue({"name": "OKX", "balances": ["1 USDT"], "baseCurrency": "USDT"}, stored, "perpetual")
    assert venue["balances"] == ["5000 USDT", "0.5 BTC"] and venue["baseCurrency"] is None
    one = S.effective("perpetual", None, ACCOUNT, venues=venues[:1])
    assert "accounts" not in one
    with pytest.raises(ContractError):
        S.check({"accounts": [{"venue": "OKX", "balances": ["lots"]}]})


def test_memory_estimate_is_within_a_quarter_of_measured_peaks():
    gib = 1024 ** 3
    for names, bars, peak in [(5, 2445, .26), (362, 174_811, 1.4), (559, 657_359, 2.27), (559, 1_369_917, 2.88)]:
        assert 1 <= S.memory_estimate(names, bars) / gib / peak <= 1.25


def test_run_settings_have_stable_keys():
    """The second layer's keys as the contract names them (the page and the sweeps read them)."""
    value = S.effective("perpetual", None, ACCOUNT)
    assert {"capital", "maxPosition", "exchange", "costs", "fill", "shorting", "benchmark", "riskFree", "heldOutFrom", "limitThroughTicks"} <= set(value)
    assert set(value["exchange"]) >= {"leverage", "maxLeverage", "marginModel", "liquidation", "maintenancePct", "stopTrigger", "positionMode"}
    assert set(value["costs"]) >= {"feePct", "makerPct", "halfSpreadBps", "impact", "volumeCapPct", "funding", "feeSource"}
