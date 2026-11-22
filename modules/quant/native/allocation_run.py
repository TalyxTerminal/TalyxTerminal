"""One allocation run in its own process: the frozen bars become daily returns, the walk-forward backtest runs period by period (each period
appended to `periods.jsonl` the moment it is done, so a stopped run keeps them), then the numbers, checks and every Results tab are written.

Started by the Host as `once` (see factor_engine.run_once); the answer is `answer.json` in the output folder.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path[:] = [path for path in sys.path if Path(path or os.getcwd()).resolve() != Path(__file__).resolve().parent]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from native import allocation_results as R  # noqa: E402
from native import allocation_work as W  # noqa: E402
from native import i18n
from native.allocation_holdings import benchmark_returns, hold_period, new_period  # noqa: E402
from native.allocation_prices import aligned_lookback, daily_returns  # noqa: E402
from native.figures import num  # noqa: E402


def write_json(path, value):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=str), encoding="utf-8")
    temporary.replace(path)


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return num(value, 8)
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def returns_of(job):
    """Daily returns of the pool from the frozen bars (see `daily_returns`), and in `info["benchmark"]` the benchmark's on the same days (`benchmark_daily`)."""
    bars = pd.read_parquet(job["data"]["barsPath"], columns=["label", "column", "close"])
    zones = job["data"].get("info", {}).get("sessionZones")
    returns, info = daily_returns(bars, [row["ticker"] for row in job["settings"].get("pool", [])], job["settings"], zones)
    info["benchmark"] = W.benchmark_daily(bars, job["settings"]["benchmarkColumns"], info["closes"].index, zones)
    return returns, info


RETURNS = {}  # frozen bars path -> (returns, info): the resident process keeps the newest two


def cached_returns(job):
    settings = job["settings"]
    key = (job["data"]["barsPath"], tuple(row["ticker"] for row in settings.get("pool", [])), settings.get("align"), str(settings.get("proxyColumns")), str(settings.get("fxColumns")),
           str(settings["benchmarkColumns"]))
    if key not in RETURNS:
        RETURNS[key] = returns_of(job)
        while len(RETURNS) > 2:
            RETURNS.pop(next(iter(RETURNS)))
    return RETURNS[key]


def too_few_periods(returns, settings, written, end):
    """None, or "Lookback 252 days leaves 2 periods before Jan 1, 2025; at least 12 needed" when the lookback leaves fewer rebalances than a backtest needs."""
    lookback = int(settings.get("lookback", 252))
    count = len([p for p in W.rebalance_days(returns.index, settings.get("frequency", "monthly")) if p + 1 >= lookback and p + 1 < len(returns)])
    if count >= 12:
        return None
    return f"Lookback {written if written is not None else lookback} days leaves {count} periods before {end:%b} {end.day}, {end.year}; at least 12 needed"


def spliced_text(info):
    """Where each proxied asset's own prices begin, as the data popover says it: "IEF before Jul 2002 from VFITX"."""
    return [f"{name} before {found['to']:%b %Y} from {found['proxy']}" for name, found in info.get("spliced", {}).items()]


def annualization(job, info):
    """Observations a year of the pool's returns: 52 or 12 when aligned weekly or monthly, else Market's calendar for the data."""
    return int(info.get("annualization") or job["data"]["info"]["annualization"])


