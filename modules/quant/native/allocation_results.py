"""An allocation run's Results: the figures and tables of each tab, computed once for the whole run from its daily returns and its periods.

Every figure carries `meta.key` and each trace `meta.role`/`meta.key`; the hover reads are written here in the draft's style (a quantity with
a sign shows + or -, one without shows none, a unit once). A figure that cannot be made is `{"why": reason}` in its place.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from native import metrics as M
from native.allocation_shock import shock_row
from native.figures import Missing, figure, iso, line, nums, reason_of, trace

from . import i18n

CRISES = [("2008 GFC", "2008-09-15", "2009-03-09"), ("2020-03 COVID", "2020-02-19", "2020-03-23"), ("2022 rate hikes", "2022-01-03", "2022-10-12"),
          ("2023 regional banks", "2023-03-08", "2023-03-17"), ("Aug 2024 yen carry unwind", "2024-07-16", "2024-08-05"), ("2025 tariff shock", "2025-02-19", "2025-04-08")]
DAY = "%{x}"


def growth_of(daily):
    return (1 + daily).cumprod()


def drawdown_of(daily):
    growth = growth_of(daily)
    return pd.Series(M.drawdown_path(growth, 1.0), index=growth.index)


def _x(index):
    return iso(index)


def growth_figure(lines, key="allocation.results.growth"):
    traces = []
    for name, series, role in lines:
        traces.append(line(_x(series.index), nums(growth_of(series).to_numpy(), 4), role, name, name, hovertemplate=name + " %{y:.3f}<extra></extra>"))
    return figure(key, traces)


def drawdown_figure(lines):
    # The allocation's own fall is the drawdown (filled, in the down colour); the baselines' are their lines.
    traces = [line(_x(series.index), nums(drawdown_of(series).to_numpy() * 100, 2), "drawdown" if role == "main" else role, name, name, hovertemplate=name + " %{y:+.1f}%<extra></extra>")
              for name, series, role in lines]
    return figure("allocation.results.drawdown", traces)


def periods_frame(periods):
    """One row a period: date, weights by asset (columns), turnover, cost."""
    weights = pd.DataFrame([p["weights"].to_numpy() for p in periods], index=[p["heldFrom"] for p in periods], columns=list(periods[0]["weights"].index))
    shares = pd.DataFrame([p["riskShare"].to_numpy() for p in periods], index=weights.index, columns=weights.columns)
    return weights, shares


def part_marks(columns, groups=None, order=None):
    """Colour of each stacked part, the draft's: a group is `GCOL[group index]`; an asset takes its group's colour, shaded from dark to light across the
    members of the group (a lone member is the plain colour). `groups` maps asset -> group; with only `order`, the columns are the groups."""
    columns = list(columns)
    marks = []
    for position, name in enumerate(columns):
        if groups is None:
            marks.append((order.index(name) if order and name in order else position, 0.0))
            continue
        group = groups.get(name, "Other")
        members = [other for other in columns if groups.get(other, "Other") == group]
        shade = 0.0 if len(members) == 1 else -0.5 + 0.8 * members.index(name) / (len(members) - 1)
        marks.append((order.index(group) if order and group in order else position, round(shade, 4)))
    return marks


def stacked(key, frame, label, template="%{y:.1f}%", scale=100.0, groups=None, order=None):
    """Every period's parts as steps stacked to the whole: `part` traces (colour by group), held until the next period. The time axis is the run's periods only."""
    traces = []
    marks = part_marks(frame.columns, groups, order)
    for name, (index, shade) in zip(frame.columns, marks):
        values = frame[name].to_numpy() * scale
        if np.nanmax(np.abs(values)) * 1 <= 0.0005 * scale:
            continue
        traces.append(trace("scatter", "part", name, mode="lines", stackgroup="one", x=_x(frame.index), y=nums(values, 2), name=name, index=index,
                            line={"width": 0, "shape": "hv"}, hovertemplate=name + " " + template + "<extra></extra>"))
        if shade:
            traces[-1]["meta"]["shade"] = shade
    return figure(key, traces, yaxis={"ticksuffix": "%", "range": [0, 100]} if scale == 100.0 and (frame.min().min() >= 0) else {"ticksuffix": "%"})


def period_table(periods):
    """The Periods table of the Weights tab, newest first: turnover and cost of each period, the limits that bound, how the solver ended."""
    out = []
    for p in reversed(periods):
        out.append({"date": p["heldFrom"].strftime("%Y-%m-%d"), "traded": bool(p["turnover"] > 1e-9), "turnover": float(p["turnover"]), "cost": float(p["cost"] * 1e4),
                    "limits": ", ".join([f"{asset} {kind}" for asset, kind in p["bound"].items()] + list(p.get("left", []))), "status": p["status"], "note": p["note"], "fix": p.get("fix")})
    return out


def turnover_figure(periods):
    """Bars only for the periods that traded (the first period's buy-in from cash is not a rebalance and would dwarf the rest), in %."""
    traded = [p for p in periods[1:] if p["turnover"] > 1e-9]
    x = _x(pd.DatetimeIndex([p["heldFrom"] for p in traded]))
    return figure("allocation.results.turnover", [trace("bar", "part", "turnover", x=x, y=nums([p["turnover"] * 100 for p in traded], 2), name="Turnover", index=0,
                                                         hovertemplate="Turnover %{y:.1f}%<br>Cost %{customdata:.2f} bps<extra></extra>",
                                                         customdata=nums([p["cost"] * 1e4 for p in traded], 2))], yaxis={"ticksuffix": "%"})


def correlation_figure(returns):
    corr = returns.corr()
    names = list(corr.columns)
    # Painted by sign (positive one colour, negative the other, zero the panel) with each value written in its cell: a one-colour ramp read as one block.
    return figure("allocation.results.corr", [trace("heatmap", "updown", "corr", x=names, y=names, z=[nums(row, 3) for row in corr.to_numpy()], zmin=-1, zmax=1,
                                                    texttemplate="%{z:.2f}", hovertemplate="%{y} · %{x}<br>Correlation %{z:+.2f}<extra></extra>")])


def weight_risk_figure(period):
    names = list(period["weights"].index)
    return figure("allocation.results.weight_risk", [
        trace("bar", "main", "weight", x=names, y=nums(period["weights"].to_numpy() * 100, 2), name="Weight", hovertemplate="Weight %{y:.2f}%<extra></extra>"),
        trace("bar", "benchmark", "risk", x=names, y=nums(period["riskShare"].to_numpy() * 100, 2), name="Risk share", hovertemplate="Risk share %{y:.2f}%<extra></extra>")],
        barmode="group")


def annual(daily_lines):
    out = {}
    for name, series, _role in daily_lines:
        out[name] = ((1 + series).groupby(series.index.year).prod() - 1) * 100
    years = sorted(set().union(*[set(v.index) for v in out.values()]))
    traces = []
    for name, _series, role in daily_lines:
        traces.append(trace("bar", role, name, x=[str(y) for y in years], y=nums([out[name].get(y, np.nan) for y in years], 1), name=name,
                            hovertemplate=name + " %{y:+.1f}%<extra></extra>"))
    return figure("allocation.results.annual", traces, barmode="group")


def year_rows(daily_lines):
    """The summary's By year table: each year's compounded return of every line (a fraction), and the allocation's difference from equal weight."""
    out = {name: ((1 + series).groupby(series.index.year).prod() - 1) for name, series, role in daily_lines}
    first = daily_lines[0][0]
    rows = []
    for year in sorted(out[first].index):
        row = {"year": int(year), **{name: float(values.get(year, np.nan)) for name, values in out.items()}}
        if "Equal weight" in out:
            row["vsEqual"] = row[first] - row["Equal weight"]
        rows.append(row)
    return rows


def monthly(daily):
    m = ((1 + daily).groupby([daily.index.year, daily.index.month]).prod() - 1) * 100
    table = m.unstack()
    return figure("allocation.results.monthly", [trace("heatmap", "updown", "monthly", x=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
                                                       y=[str(y) for y in table.index], z=[nums(row, 2) for row in table.reindex(columns=range(1, 13)).to_numpy()],
                                                       texttemplate="%{z:+.1f}", hovertemplate="%{x} %{y}<br>Return %{z:+.2f}%<extra></extra>")])


def rolling(daily_lines, annual, risk_free=0.0):
    """The Sharpe of each line over the year before each day: a year of the pool's observations (`annual`), annualized by the same A, over the run's risk-free rate
    (an observation's: a number, or a Series on the days; None leaves the chart empty, as the run's Sharpe is)."""
    if risk_free is None:
        raise Missing("Market has no 13-week T-bill for this period, so no Sharpe over it")
    traces = []
    for name, series, role in daily_lines:
        rate = risk_free.reindex(series.index) if isinstance(risk_free, pd.Series) else risk_free
        values = [M.sharpe(series.iloc[end - annual:end].to_numpy(float), annual, rate.iloc[end - annual:end].to_numpy(float) if isinstance(rate, pd.Series) else rate)
                  for end in range(annual, len(series) + 1)]
        days = series.index[annual - 1:]
        traces.append(line(_x(days), nums(np.array([np.nan if v is None else v for v in values], float), 2), role, name, name, hovertemplate=name + " Sharpe %{y:.2f}<extra></extra>"))
    return figure("allocation.results.rolling", traces)


def distribution(daily_lines):
    traces = []
    for name, series, role in daily_lines:
        counts, edges = np.histogram(series.to_numpy() * 100, bins=40)
        centers = (edges[:-1] + edges[1:]) / 2
        traces.append(trace("bar", role, name, x=nums(centers, 2), y=nums(counts / len(series) * 100, 2), name=name,
                            hovertemplate="Day's return %{x:+.2f}%<br>" + name + " %{y:.1f}% of days<extra></extra>"))
    return figure("allocation.results.distribution", traces, barmode="overlay")


def contribution(held, returns):
    """Each asset's contribution: what was held through each day (the drifted weights the backtest earned on) x the asset's return that day, summed over the run, in
    percentage points; the parts add up to the sum of the run's daily returns before costs."""
    total = (held * returns.reindex(held.index)).sum() * 100
    return figure("allocation.results.contribution", [trace("bar", "updown", "contribution", x=list(total.index), y=nums(total.to_numpy(), 2), name="Contribution",
                                                             hovertemplate="Contribution %{y:+.2f} pt<extra></extra>")])


def crisis_table(lines):
    rows = []
    for label, start, end in CRISES:
        row = {"window": label, "from": str(start)[:10]}   # a click opens the period held at the window's start
        covered = True
        for name, series, _role in lines:
            part = series.loc[start:end]
            if len(part) < 5 or pd.Timestamp(start) < series.index[0]:
                covered = False
                break
            row[name] = float((1 + part).prod() - 1)
        if covered:
            rows.append(row)
    return rows


def vol_calibration(periods, returns, lookback, annual, settings):
    """Predicted volatility at each period (the fit window's covariance as the settings estimate it, with the period's weights) against what the next period's
    days realized, a year's scale: a better estimate predicts closer."""
    from native import allocation_work as W
    dates, predicted, realized = [], [], []
    for k, p in enumerate(periods[:-1]):
        window = returns.loc[:p["date"]].iloc[-lookback:]
        weights = p["weights"].reindex(returns.columns).fillna(0.0).to_numpy()
        ahead = returns.loc[p["heldFrom"]:periods[k + 1]["heldFrom"]].iloc[:-1] if k + 1 < len(periods) else returns.loc[p["heldFrom"]:]
        if len(window) < 20 or len(ahead) < 5:
            continue
        predicted.append(float(np.sqrt(weights @ W.fitted_covariance(window, settings) @ weights) * np.sqrt(annual) * 100))
        realized.append(float((ahead.to_numpy() @ weights).std(ddof=1) * np.sqrt(annual) * 100))
        dates.append(p["heldFrom"])
    if not dates:
        raise Missing("Too few periods to compare predicted and realized volatility")
    x = _x(pd.DatetimeIndex(dates))
    return figure("allocation.results.vol_calibration", [
        line(x, nums(predicted, 2), "main", "predicted", "Predicted", hovertemplate="Predicted %{y:.2f}%<extra></extra>"),
        line(x, nums(realized, 2), "group", "realized", "Realized", index=1, of=2, hovertemplate="Realized %{y:.2f}%<extra></extra>")], yaxis={"ticksuffix": "%"})


def frontier_figure(weights, returns, settings, lookback, annual=252, size=20):
    """The efficient frontier of the newest window under the run's limits (variance, the settings' covariance and expected returns), with the run's newest
    portfolio and equal weight on it; only the limits that tie a portfolio to a holding or a benchmark (turnover, tracking) and the risk caps are let go, as a
    frontier spans every level of risk. A point that did not solve is left out and said under the chart."""
    from native import allocation_work as W
    window = returns.iloc[-lookback:]
    assets = list(window.columns)
    estimator = W.estimator({**settings, "objective": "min_risk", "risk_measure": "variance", "robust_returns": False, "robust_covariance": False,
                             "objective_parameters": "", "engine_parameters": ""}, assets)
    keep = {key: None for key in ("max_turnover", "max_tracking_error", "max_cvar", "max_variance", "max_standard_deviation", "max_cdar", "max_semi_deviation", "max_max_drawdown")
            if key in estimator.get_params()}
    estimator.set_params(efficient_frontier_size=size, **keep)
    estimator.fit(window)
    grid = np.atleast_2d(estimator.weights_)
    distribution = estimator.prior_estimator_.return_distribution_
    cov, mean = np.asarray(distribution.covariance, float), np.asarray(distribution.mu, float)
    solved = [row for row in grid if np.all(np.isfinite(row))]
    if not solved:
        raise Missing("No point of the frontier solved under these limits")
    vol = [float(np.sqrt(row @ cov @ row) * np.sqrt(annual) * 100) for row in solved]
    ret = [float(row @ mean * annual * 100) for row in solved]
    mine = weights.reindex(assets).fillna(0.0).to_numpy()
    equal = np.full(len(assets), 1.0 / len(assets))
    point = lambda row: (float(np.sqrt(row @ cov @ row) * np.sqrt(annual) * 100), float(row @ mean * annual * 100))  # noqa: E731
    note = [] if len(solved) == len(grid) else [{"text": i18n.t("{unsolved} of {total} points did not solve", unsolved=len(grid) - len(solved), total=len(grid)), "xref": "paper", "yref": "paper", "x": 0, "y": -0.2,
                                                  "xanchor": "left", "showarrow": False, "font": {"size": 10}}]
    hover = "%{customdata}<br>Predicted volatility %{x:.2f}%<br>Expected return %{y:+.2f}%<extra></extra>"
    mx, my = point(mine)
    ex, ey = point(equal)
    return figure("allocation.results.frontier", [
        line(nums(vol, 2), nums(ret, 2), "main", "frontier", "Efficient frontier", customdata=["Efficient frontier"] * len(vol), hovertemplate=hover),
        # Equal weight is a grey reference point (orange is only for what needs attention), drawn first so the allocation's point stays on top where they meet.
        trace("scatter", "compare", "equal", mode="markers", x=[round(ex, 2)], y=[round(ey, 2)], name="Equal weight", customdata=["Equal weight"], hovertemplate=hover, marker={"size": 9}),
        trace("scatter", "part", "run", mode="markers", x=[round(mx, 2)], y=[round(my, 2)], name="Allocation", index=0, customdata=["Allocation"], hovertemplate=hover, marker={"size": 9})],
        xaxis={"ticksuffix": "%"}, yaxis={"ticksuffix": "%"}, annotations=note, margin={"l": 8, "r": 52, "t": 12, "b": 40})


def scenarios(weights, returns, shocks, cov):
    """Historical replay of today's weights through each window, and conditional-mean shocks (an asset or a group falls X%, the others follow by `cov`, the newest
    period's estimate of risk)."""
    out = []
    held = weights
    for label, start, end in CRISES:
        part = returns.loc[start:end]
        if len(part) < 5 or pd.Timestamp(start) < returns.index[0]:
            continue
        asset = (1 + part).prod() - 1
        out.append({"scenario": label, "kind": "Replay", "portfolio": float((held * asset).sum()), "worst": asset.idxmin()})
    names = list(returns.columns)
    groups = shocks.get("groups", {})
    for shock in shocks.get("rows", []):
        row = shock_row(names, held, cov, groups, shock["target"], shock["pct"])
        if row:
            out.append(row)
    return out


def guarded(builder, *args):
    try:
        return builder(*args)
    except Exception as error:  # noqa: BLE001
        return {"why": reason_of(error)}


def brinson(held, returns, groups, annual=252):
    """By group against the 60/40 of SPY and IEF (the benchmark of the run; the caller asks only when both are in the pool): SPY's group is 60% of the benchmark
    and earns SPY's return, IEF's group 40% and IEF's, the rest 0. Allocation effect (the weight difference times the group's return over the benchmark total),
    selection effect (the benchmark weight times what the portfolio's picks added within the group), total; annualized from daily means. `held` is what the
    backtest held through each day."""
    held = held[held.sum(axis=1) > 0]
    r = returns.loc[held.index]
    names = list(dict.fromkeys(groups.get(a, "Other") for a in returns.columns))
    anchors = {groups.get("SPY", "Other"): "SPY", groups.get("IEF", "Other"): "IEF"} if groups.get("SPY", "Other") != groups.get("IEF", "Other") else {}
    bench_w = {g: {"SPY": 0.6, "IEF": 0.4}.get(anchors.get(g), 0.0) for g in names}
    if not anchors:  # SPY and IEF in one group: the group is the whole benchmark
        bench_w[groups.get("SPY", "Other")] = 1.0
    port_w, port_r, bench_r = {}, {}, {}
    for g in names:
        members = [a for a in returns.columns if groups.get(a, "Other") == g]
        w = held[members].sum(axis=1)
        port_w[g] = float(w.mean())
        contribution = (held[members] * r[members]).sum(axis=1)
        port_r[g] = float((contribution / w.where(w > 1e-9)).dropna().mean() * annual) if (w > 1e-9).any() else 0.0
        anchor = anchors.get(g)
        if anchor:
            bench_r[g] = float(r[anchor].mean() * annual)
        elif not anchors and g == groups.get("SPY", "Other"):
            bench_r[g] = float((0.6 * r["SPY"] + 0.4 * r["IEF"]).mean() * annual)
        else:
            bench_r[g] = float(r[members].mean(axis=1).mean() * annual)
    total_b = sum(bench_w[g] * bench_r[g] for g in names)
    rows = []
    for g in names:
        allocation = (port_w[g] - bench_w[g]) * (bench_r[g] - total_b)
        selection = bench_w[g] * (port_r[g] - bench_r[g])
        rows.append({"group": g, "allocation": allocation, "selection": selection, "total": allocation + selection})
    return rows


class PoolPanel:
    """The pool's stocks as the small panel `styles.price_styles` reads: bars by names, every name a member. The size is the 30-bar mean dollar volume (there are no shares
    outstanding here), so Liquidity and Size need the volume."""

    def __init__(self, returns, amount):
        from native import styles
        self.names = list(returns.columns)
        self.returns = returns.to_numpy(float)
        self.member = np.ones(self.returns.shape, bool)
        self.fields = {"close": (1 + returns).cumprod().to_numpy(float)}
        self._derived = {"returns": self.returns}
        if amount is not None:
            self._derived["amount"] = amount.to_numpy(float)
            self._derived["size"] = styles.rolling_mean(self._derived["amount"], 30, 15)
        else:
            self._derived["amount"] = np.full(self.returns.shape, np.nan)
            self._derived["size"] = np.full(self.returns.shape, np.nan)

    def derived(self, name):
        return self._derived[name]

    def cached(self, key, compute):
        return compute()


def style_exposures(returns, held, groups=None, amount=None, annual=252, daily=None):
    """The portfolio's exposure to the price styles `styles.py` defines (its `VERSION` is written beside the table), over the pool's stocks: each style standardized across
    the stocks each day, the exposure the weights times it, averaged over the run and now; and what each contributes a year (the style's daily return, the slope of the
    stocks' returns on it, times the exposure held). `Specific` is the rest of the run's return a year. Only the styles that can be computed are listed."""
    from native import styles
    stocks = [a for a in returns.columns if (groups or {}).get(a, "Other") == "Stocks"]
    if len(stocks) < 3:
        return []
    r = returns[stocks]
    values = styles.price_styles(PoolPanel(r, amount[stocks].reindex(r.index) if amount is not None else None))
    held = held.reindex(index=returns.index, columns=list(returns.columns)).fillna(0.0)[stocks]   # what the backtest held through each day
    rows, total = [], 0.0
    for name, raw in values.items():
        z = styles._zscore_rows(np.where(np.isfinite(raw), raw, np.nan))
        if np.isfinite(z).sum() < 20:
            continue
        now_z = np.nan_to_num(z)
        before = np.vstack([np.zeros((1, z.shape[1])), now_z[:-1]])  # a day's return is explained by what was known the day before
        with np.errstate(invalid="ignore", divide="ignore"):
            slope = (before * r.to_numpy()).sum(axis=1) / (before ** 2).sum(axis=1)
        slope = np.where(np.isfinite(slope), slope, 0.0)
        exposure = (held.to_numpy() * before).sum(axis=1)
        live = held.sum(axis=1).to_numpy() > 1e-9
        series = pd.Series(exposure[live])
        if series.empty:
            continue
        contribution = float((exposure * slope)[live].mean() * annual)
        total += contribution
        rows.append({"style": name, "average": float(series.mean()), "now": float(exposure[live][-1]), "contribution": contribution})
    if rows and daily is not None:
        rows.append({"style": "Specific", "average": None, "now": None, "contribution": float(daily.mean() * annual - total)})
    return rows


PERIOD_OF = {12: i18n.mark("Monthly"), 52: i18n.mark("Weekly")}   # observations a year of an aligned pool; any other count is Market's calendar of days


def risk_measures(daily, benchmark=None, annual=None, vol_figure=None):
    """Whole-run risk numbers of the run's returns: historical value at risk and CVaR at 95% of one observation (`native/metrics.py`; a day, or a week or a month
    when the pool is aligned so, and the label says which), CDaR at 95% (drawdown), the tracking error against 60/40, the average predicted and realized volatility
    (read off the calibration chart). `plain` rows are unsigned figures."""
    r = daily.to_numpy(float)
    var, cvar = M.value_at_risk(r, 0.95)
    growth = (1 + daily).cumprod()
    depth = M.drawdown_path(growth, 1.0)
    deep = np.quantile(depth, 0.05)
    period = i18n.t(PERIOD_OF.get(annual, i18n.mark("Daily")))
    rows = [{"label": i18n.t("{period} VaR 95%", period=period), "value": var}, {"label": i18n.t("{period} CVaR 95%", period=period), "value": cvar},
            {"label": i18n.t("CDaR 95%"), "value": float(depth[depth <= deep].mean())}]
    if benchmark is not None and len(daily) > 2:
        rows.append({"label": i18n.t("Tracking error vs 60/40"), "value": float((daily - benchmark).std() * np.sqrt(annual)), "plain": True})
    traces = {t.get("meta", {}).get("key"): t for t in (vol_figure or {}).get("data", [])} if isinstance(vol_figure, dict) else {}
    for key, label in (("predicted", i18n.mark("Predicted volatility, average")), ("realized", i18n.mark("Realized next month, average"))):
        values = [v for v in (traces.get(key) or {}).get("y", []) if v is not None]
        if values:
            rows.append({"label": i18n.t(label), "value": float(np.mean(values)) / 100.0, "plain": True})
    return rows


def cost_table(rows):
    """[(label, cagr, sharpe, max drawdown)] for the run at costs 0 ×, as set and 2 ×."""
    return [{"label": label, "cagr": m["cagr"], "sharpe": m["sharpe"], "maxDrawdown": m["maxDrawdown"]} for label, m in rows]
