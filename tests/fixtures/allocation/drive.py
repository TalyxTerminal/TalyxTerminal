"""Runs in the research environment: python drive.py <case>; prints one JSON line. PYTHONPATH holds modules/quant and the repository root."""
import json
import sys
import time

import numpy as np
import pandas as pd
from native import allocation_work as W

from tests.fixtures.allocation import returns as R

case = sys.argv[1]
out = {}
r = R.make()
if case == "equal_reconcile":
    s = R.settings(objective="equal_weight")
    run = W.backtest(r, s)
    daily = run["daily"]
    # hand loop
    pos = W.rebalance_days(r.index, "monthly")
    pos = [p for p in pos if p + 1 >= 252 and p + 1 < len(r)]
    w = np.full(6, 1 / 6)
    manual = pd.Series(0.0, index=r.index)
    for k, p in enumerate(pos):
        w = np.full(6, 1 / 6)
        end = pos[k + 1] + 1 if k + 1 < len(pos) else len(r) - 1
        for t in range(p + 2, end + 1):
            x = r.iloc[t].to_numpy(); g = float(w @ x); manual.iloc[t] = g; w = w * (1 + x) / (1 + g)
    manual = manual.loc[daily.index]
    out["diff"] = float(np.abs((1 + daily).cumprod() - (1 + manual).cumprod()).max())
    m = W.metrics(daily, annual=252)
    out["compound_diff"] = abs(m["total"] - float((1 + daily).prod() - 1))
    out["periods"] = len(run["periods"])
elif case == "trial_last":
    s = R.settings(lookback=126)
    run = W.backtest(r, s)
    t = W.trial(r, s, previous=run["periods"][-2]["weights"].to_numpy() * 0 + 0 if False else None)
    last = run["periods"][-1]
    # the last backtest period fits the window ending at its rebalance day
    pos = r.index.get_loc(last["date"])
    t2 = W.trial(r.iloc[:pos + 1], s)
    out["same"] = max(abs(t2["rows"][i]["weight"] - last["weights"].iloc[i]) for i in range(6))
elif case == "risk_parity":
    s = R.settings(objective="risk_parity")
    res = W.fit_period(r.iloc[-252:], s)
    out["shares"] = list(res["riskShare"]); out["sum"] = float(res["riskShare"].sum())
elif case == "each_objective":
    for obj in ["equal_weight", "inverse_volatility", "min_risk", "max_ratio", "risk_parity", "max_diversification", "hrp", "herc", "nco", "schur"]:
        res = W.fit_period(r.iloc[-252:], R.settings(objective=obj))
        out[obj] = [res["status"], float(res["weights"].sum())]
elif case == "limits":
    res = W.fit_period(r.iloc[-252:], R.settings(max_weight=25, group_limits=[{"group": "Bonds", "max": 40}]))
    w = res["weights"]
    out["max"] = float(w.max()); out["bonds"] = float(w[["A1", "A2"]].sum()); out["status"] = res["status"]
    res = W.fit_period(r.iloc[-252:], R.settings(long_cap=130, short_cap=30, net=100))
    w = res["weights"]; out["neg"] = float(w[w < 0].sum()); out["pos"] = float(w[w > 0].sum()); out["net"] = float(w.sum()); out["status2"] = res["status"]
    res = W.fit_period(r.iloc[-252:], R.settings(cardinality=3, risk_measure="cvar"))
    out["count_cvar"] = int((res["weights"].abs() > 1e-6).sum()); out["status3"] = res["status"]
    res = W.fit_period(r.iloc[-252:], R.settings(cardinality=3, risk_measure="semi_deviation"))
    out["count_semi"] = int((res["weights"].abs() > 1e-6).sum()); out["status4"] = res["status"]
    res = W.fit_period(r.iloc[-252:], R.settings(linear="A1 + A2 <= 0.3"))
    out["linear"] = float(res["weights"][["A1", "A2"]].sum())
    res = W.fit_period(r.iloc[-252:], R.settings(risk_measure="cvar", objective="max_ratio", risk_cap=3))
    out["status5"] = res["status"]
elif case == "cost_aware":
    base = dict(objective="max_ratio", frequency="monthly", costs=[{"group": g, "bps": 20} for g in ("Bonds", "Stocks", "Other")])
    a = W.backtest(r, R.settings(cost_aware=True, **base)); b = W.backtest(r, R.settings(cost_aware=False, **base))
    out["on"] = sum(p["turnover"] for p in a["periods"]); out["off"] = sum(p["turnover"] for p in b["periods"])