def trial(job, fitter=None):
    """One period, the same fit as the backtest's: {ok, period, rows, seconds, ...}. Fitted at the close before `job.heldFrom` from the holding `job.previous` when
    they are given (the period of the run it is compared with, or a published period under review: the moment those weights were fitted); else at the newest
    rebalance close before the held-out part, as the backtest's last period. `job.pin` holds one asset at a weight and the rest are solved again."""
    settings = job["settings"]
    returns, info = cached_returns(job)
    if job.get("heldFrom") is not None:
        returns = returns[returns.index < pd.Timestamp(int(job["heldFrom"]), unit="ms").tz_localize(returns.index.tz)]
    else:
        if job.get("heldOutFrom"):
            returns = returns.loc[:pd.Timestamp(job["heldOutFrom"]) - pd.Timedelta(days=1)]
        ends = [p for p in W.rebalance_days(returns.index, settings.get("frequency", "monthly")) if p + 1 < len(returns)]
        if ends:
            returns = returns.iloc[:ends[-1] + 1]
    pool = list(returns.columns)
    settings = {**settings, "pool": [row for row in settings.get("pool", []) if row["ticker"] in pool], "lookback": aligned_lookback(settings),
                "annualization": annualization(job, info)}
    benchmark = info["benchmark"]
    try:
        W.check_feasible(settings, pool)
        if fitter is None:
            W.check_settings(settings, pool, benchmark)
        settings = W.with_rate(settings, W.risk_free_per_bar(job["riskFree"], returns.index, settings["annualization"]), len(returns) - 1)
    except ValueError as error:
        return {"ok": False, "code": "invalid_settings", "message": str(error), "fields": list(getattr(error, "fields", []))}
    problem = too_few_periods(returns, settings, job["settings"].get("lookback"), returns.index[-1] + pd.Timedelta(days=1))
    if problem:
        return {"ok": False, "code": "too_few_periods", "message": problem}
    previous = pd.Series(job["previous"]).reindex(pool).fillna(0.0).to_numpy() if job.get("previous") else None
    out = W.trial(returns, settings, previous=previous, benchmark=benchmark, pin=job.get("pin"), published=job.get("published"), fitter=fitter)
    return {"ok": True, **clean(out), "cutShort": info["cutShort"], "spliced": spliced_text(info)}


ACTIVE = {}  # while a code run executes: its returns and settings in, the backtest out


def estimator_error(estimator):
    """None when `estimator` is a skfolio optimization or a Pipeline that ends in one, else the message the run stops with."""
    from skfolio.optimization import BaseOptimization
    from sklearn.pipeline import Pipeline
    last = estimator.steps[-1][1] if isinstance(estimator, Pipeline) else estimator
    if not isinstance(last, BaseOptimization):
        return "estimator must be a skfolio optimization (or a Pipeline ending in one)"
    return None


def run_code(job, settings, **context):
    import runpy

    import talyx_quant as q

    ACTIVE.clear()
    ACTIVE.update(context)
    q._bind_parameters(settings)
    cutoff = None
    if job.get("heldOutFrom") and job.get("kind") != "heldout":
        cutoff = pd.Timestamp(job["heldOutFrom"]).value // 1_000_000 - 1
    if job.get("heldFrom") is not None:
        cutoff = int(job["heldFrom"]) - 1
    q._bind_research({"mode": "values", "research": job["data"], "cutoff": cutoff}, None)
    code = job["code"]
    sys.path[:0] = code["roots"]
    runpy.run_path(str(Path(code["source"]) / code["entry"]), run_name="__main__")
    if "result" not in ACTIVE:
        raise ValueError("The code never called q.allocate")
    return ACTIVE["result"]


def allocate(settings, estimator=None):
    """`q.allocate`: the walk-forward run of the standard flow (or of `estimator`), on the data the run gives; the weights of each period are kept as the run's
    `weights` publication. In a values run the returns end at the cutoff."""
    import talyx_quant as q
    if estimator is not None and (problem := estimator_error(estimator)):
        raise ValueError(problem)
    if "operation" in ACTIVE:
        job = ACTIVE["job_input"]
        returns, info = cached_returns(job)
        effective = {**job["settings"], **settings, "annualization": annualization(job, info)}
        fitter = (lambda window, previous: W.fit_custom(window, estimator, previous, effective, info["benchmark"])) if estimator is not None else None
        result = ACTIVE["operation"]({**job, "settings": effective}, fitter)
        ACTIVE["result"] = result
        return result
    if "returns" in ACTIVE:
        returns, annual, rate, benchmark = ACTIVE["returns"], ACTIVE["annualization"], ACTIVE["riskFree"], ACTIVE["benchmark"]
    else:
        data = q.data()
        pool = [row["ticker"] for row in settings.get("pool", [])]
        returns, info = daily_returns(data.bars, pool, settings, data.info.get("sessionZones"))
        # a notebook's q.allocate follows a benchmark only when its settings name the columns (`benchmarkColumns`) of bars it holds
        benchmark = W.benchmark_daily(data.bars, settings.get("benchmarkColumns"), info["closes"].index, data.info.get("sessionZones"))
        annual = int(info.get("annualization") or data.annualization)
        frozen_rate = (q._Run.invocation or {}).get("riskFree")
        rate = W.risk_free_per_bar(frozen_rate, returns.index, annual) if frozen_rate else 0.0
        settings = {**settings, "lookback": aligned_lookback(settings)}
    if q._Run.mode == "values" and q._Run.cutoff is not None:
        returns = returns.loc[:pd.to_datetime(q._Run.cutoff, unit="ms")]
    settings = {**settings, "pool": [row for row in settings.get("pool", []) if row["ticker"] in returns.columns] or [{"ticker": c, "group": "Other"} for c in returns.columns],
                "annualization": annual}
    returns = returns[[row["ticker"] for row in settings["pool"]]]
    fitter = None
    if estimator is not None:
        fitter = lambda window, previous: W.fit_custom(window, estimator, previous, settings, benchmark)  # noqa: E731
    else:
        W.check_settings(settings, list(returns.columns), benchmark)
    result = W.backtest(returns, settings, benchmark=benchmark, fitter=fitter, progress=ACTIVE.get("progress"), on_period=ACTIVE.get("on_period"), risk_free=rate)
    periods = result["periods"]
    if periods:   # each period's weights and the risk shares of the same fit (the library keeps both)
        tables = []
        for field, frame in zip(("weight", "riskShare"), R.periods_frame(periods), strict=True):
            long = frame.stack().rename("value").reset_index()
            long.columns = ["datetime", "instrument", "value"]
            index = pd.DatetimeIndex(long["datetime"]).tz_localize("UTC")
            tables.append(pd.DataFrame({"datetime": index.as_unit("ms").asi8, "instrument": long["instrument"].astype(str), "field": field, "value": long["value"].astype(float)}))
        q._capture("weights", "allocation", pd.concat(tables, ignore_index=True), field="weight")
    ACTIVE.update({"result": result, "settings": settings, "fitter": fitter, "estimator": repr(estimator)[:200] if estimator is not None else None})
    return result


