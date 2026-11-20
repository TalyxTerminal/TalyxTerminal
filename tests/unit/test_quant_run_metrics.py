"""Headline metrics use the account's settlement currency, including spot accounts."""

from modules.quant.backend.object_history import ACCOUNT_FIELDS, metrics_of


def test_spot_win_rate_uses_settlement_currency_after_empty_inventory_statistics():
    account = {key: 0 for key in ACCOUNT_FIELDS}
    account.update(account="GATE-001", currency="USDT")
    native = {"total_orders": 8, "total_positions": 4, "accountPerformance": [account],
              "stats_pnls": {"BTC": {"Win Rate": None}, "USDT": {"Win Rate": 0.0}}}
    assert metrics_of({"metrics": {}, "units": [{"nativeStatistics": native}]})["winRate"] == 0.0


def test_multiple_account_win_rates_keep_their_currency_identity():
    accounts = [{**dict.fromkeys(ACCOUNT_FIELDS, 0), "account": "VENUE-001", "currency": currency}
                for currency in ("USD", "USDT")]
    native = {"total_orders": 8, "total_positions": 4, "accountPerformance": accounts,
              "stats_pnls": {"USD": {"Win Rate": .25}, "USDT": {"Win Rate": .75}}}
    values = metrics_of({"metrics": {}, "units": [{"nativeStatistics": native}]})
    assert values["VENUE-001.USD.winRate"] == .25
    assert values["VENUE-001.USDT.winRate"] == .75


def test_a_strategy_run_gives_only_the_numbers_its_report_stored():
    # the generic read gave sharpe and dailySharpe (other day boundary), maxDrawdown and maximumDrawdown (intraday), and the engine's win rate
    # with the trade still open in it, side by side; the Agent could pick the wrong one.
    account = {**dict.fromkeys(ACCOUNT_FIELDS, 0), "account": "VENUE-001", "currency": "USD", "dailySharpe": -0.888, "maximumDrawdown": -0.3623}
    native = {"total_orders": 8, "total_positions": 4, "accountPerformance": [account], "stats_pnls": {"USD": {"Win Rate": 0.3145}}}
    stats = {"sharpe": -0.902, "maxDrawdown": -0.3611, "winRate": 0.3165, "trades": 159, "sortino": None}
    values = metrics_of({"metrics": {}, "units": [{"nativeStatistics": native}], "strategyReport": {"stats": stats}})
    assert values == {"sharpe": -0.902, "maxDrawdown": -0.3611, "winRate": 0.3165, "trades": 159}
