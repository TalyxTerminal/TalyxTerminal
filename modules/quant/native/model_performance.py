"""Presentation values derived from the original Qlib portfolio report."""
import math

import pandas as pd


def account_curve(report, task):
    records = [item for item in task.get("record", []) if isinstance(item, dict)]
    backtest = next((item.get("kwargs", {}).get("config", {}).get("backtest", {}) for item in reversed(records) if item.get("kwargs", {}).get("config", {}).get("backtest")), {})
    exchange = backtest.get("exchange_kwargs", {})
    initial = backtest.get("account")
    initial = float(initial) if isinstance(initial, (int, float)) and not isinstance(initial, bool) and math.isfinite(initial) and initial > 0 else None
    curve = pd.DataFrame(index=report.index.copy())
    curve.index.name = report.index.name or "datetime"
    if "account" in report:
        curve["account"] = report["account"]
    no_benchmark = bool(report.attrs.get("benchmarkMeaning")) or ("benchmark" in backtest and backtest["benchmark"] is None)
    if initial is not None and "bench" in report and not no_benchmark:
        # Benchmark observations are the original native report's period
        # returns. A missing observation breaks the path; it is never filled.
        curve["benchmark_account"] = initial * (1 + report["bench"]).cumprod(skipna=False)
    if initial is not None and "account" in curve:
        curve["net_value"] = curve["account"] / initial
        if "benchmark_account" in curve:
            curve["benchmark_net_value"] = curve["benchmark_account"] / initial
        elif no_benchmark:
            curve["reference_net_value"] = 1.0
    benchmark = backtest.get("benchmark")
    conditions = {"initialAccount": initial, "benchmark": None if no_benchmark else benchmark if isinstance(benchmark, str) else "Custom return series" if benchmark is not None else "Engine default benchmark",
                  "benchmarkMeaning": "No benchmark selected" if no_benchmark else "Native benchmark returns compounded from the same initial capital" if "benchmark_account" in curve else "Benchmark capital basis unavailable",
                  "accountMeaning": "Original account value after execution costs",
                  "netValueMeaning": "Original account divided by initial capital; native benchmark compounded from the same capital, or an explicitly labelled constant-one zero-return reference when no benchmark is selected",
                  "executionPrice": exchange.get("deal_price", "close"),
                  "frequency": exchange.get("freq", "day"), "openCost": exchange.get("open_cost"), "closeCost": exchange.get("close_cost"),
                  "minimumCost": exchange.get("min_cost"), "tradeUnit": exchange.get("trade_unit"),
                  "signalTiming": "The native TopkDropout strategy uses the previous observation score at each trade step" if any(item.get("kwargs", {}).get("config", {}).get("strategy", {}).get("class") == "TopkDropoutStrategy" for item in records) else "Decision timing is defined by the selected native strategy"}
    return curve, conditions