elif case == "robust_reg_engine":
    w0 = W.fit_period(r.iloc[-252:], R.settings(objective="max_ratio", risk_measure="variance"))["weights"]
    w1 = W.fit_period(r.iloc[-252:], R.settings(objective="max_ratio", risk_measure="variance", robust_returns=True, robust_covariance=True))["weights"]
    out["differs"] = float(np.abs(w0 - w1).max())
    w2 = W.fit_period(r.iloc[-252:], R.settings(l2=0.5))["weights"]; w3 = W.fit_period(r.iloc[-252:], R.settings())["weights"]
    out["reg_differs"] = float(np.abs(w2 - w3).max())
    est = W.estimator(R.settings(engine_parameters="risk_free_rate=0.02"))
    out["engine"] = est.risk_free_rate
elif case == "missing_not_zero":
    rr = R.make(late_start=100)
    run = W.backtest(rr.iloc[100:], R.settings(lookback=252))
    out["zeros"] = int((rr.iloc[100:] == 0).sum().sum())
elif case == "scip_repeat":
    s = R.settings(cardinality=3, risk_measure="semi_deviation")
    t = time.time(); a = W.fit_period(r.iloc[-252:], s); out["seconds"] = time.time() - t
    b = W.fit_period(r.iloc[-252:], s)
    out["same"] = float(np.abs(a["weights"] - b["weights"]).max())
elif case == "worker":
    import pathlib
    root = pathlib.Path(sys.argv[2])
    rr = R.make(days=1500, seed=3)
    rr.columns = ["SPY", "IEF", "A3", "A4", "A5", "A6"]
    close = (1 + rr).cumprod() * 100
    labels = close.index.tz_localize("UTC").asi8 // 1_000_000
    bars = pd.DataFrame({"label": np.repeat(labels, 6), "column": np.tile(close.columns, len(close)), "close": close.to_numpy().ravel()})
    bars.to_parquet(root / "bars.parquet", index=False)
    pool = [{"ticker": c, "group": g} for c, g in zip(close.columns, ["Stocks", "Bonds", "Stocks", "Stocks", "Other", "Other"])]
    s = R.settings(pool=pool, objective="risk_parity", costs=[{"group": g, "bps": 5} for g in ("Bonds", "Stocks", "Other")])
    job = {"output": str(root / "out"), "data": {"barsPath": str(root / "bars.parquet"), "info": {"annualization": 252}}, "settings": s, "riskFree": {"basis": "zero"}}
    (root / "invocation.json").write_text(json.dumps({"mode": "once", "job": job}))
    from native import allocation_run
    sys.argv = ["x", str(root / "invocation.json")]
    code = allocation_run.main()
    ans = json.loads((root / "out" / "answer.json").read_text(encoding="utf-8"))
    out["answer"] = ans
    if ans["ok"]:
        work = json.loads((root / "out" / "work.json").read_text(encoding="utf-8"))
        out["metrics"] = work["metrics"]; out["checks"] = [(c["id"], c["state"], c["value"]) for c in work["checks"]]
        out["tabs"] = sorted(p.name for p in (root / "out" / "results").iterdir())
        out["sum"] = sum(r["riskShare"] for r in work["thisPeriod"])
        reads = {}
        for tab in (root / "out" / "results").iterdir():
            if tab.name in ("style.json", "robust.json"): continue
            body = json.loads(tab.read_text(encoding="utf-8"))
            for fig in body.values():
                if isinstance(fig, dict) and "data" in fig:
                    reads[fig["layout"]["meta"]["key"]] = sorted({t.get("hovertemplate") for t in fig["data"]} - {None})
        reads["allocation.work.periods"] = sorted({t.get("hovertemplate") for t in work["periodsFigure"]["data"]})
        out["reads"] = reads
        weights_fig = json.loads((root / "out" / "results" / "weights.json").read_text(encoding="utf-8"))
        out["stacks"] = {name: [(t["meta"]["role"], t["meta"]["index"], t["meta"].get("shade"), t["line"]["shape"], t["line"]["width"]) for t in weights_fig[name]["data"]] for name in ("weights", "byGroup")}
        out["work_stack"] = [(t["meta"]["role"], t["meta"]["index"], t["meta"].get("shade")) for t in work["periodsFigure"]["data"]]
        bar = weights_fig["turnover"]["data"][0]
        out["turnover"] = {"role": bar["meta"]["role"], "index": bar["meta"]["index"], "n": len(bar["x"]), "min": min(bar["y"]), "periods": work["periods"]}
        out["fig_keys"] = [t["meta"] for t in json.loads((root / "out" / "results" / "summary.json").read_text(encoding="utf-8"))["growth"]["data"]]