def values(job, fitter=None):
    """What a published allocation makes for the bars after its cursor (op `allocation_values`): at each bar that starts a new rebalance period the same fit as every
    period of the backtest, on the `lookback` days before that bar and from the weights held until then, drifted; the other bars carry the weights. The rows are
    datetime (the bar the weights take effect), instrument (the column), field, value."""
    settings = job["settings"]
    returns, info = cached_returns(job)
    pool = list(returns.columns)
    settings = {**settings, "pool": [row for row in settings.get("pool", []) if row["ticker"] in pool], "annualization": annualization(job, info)}
    lookback, every = aligned_lookback(settings), job.get("every", "month")
    benchmark = info["benchmark"]   # a benchmark-tracking allocation goes on tracking the same benchmark
    if fitter is None:
        W.check_settings(settings, pool, benchmark)
    rate = W.risk_free_per_bar(job["riskFree"], returns.index, settings["annualization"])
    held = pd.Series(job["last"]["weights"]).reindex(pool).fillna(0.0) if job.get("last") else None  # what the version holds at its cursor
    shares = pd.Series(job["last"].get("riskShares") or {}).reindex(pool).fillna(0.0) if job.get("last") else None
    since = pd.Timestamp(job["last"]["label"], unit="ms").normalize() if job.get("last") else None
    previous = job.get("after")
    rows, notes = [], {}
    for label in job["labels"]:
        stamp = pd.Timestamp(label, unit="ms").normalize()
        if new_period(label, previous, every):
            window = returns.loc[returns.index < stamp].iloc[-lookback:]
            if len(window) < lookback:
                continue
            drifted = None
            if held is not None and since is not None:
                start = int(returns.index.searchsorted(since))
                end = int(returns.index.searchsorted(stamp))
                _, _, drifted = hold_period(held, returns.iloc[start + 1:end])
            result = fitter(window, drifted) if fitter else W.fit_period(window, W.with_rate(settings, rate, int(returns.index.get_loc(window.index[-1]))),
                                                                        previous=drifted, benchmark=benchmark)
            held, since, shares = result["weights"], stamp, result["riskShare"]  # the shares from the same window and risk measure as the weights
            if result["note"]:   # a period not solved as set (limits relaxed, equal weight): the library shows how it ended
                notes[int(label)] = result["note"][0].upper() + result["note"][1:]
        elif held is None:
            continue
        previous = label
        rows += [(int(label), column, "weight", float(value)) for column, value in held.items()]
        rows += [(int(label), column, "riskShare", float(value)) for column, value in shares.items()]
    return {"ok": True, "rows": rows, "solver": notes}


