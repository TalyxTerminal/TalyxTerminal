import pandas as pd
import pytest

from modules.quant.native.model_performance import account_curve


def test_account_values_are_original_and_benchmark_compounds_from_initial_capital():
    report = pd.DataFrame({"account":[108,104],"return":[.1,-.02],"cost":[.02,.01],"bench":[.05,-.1]},index=pd.date_range("2025-01-01",periods=2))
    task={"record":[{"kwargs":{"config":{"backtest":{"account":100,"benchmark":"SPY","exchange_kwargs":{"deal_price":"close","open_cost":.0005,"close_cost":.001}}}}}]}
    curve,conditions=account_curve(report,task)
    assert curve.account.tolist()==[108,104]
    assert curve.benchmark_account.tolist()==pytest.approx([105,94.5])
    assert curve.net_value.tolist() == pytest.approx([1.08, 1.04])
    assert curve.benchmark_net_value.tolist() == pytest.approx([1.05, .945])
    assert conditions["openCost"]==.0005
    assert conditions["initialAccount"]==100


def test_missing_benchmark_and_non_cash_initial_accounts_do_not_invent_values():
    report=pd.DataFrame({"account":[100,101,102],"bench":[.1,float("nan"),.1]})
    task={"record":[{"kwargs":{"config":{"backtest":{"account":100,"benchmark":"SPY"}}}}]}
    curve,_=account_curve(report,task)
    assert curve.benchmark_account.iloc[0]==pytest.approx(110)
    assert curve.benchmark_account.iloc[1:].isna().all()
    report.attrs["benchmarkMeaning"]="No benchmark · zero-return reference"
    assert "benchmark_account" not in account_curve(report,task)[0]
    assert account_curve(report, task)[0].reference_net_value.tolist() == [1., 1., 1.]
    report.attrs.clear(); task["record"][0]["kwargs"]["config"]["backtest"]["account"]={"cash":100,"A":3}
    assert "benchmark_account" not in account_curve(report,task)[0]