elif case == "custom_lifecycle":
    from pathlib import Path

    from native import allocation_run as worker
    from skfolio.optimization import MeanRisk

    root = Path(sys.argv[2])
    names = ["AAPL", "MSFT", "AMZN"]
    returns = R.make(days=340).iloc[:, :3].set_axis(names, axis=1)
    settings = R.settings(pool=[{"ticker": name, "group": "Stocks"} for name in names], lookback=60)
    estimator = MeanRisk(min_weights={"AAPL": 1}, max_weights={"MSFT": 0, "AMZN": 0})
    out["wrapper"] = W.fit_custom(returns.tail(60), estimator, settings=settings)["weights"].tolist()
    close = ((1 + returns).cumprod() * 100).rename_axis("label")
    close.index = close.index.as_unit("ms").asi8
    prices = close.rename_axis("label").reset_index().melt("label", var_name="column", value_name="close")
    prices.to_parquet(root / "bars.parquet")
    source = root / "source"
    source.mkdir()
    (source / "main.py").write_text("import talyx_quant as q\nfrom skfolio.optimization import MeanRisk\n"
                                    "q.allocate({'lookback': 60, 'pool': q.table('pool', [], columns=['ticker'])}, "
                                    "estimator=MeanRisk(min_weights={'AAPL': 1}, max_weights={'MSFT': 0, 'AMZN': 0}))", encoding="utf-8")
    code = {"source": str(source), "entry": "main.py", "roots": [str(source)]}
    job = {"output": str(root / "out"), "code": code, "settings": settings, "riskFree": {"basis": "zero"},
           "data": {"barsPath": str(root / "bars.parquet"), "info": {"annualization": 252}}}
    def invoke(op, **kwargs):
        import subprocess
        invocation = root / "invocation.json"
        invocation.write_text(json.dumps({"op": op, "job": {**job, **kwargs}}))
        subprocess.run([sys.executable, "-B", worker.__file__, str(invocation)], check=True)
        return json.loads((root / "out" / "answer.json").read_text(encoding="utf-8"))
    trial = invoke("allocation_trial")
    out["trial"] = {r["asset"]: r["weight"] for r in trial["rows"]}
    invoke("run")
    periods = pd.read_parquet(root / "out" / "periods.parquet")
    out["backtest"] = periods[["w:" + name for name in names]].to_numpy().tolist()
    cut = pd.Timestamp(periods.heldFrom.iloc[-1]).value // 1_000_000
    price_cut = prices[prices.label <= cut]
    price_cut.to_parquet(root / "cut.parquet")
    invoke("run", data={**job["data"], "barsPath": str(root / "cut.parquet")})
    out["cut"] = pd.read_parquet(root / "out" / "periods.parquet")[["w:" + name for name in names]].iloc[-1].tolist()
    made = invoke("allocation_values", labels=[cut], after=cut - 86400000, every="month")
    out["next"] = {asset: value for _, asset, field, value in made["rows"] if field == "weight"}
elif case == "custom":
    import talyx_quant as q
    from native import allocation_run
    from skfolio.optimization import MeanRisk
    from skfolio.pre_selection import SelectKExtremes
    from sklearn.pipeline import Pipeline
    q._Run.mode = "values"; q._Run.cutoff = None; q._Run.publications = []; q._Run.invocation = None
    allocation_run.ACTIVE.clear(); allocation_run.ACTIVE.update(returns=r, annualization=252, riskFree=0.0, benchmark=None)
    est = Pipeline([("pre", SelectKExtremes(k=3)), ("opt", MeanRisk())])
    result = allocation_run.allocate(R.settings(), estimator=est)
    zeros = [int((p["weights"].abs() < 1e-9).sum()) for p in result["periods"]]
    out["unselected_zero"] = all(z >= 3 for z in zeros)
    out["captured"] = any(item["name"] == "weights" and item["kind"] == "allocation" for item in q._Run.publications)
    try:
        allocation_run.allocate(R.settings(), estimator=object())
    except ValueError as error:
        out["checked"] = str(error)