def run(job):
    started = time.perf_counter()
    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    settings = job["settings"]
    (output / "periods.jsonl").unlink(missing_ok=True)
    returns, info = returns_of(job)
    pool = list(returns.columns)
    annual = annualization(job, info)
    settings = {**settings, "pool": [row for row in settings.get("pool", []) if row["ticker"] in pool], "lookback": aligned_lookback(settings), "annualization": annual}
    if job.get("warmup"):  # a sweep over the lookback: every cell starts where the longest lookback can
        settings["warmup"] = aligned_lookback({**settings, "lookback": job["warmup"]})
    held = job.get("heldOutFrom")
    kind = job.get("kind", "backtest")
    if held and kind == "backtest":  # the held-out part is never seen by an ordinary run
        returns = returns.loc[:pd.Timestamp(held) - pd.Timedelta(days=1)]
    if len(pool) < 1:
        raise ValueError("The pool is empty")
    lookback = int(settings.get("lookback", 252))
    problem = too_few_periods(returns, {**settings, "lookback": max(lookback, int(settings.get("warmup") or 0))}, job["settings"].get("lookback"),
                              pd.Timestamp(held) if held and kind == "backtest" else returns.index[-1] + pd.Timedelta(days=1))
    if problem:
        raise ValueError(problem)
    W.check_feasible(settings, pool)
    benchmark = info["benchmark"]
    if not job.get("code"):
        W.check_settings(settings, pool, benchmark)  # a setting that cannot work stops the run here, named, before any period
    rate = W.risk_free_per_bar(job["riskFree"], returns.index, annual)   # the run's risk-free rate an observation; None when Market has none

    def progress(done, total):
        with (output / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"stage": "backtest", "done": done, "of": total}) + "\n")

    def on_period(period):
        row = {"date": str(period["date"].date()), "heldFrom": str(period["heldFrom"].date()), "weights": {k: float(v) for k, v in period["weights"].items()},
               "riskShare": {k: float(v) for k, v in period["riskShare"].items()}, "turnover": period["turnover"], "cost": period["cost"],
               "status": period["status"], "note": period["note"], "bound": period["bound"], "seconds": period["seconds"]}
        with (output / "periods.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")

    if job.get("code"):  # the allocation is written as code: the file runs and its q.allocate does the walk-forward on these returns
        result = run_code(job, settings, returns=returns, annualization=annual, riskFree=rate, benchmark=benchmark, progress=progress, on_period=on_period)
        if ACTIVE.get("estimator"):
            settings = {**settings, "estimator": ACTIVE["estimator"]}
    else:
        result = W.backtest(returns, settings, benchmark=benchmark, progress=progress, on_period=on_period, risk_free=rate)
    periods, daily = result["periods"], result["daily"]
    if held and kind == "heldout":  # only the periods and days from the held-out start are this run's
        periods = [p for p in periods if p["heldFrom"] >= pd.Timestamp(held)]
        daily = daily.loc[pd.Timestamp(held):]
        if not periods:
            raise ValueError("The held-out period is too short for a period of its own")
    (1 + daily).cumprod().rename("equity").to_frame().to_parquet(output / "equity.parquet")
    closes = [period["heldFrom"] for period in result["periods"]]
    equal = benchmark_returns(returns, closes, settings).reindex(daily.index)
    six = None
    if {"SPY", "IEF"} <= set(pool):
        six = benchmark_returns(returns, closes, settings, {"SPY": 0.6, "IEF": 0.4}).reindex(daily.index)
    metrics = W.metrics(daily, benchmark_daily=six, equal_daily=equal, periods=periods, annual=annual, risk_free=rate)
    equal_metrics = W.metrics(equal, annual=annual, risk_free=rate)
    fitter = ACTIVE.get("fitter") if job.get("code") else None
    sens_detail = {}
    sens = W.sensitivity(returns, settings, benchmark, detail=sens_detail, annual=annual, fitter=fitter, base=result if kind == "backtest" else None, risk_free=rate)
    raw = returns
    returns = returns.fillna(0.0)   # from here on only reports read the returns: an asset that left earns nothing after its last price
    checks = W.checks(periods, metrics, equal_metrics, settings, sens, cut_short=info["cutShort"])
    lines = [("Allocation", daily, "main"), ("Equal weight", equal, "benchmark")] + ([("60/40", six, "benchmark")] if six is not None else [])
    weights, shares = R.periods_frame(periods)
    last = periods[-1]
    groups = W.groups_of(settings)
    order = list(dict.fromkeys(groups.get(a, "Other") for a in pool))
    weights_by_group = weights.T.groupby(lambda a: groups.get(a, "Other")).sum().T.reindex(columns=order)
    held_frame = weights.copy()   # what is held at the end of each period, drifted by the days up to the next rebalance (the last period's is its target)
    for k in range(len(periods) - 1):
        drifted = periods[k + 1].get("drifted")
        if drifted is not None:
            held_frame.iloc[k] = drifted.reindex(weights.columns).fillna(0.0).to_numpy()
    held_by_group = held_frame.T.groupby(lambda a: groups.get(a, "Other")).sum().T.reindex(columns=order)
    vol_figure = R.guarded(R.vol_calibration, periods, returns, lookback, annual, settings)
    last_window = raw.loc[:last["date"]].iloc[-lookback:].fillna(0.0)
    covariance = W.fitted_covariance(last_window, settings)   # the shocks move with the newest period's own estimate of risk
    results = {
        "summary": {"growth": R.growth_figure(lines), "drawdown": R.drawdown_figure(lines), "crises": R.crisis_table(lines), "years": R.year_rows(lines),
                    "notes": {"trackingError": float((daily - six).std() * np.sqrt(annual)) if six is not None and len(daily) > 2 else None,
                              "traded": sum(1 for p in periods[1:] if p["turnover"] > 5e-4), "periods": max(len(periods) - 1, 0), "to": str(daily.index[-1].date())}},
        "weights": {"weights": R.stacked("allocation.results.weights", weights, "Weights", groups=groups, order=order), "byGroup": R.stacked("allocation.results.weights", weights_by_group, "Weights", order=order),
                    "heldWeights": R.stacked("allocation.results.weights", held_frame, "Weights", groups=groups, order=order),
                    "heldByGroup": R.stacked("allocation.results.weights", held_by_group, "Weights", order=order),
                    "turnover": R.turnover_figure(periods), "periods": R.period_table(periods)},
        "risk": {"riskShares": R.stacked("allocation.results.risk_shares", shares, "Risk shares", groups=groups, order=order),
                 "riskSharesByGroup": R.stacked("allocation.results.risk_shares", shares.T.groupby(lambda a: groups.get(a, "Other")).sum().T.reindex(columns=order), "Risk shares", order=order),
                 "measures": R.risk_measures(daily, six, annual, vol_figure), "weightRisk": R.weight_risk_figure({"weights": last["weights"], "riskShare": last["riskShare"]}),
                 "corr": R.guarded(R.correlation_figure, returns.iloc[-lookback:]),
                 "volCalibration": vol_figure,
                 "frontier": R.guarded(R.frontier_figure, last["weights"], returns, settings, lookback, annual),
                 "scenarios": R.guarded(R.scenarios, last["weights"], returns, {"groups": groups, "rows": settings.get("shocks", [])}, covariance)},
        "returns": {"annual": R.annual(lines), "monthly": R.monthly(daily), "rolling": R.guarded(R.rolling, lines, annual, rate), "distribution": R.distribution(lines[:2]),
                    "contribution": R.guarded(R.contribution, result["held"].reindex(daily.index).fillna(0.0), returns)},
        "settings": {"settings": settings},
    }
    style = None
    cost_rows = []
    for label, multiple in (("None", 0.0), ("As set", None), ("2 ×", 2.0)):
        if multiple is None:
            cost_rows.append((label, metrics))
            continue
        other = W.backtest(raw, {**settings, "cost_multiple": multiple}, benchmark=benchmark, fitter=fitter, risk_free=W.risk_free_per_bar(job["riskFree"], raw.index, annual))
        other_daily = other["daily"].loc[daily.index[0]:]
        cost_rows.append((label, W.metrics(other_daily, periods=[p for p in other["periods"] if p["heldFrom"] >= periods[0]["heldFrom"]], annual=annual, risk_free=rate)))
    try:
        from native import styles
        stock_pool = [a for a in pool if groups.get(a, "Other") == "Stocks"]
        if not stock_pool:
            style = {"noStocks": True}
        else:
            amount, volume_why = None, None
            try:
                bars = pd.read_parquet(job["data"]["barsPath"], columns=["label", "column", "close", "volume"])
                bars["amount"] = bars["close"] * bars["volume"]
                wide = bars.pivot_table(index="label", columns="column", values="amount", aggfunc="last").sort_index()
                wide.index = pd.to_datetime(wide.index, unit="ms", utc=True).tz_localize(None).normalize()
                amount = wide[~wide.index.duplicated(keep="last")].reindex(returns.index)
            except Exception as error:  # noqa: BLE001 - without volume only the styles from prices, and the section says why
                amount, volume_why = None, R.reason_of(error)
            style = {"version": styles.VERSION, "rows": R.style_exposures(returns, result["held"].reindex(daily.index).fillna(0.0), groups, amount, annual, daily), "groups": R.brinson(result["held"].reindex(daily.index).fillna(0.0), returns, groups, annual) if six is not None else None,
                     "volumeWhy": volume_why}
    except Exception as error:  # noqa: BLE001
        style = {"why": R.reason_of(error)}
    results["style"] = style
    results["robust"] = {"costs": R.cost_table(cost_rows) if cost_rows else None,
                         "sensitivity": [{"name": name, "label": (sens_detail.get(name) or {}).get("label", name), "move": value, "sharpe": (sens_detail.get(name) or {}).get("sharpe"),
                                         "changed": (sens_detail.get(name) or {}).get("changed")} for name, value in (sens or {}).items()], "heldOut": None}
    holdout = None
    if kind == "heldout":  # the held-out stretch scored once: the lines, and the numbers of the allocation, 60/40 and equal weight over it
        holdout = {"from": str(daily.index[0].date()), "to": str(daily.index[-1].date()), "growth": R.growth_figure(lines, key="allocation.work.holdout"),
                   "rows": {"held": metrics, "sixty": W.metrics(six, annual=annual, risk_free=rate) if six is not None else None, "equal": equal_metrics}}
    (output / "results").mkdir(exist_ok=True)
    for name, body in results.items():
        write_json(output / "results" / f"{name}.json", clean(body))
    rows = [{"date": str(p["date"].date()), "heldFrom": str(p["heldFrom"].date()), **{f"w:{k}": float(v) for k, v in p["weights"].items()},
             **{f"r:{k}": float(v) for k, v in p["riskShare"].items()}, "turnover": p["turnover"], "cost": p["cost"], "status": p["status"], "note": p["note"],
             "bound": ",".join(f"{k}:{v}" for k, v in p["bound"].items())} for p in periods]
    pd.DataFrame(rows).to_parquet(output / "periods.parquet", index=False)
    # What a shock added later needs: the last weights, the covariance of the returns and the groups (read by `add_allocation_shock` without the research process).
    write_json(output / "shock-basis.json", {"names": list(returns.columns), "weights": {k: float(v) for k, v in last["weights"].items()}, "cov": covariance.tolist(), "groups": groups})
    notes = []
    if info["missing"]:
        notes.append(f"{sum(info['missing'].values())} missing closes ({', '.join(f'{k} {v}' for k, v in info['missing'].items())}): returns run across the gap")
    notes += info["cutShort"] + spliced_text(info)
    work = {"metrics": metrics, "equalMetrics": equal_metrics, "checks": checks, "notes": notes, "periods": len(periods), "from": str(daily.index[0].date()),
            "to": str(daily.index[-1].date()), "thisPeriod": W.period_rows(last, settings), "periodsFigure": R.stacked("allocation.work.periods", weights, "Weights", groups=groups, order=order),
            "periodsGroupFigure": R.stacked("allocation.work.periods", weights_by_group, "Weights", order=order), "periodDates": [str(p["heldFrom"].date()) for p in periods],
            "groups": list(weights_by_group.columns),
            "seconds": time.perf_counter() - started, "annualization": annual, "cutShort": info["cutShort"], "spliced": spliced_text(info), "holdout": holdout}
    write_json(output / "work.json", clean(work))
    return {"ok": True, "seconds": work["seconds"], "periods": len(periods)}


def main():
    invocation = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if invocation.get("support"):
        sys.path.insert(0, invocation["support"])
    job = invocation["job"]
    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    try:
        op = invocation.get("op", "run")
        if op == "run":
            answer = run(job)
        else:
            operation = {"allocation_trial": trial, "allocation_values": values}[op]
            answer = run_code(job, job["settings"], operation=operation, job_input=job)
    except Exception as error:  # noqa: BLE001
        traceback.print_exc()
        fields = list(getattr(error, "fields", []))
        answer = {"ok": False, "failure": {"stage": "settings" if fields else "compute", "title": i18n.t("stopped"), "message": str(error).splitlines()[0] if str(error) else type(error).__name__,
                                           "fields": fields, "traceback": traceback.format_exc()[-1500:]}}
    write_json(output / "answer.json", answer)
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    from native.allocation_run import main as worker_main

    raise SystemExit(worker_main())