elif case == "values":
    import pathlib

    from native import allocation_run
    root = pathlib.Path(sys.argv[2])
    rr = R.make(days=900, seed=11)
    close = (1 + rr).cumprod() * 100
    labels = close.index.tz_localize("UTC").asi8 // 1_000_000
    pd.DataFrame({"label": np.repeat(labels, 6), "column": np.tile(close.columns, len(close)), "close": close.to_numpy().ravel()}).to_parquet(root / "bars.parquet", index=False)
    s = R.settings(objective="min_risk", cost_aware=True, max_turnover="30", lookback=126, costs=[{"group": g, "bps": 25} for g in ("Bonds", "Stocks", "Other")])
    run = W.backtest(rr.iloc[1:], s)
    periods = run["periods"]
    k = len(periods) - 1
    stamp = lambda ts: int(pd.Timestamp(ts).tz_localize("UTC").value // 1_000_000)  # noqa: E731
    job = {"data": {"barsPath": str(root / "bars.parquet"), "info": {"annualization": 252}}, "riskFree": {"basis": "zero"}, "settings": s, "labels": [stamp(periods[k]["heldFrom"])], "after": stamp(periods[k - 1]["heldFrom"]),
           "last": {"label": stamp(periods[k - 1]["heldFrom"]), "weights": periods[k - 1]["weights"].to_dict()}, "every": "month"}
    job["last"]["riskShares"] = periods[k - 1]["riskShare"].to_dict()
    # the bars of the frozen data are those the day before the new period starts and the day itself (the loop freezes until the bar)
    made = allocation_run.values(job)
    got = {column: value for _, column, field, value in made["rows"] if field == "weight"}
    out["diff"] = max(abs(got[c] - periods[k]["weights"][c]) for c in got)
    out["rows"] = len(made["rows"])
    shares = {column: value for _, column, field, value in made["rows"] if field == "riskShare"}
    out["share_diff"] = max(abs(shares[c] - periods[k]["riskShare"][c]) for c in shares)
    out["turnover_limited"] = float(0.5 * np.abs(periods[k]["weights"] - periods[k]["previous"]).sum()) if periods[k]["previous"] is not None else None
elif case == "values_weekly":
    import pathlib

    from native import allocation_run
    root = pathlib.Path(sys.argv[2])
    rr = R.make(days=900, seed=11)
    close = (1 + rr).cumprod() * 100
    labels = close.index.tz_localize("UTC").asi8 // 1_000_000
    bars = pd.DataFrame({"label": np.repeat(labels, 6), "column": np.tile(close.columns, len(close)), "close": close.to_numpy().ravel()})
    bars.to_parquet(root / "bars.parquet", index=False)
    s = R.settings(objective="min_risk", lookback=252, align="weekly")
    weekly, info = allocation_run.daily_returns(bars, list(close.columns), s)
    run = W.backtest(weekly, {**s, "lookback": allocation_run.aligned_lookback(s), "annualization": 52})
    periods = run["periods"]
    k = len(periods) - 1
    stamp = lambda ts: int(pd.Timestamp(ts).tz_localize("UTC").value // 1_000_000)  # noqa: E731
    job = {"data": {"barsPath": str(root / "bars.parquet"), "info": {"annualization": 252}}, "riskFree": {"basis": "zero"}, "settings": s, "labels": [stamp(periods[k]["heldFrom"])], "after": stamp(periods[k - 1]["heldFrom"]),
           "last": {"label": stamp(periods[k - 1]["heldFrom"]), "weights": periods[k - 1]["weights"].to_dict(), "riskShares": periods[k - 1]["riskShare"].to_dict()}, "every": "month"}
    made = allocation_run.values(job)
    got = {column: value for _, column, field, value in made["rows"] if field == "weight"}
    out["diff"] = max(abs(got[c] - periods[k]["weights"][c]) for c in got)
    out["annual"] = info["annualization"]
if case == "asset_stops":
    rr = R.make(stop_at=900)
    run = W.backtest(rr, R.settings(lookback=252))
    last = run["periods"][-1]
    gone = [p for p in run["periods"] if p.get("left")]
    out.update(first_left=str(gone[0]["date"].date()) if gone else None, texts=gone[0]["left"] if gone else [], weight=float(last["weights"]["A5"]), total=float(last["weights"].sum()),
               before=float(run["periods"][-30]["weights"]["A5"]) if len(run["periods"]) > 30 else None, finite=bool(np.isfinite(run["daily"]).all()))
print(json.dumps(out))
