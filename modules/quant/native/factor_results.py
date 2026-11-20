"""A factor run's Results: the full report, built in the background after Work's numbers, in its own process.

The numbers come from Alphalens wherever Alphalens has them (IC table, group statistics, returns table with alpha and beta, mean return
by group, factor-weighted long–short, top-minus-bottom spread, IC by sector and by size, turnover and rank autocorrelation); TALYX
computes what Alphalens does not (by year, costs, styles, before and after taking out industry and size, the overlap-corrected t) with
the same `metrics` as Work. Every figure is drawn here as Plotly with each trace's role (`meta.role`) and the figure's fixed identity
(`layout.meta.key`); the display colours it. A figure or table that cannot be made says why in its place.

Alphalens is given the run's own values, groups and forward returns (held `p` bars from `delay` bars after the value, as Work), with
forward-return columns named `<p>D` meaning p bars, and `annualization` the bars in a year: its arithmetic then holds for any bar size.

Files, in the run's `output/results/`: `<tab>.json` for each tab as it is done, `progress.jsonl` (one line a step) and `answer.json`.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

native_directory = Path(__file__).resolve().parent
sys.path[:] = [path for path in sys.path if Path(path or os.getcwd()).resolve() != native_directory]
sys.path.insert(0, str(native_directory.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from native import factor_work as W  # noqa: E402
from native import metrics as M  # noqa: E402
from native import styles as S  # noqa: E402

TABS = ("returns", "ic", "industry", "style", "turnover", "costs", "event")
# The order they are built in: the slowest (IC inside each sector and size, about half the time) last, so the rest show first.
BUILD_ORDER = ("returns", "ic", "turnover", "costs", "style", "event", "industry")
HOLDINGS = (1, 2, 3, 5, 10, 20)
EVENT_BEFORE, EVENT_AFTER = 5, 20
ROLLING = 22  # Alphalens' "1 month moving average"
BPS = 1e4


from native.figures import (  # noqa: E402,F401
    Missing,
    figure,
    iso,
    line,
    num,
    nums,
    reason_of,
    reference,
    trace,
)


# ---------------------------------------------------------------------------------------------------------------- the builder
def spread_of(top, bottom):
    """The Returns table's spread: the top group's mean return less the bottom group's (design.md, one algorithm under Raw and Demeaned)."""
    return float(top) - float(bottom)


class Results:
    def __init__(self, job, panel, raw):
        self.job = job
        self.settings = W.settings_of(job["settings"])
        held = job.get("heldOutFrom")
        self.panel = panel if held is None else panel.truncate(held)
        self.formula = bool(job.get("formula"))
        self.demeaned, self.group_adjust = bool(self.settings["demeaned"]), bool(self.settings["groupAdjust"])  # Alphalens' two options, from the run's settings
        self.ev = W.Evaluation(self.panel, raw, self.settings, formula=self.formula, with_similar=False)
        self.raw = self.ev.raw
        self.h, self.delay = self.settings["holding"], self.settings["delay"]
        self.A = self.panel.annualization
        frame = str(self.panel.info.get("timeframe") or "1d")
        self.unit = "day" if frame.endswith("d") and frame[:-1] in ("", "1") else "hour" if frame in ("1h", "h") else "bar"
        limit = max(1, len(self.panel.labels) // 3)
        self.periods = [p for p in sorted({1, 5, 10, self.h}) if p < limit] or [1]
        self.similar = job.get("similar") or []
        self._factor_data = None
        self._ic = None

    def label(self, period):
        return f"{period}D" if self.unit == "day" else f"{period}h" if self.unit == "hour" else f"{period} bars"

    def column(self, period):
        return f"{period}D"  # Alphalens reads a period from the column name; here a "day" is one bar

    # ------------------------------------------------------------------------------------------------ Alphalens' input
    def factor_data(self):
        """Alphalens' factor table: one row per day and name with a value, a group and every forward return, as get_clean_factor gives."""
        if self._factor_data is None:
            ev, panel = self.ev, self.panel
            close = panel.fields["close"]
            forwards = {p: ev.forward if p == self.h else M.forward_returns(close, p, self.delay) for p in self.periods}
            keep = np.isfinite(ev.after) & (ev.groups > 0)
            for value in forwards.values():
                keep &= np.isfinite(value)
            t, i = np.nonzero(keep)
            if not len(t):
                raise Missing("No day has values and later returns for these names")
            index = pd.MultiIndex.from_arrays([panel.dates[t], np.asarray(panel.columns, dtype=object)[i]], names=["date", "asset"])
            data = {self.column(p): forwards[p][t, i] for p in self.periods}
            data["factor"] = ev.after[t, i]
            data["factor_quantile"] = ev.groups[t, i].astype(int)
            frame = pd.DataFrame(data, index=index)
            if panel.industry is not None:
                labels = np.asarray([item if item else "Unknown" for item in panel.industry], dtype=object)
                frame["group"] = labels[i]
            self._factor_data = (frame, t, i)
        return self._factor_data[0]

    def ic(self):
        """The daily rank IC for each period, by date: Work's IC (the same function, the same names) — each day the names with a value and
        that period's later return, at least 3, ranked and correlated; with Group neutral against returns less their group's mean. The
        holding's column is Work's own series, so every page of a run reads one sample."""
        if self._ic is None:
            ev, panel = self.ev, self.panel
            codes = None
            if self.group_adjust:
                if panel.industry is None:
                    raise Missing("Group neutral needs an industry for each name, and Market gives none for these names")
                codes = pd.factorize(np.asarray([item or "Unknown" for item in panel.industry], dtype=object))[0]
            close = panel.fields["close"]
            series = {self.column(p): ev.ic if p == self.h else W.rank_ic(ev.after, close, p, self.delay, codes=codes)[0] for p in self.periods}
            frame = pd.DataFrame(series, index=pd.Index(panel.dates, name="date"))
            self._ic = frame.dropna(how="all")
        return self._ic

    @staticmethod
    def group_ic_daily(frame, column):
        """Each day and group's Spearman of the value against the forward return, indexed by (date, group); averaged over the days
        (`group_ic`) it is Alphalens' mean_information_coefficient(by_group=True) for one period. Alphalens calls scipy once per day and group (about 80 seconds for ten years of
        the S&P 500 by sector); this is the same arithmetic in whole columns: ranks within the day and group (ties averaged, as
        spearmanr), then their Pearson correlation; a group of one name or with all values equal has none that day, as in scipy."""
        keys = [frame.index.get_level_values("date"), frame["group"].to_numpy()]
        ranked = pd.DataFrame({"x": frame["factor"].groupby(keys).rank(method="average").to_numpy(),
                               "y": frame[column].groupby(keys).rank(method="average").to_numpy()})
        ranked["xy"], ranked["xx"], ranked["yy"] = ranked["x"] * ranked["y"], ranked["x"] ** 2, ranked["y"] ** 2
        ranked["date"], ranked["group"] = keys
        sums = ranked.groupby(["date", "group"], sort=False).agg(n=("x", "size"), x=("x", "sum"), y=("y", "sum"), xy=("xy", "sum"), xx=("xx", "sum"), yy=("yy", "sum"),
                                                                 xlow=("x", "min"), xhigh=("x", "max"), ylow=("y", "min"), yhigh=("y", "max"))
        with np.errstate(invalid="ignore", divide="ignore"):
            cov = sums["xy"] - sums["x"] * sums["y"] / sums["n"]
            spread = (sums["xx"] - sums["x"] ** 2 / sums["n"]) * (sums["yy"] - sums["y"] ** 2 / sums["n"])
            ic = (cov / np.sqrt(spread)).clip(-1, 1)
        varies = (sums["n"] > 1) & (sums["xhigh"] > sums["xlow"]) & (sums["yhigh"] > sums["ylow"])
        return ic.where(varies)

    @classmethod
    def group_ic(cls, frame, column):
        return cls.group_ic_daily(frame, column).groupby(level="group").mean()

    # ------------------------------------------------------------------------------------------------ the tabs
    def build(self, name):
        tab = {"tab": name, "holding": self.h, "unit": self.unit, "periods": self.periods, "periodLabels": [self.label(p) for p in self.periods],
               "figures": {}, "missing": {}}
        getattr(self, "tab_" + name)(tab)
        return tab

    def section(self, tab, name, make, *, title=None, figure_keys=()):
        """Make one table or figure; when it cannot be made, write why in its place (and in every figure it would have drawn)."""
        try:
            return make()
        except Exception as error:  # noqa: BLE001 - the reason is shown where the section would be
            reason = reason_of(error)
            if not isinstance(error, Missing):
                traceback.print_exc()
            tab["missing"][name] = reason
            for key, label in figure_keys:
                tab["figures"].setdefault(key, {"key": f"factor.results.{key}", "title": label, "unavailable": reason})
            return None

    def put(self, tab, key, title, made):
        layout = made["layout"]
        for axis in ("xaxis", "yaxis"):
            if isinstance(layout.get(axis), dict):
                layout[axis].pop("title", None)  # the unit is in the section's subtitle, not along the axis
        if any(item.get("type") == "bar" and item.get("orientation") == "h" for item in made["data"]):
            layout["yaxis"] = {**layout.get("yaxis", {}), "side": "left"}
        tab["figures"][key] = {"key": made["layout"]["meta"]["key"], "title": title, "figure": made}

    # ---------------------------------------------------------------- Returns
    def tab_returns(self, tab):
        ev, panel = self.ev, self.panel

        def groups():
            made = W.groups_figure(panel.dates, ev.returns["relative"], ev.returns["net"], key="factor.results.groups",
                                   step=max(1, len(panel.dates) // 4000))
            made["layout"]["dragmode"] = "zoom"
            self.put(tab, "groups", "Cumulative return by group", made)
        self.section(tab, "groups", groups, figure_keys=[("groups", "Cumulative return by group")])

        def statistics():
            from alphalens import plotting
            table = plotting.plot_quantile_statistics_table(self.factor_data(), return_df=True)
            tab["groupStats"] = [{"group": int(q), "min": num(row["min"]), "max": num(row["max"]), "mean": num(row["mean"]), "std": num(row["std"]),
                                  "count": int(row["count"]), "pct": num(row["count %"], 3)} for q, row in table.iterrows()]
        self.section(tab, "groupStats", statistics)

        def returns():
            from alphalens import performance as perf
            from alphalens import plotting, utils
            frame = self.factor_data()
            if self.group_adjust and "group" not in frame:
                raise Missing("Group neutral needs an industry for each name, and Market gives none for these names")
            demeaned, adjust = self.demeaned, self.group_adjust
            factor_returns = perf.factor_returns(frame, demeaned=demeaned, group_adjust=adjust)
            mean, error = perf.mean_return_by_quantile(frame, by_group=False, demeaned=demeaned, group_adjust=adjust)
            base = mean.columns[0]
            rate = mean.apply(utils.rate_of_return, axis=0, base_period=base)
            rate_error = error.apply(utils.std_conversion, axis=0, base_period=base)
            by_date, std_daily = perf.mean_return_by_quantile(frame, by_date=True, by_group=False, demeaned=demeaned, group_adjust=adjust)
            rate_by_date = by_date.apply(utils.rate_of_return, axis=0, base_period=by_date.columns[0])
            std_rate = std_daily.apply(utils.std_conversion, axis=0, base_period=std_daily.columns[0])
            alpha_beta = perf.factor_alpha_beta(frame, factor_returns, demeaned, adjust, annualization=self.A)
            spread, spread_error = perf.compute_mean_returns_spread(rate_by_date, frame["factor_quantile"].max(), frame["factor_quantile"].min(), std_err=std_rate)
            table = plotting.plot_returns_table(alpha_beta, rate, spread, return_df=True)
            rows = []
            for p in self.periods:
                c = self.column(p)
                top, bottom = table.loc["Mean Period Wise Return Top Quantile (bps)", c], table.loc["Mean Period Wise Return Bottom Quantile (bps)", c]
                # The spread is the top group's mean less the bottom group's, the two numbers beside it, under Raw and Demeaned alike
                # (Alphalens' own averages the daily spreads over days, a different number under the same name).
                rows.append({"period": p, "alpha": num(table.loc["Ann. alpha", c]), "beta": num(table.loc["beta", c]),
                             "top": num(top, 4), "bottom": num(bottom, 4), "spread": num(spread_of(top, bottom), 4)})
            tab["returnsByPeriod"] = rows
            # Mean return by group over the whole period, for every name together (not by sector): the bars' numbers.
            groups = [int(q) for q in rate.index]
            tab["meanByGroup"] = [{"group": q, "means": nums(rate.loc[q, [self.column(p) for p in self.periods]] * BPS, 4),
                                   "errors": nums(rate_error.loc[q, [self.column(p) for p in self.periods]] * BPS, 4)} for q in groups]
            return {"rate": rate, "rateByDate": rate_by_date, "factorReturns": factor_returns, "spread": spread, "spreadError": spread_error, "groups": groups}
        found = self.section(tab, "returnsByPeriod", returns, figure_keys=[("meanByGroup", "Mean return by group"), ("violin", "Return by group, each day"),
                                                                           ("factorWeighted", "Factor-weighted long–short"), ("spread", "Top minus bottom group")])
        if found is None:
            tab["missing"].setdefault("meanByGroup", tab["missing"]["returnsByPeriod"])
            return
        count = len(found["groups"])
        labels = [f"Q{q}" for q in found["groups"]]

        def means():
            traces = [trace("bar", "group", f"period:{p}", x=labels, y=nums(found["rate"][self.column(p)] * BPS, 4), name=self.label(p), index=k, of=len(self.periods),
                            hovertemplate=f"%{{x}} {self.label(p)}<br>%{{y:+.1f}} bps<extra></extra>") for k, p in enumerate(self.periods)]
            self.put(tab, "meanByGroup", "Mean return by group", figure("factor.results.mean-by-group", traces, barmode="group", showlegend=True,
                                                                        yaxis={"title": {"text": "Mean return (bps)"}, "side": "right", "zeroline": True}))
        self.section(tab, "meanByGroup", means, figure_keys=[("meanByGroup", "Mean return by group")])

        def violin():
            frame = found["rateByDate"] * BPS
            low, high = np.nanpercentile(frame.values, 1), np.nanpercentile(frame.values, 99)
            traces = []
            for k, p in enumerate(self.periods):
                column = frame[self.column(p)].dropna()
                quantiles = column.index.get_level_values("factor_quantile")
                traces.append(trace("violin", "group", f"period:{p}", x=[f"Q{q}" for q in quantiles], y=nums(column.to_numpy(), 2), name=self.label(p),
                                    index=k, of=len(self.periods), spanmode="hard", points=False, box={"visible": True}, meanline={"visible": False},
                                    scalegroup=self.label(p), hoverinfo="skip", offsetgroup=self.label(p)))
                # The read-out of a violin: a mark at its median (not drawn) carries the median and the quartiles of that group and holding.
                stats_ = column.groupby(quantiles).quantile([.25, .5, .75]).unstack()
                traces.append(trace("scatter", "group", f"period:{p}:read", mode="markers", x=[f"Q{q}" for q in stats_.index], y=nums(stats_[.5].to_numpy(), 2), name=self.label(p),
                                    index=k, of=len(self.periods), marker={"size": 16, "opacity": 0}, offsetgroup=self.label(p), showlegend=False,
                                    customdata=[[num(a, 1), num(b, 1)] for a, b in zip(stats_[.25], stats_[.75], strict=True)],
                                    hovertemplate=f"%{{x}} {self.label(p)}<br>Median %{{y:+.1f}} bps<br>Quartiles %{{customdata[0]:+.1f}} to %{{customdata[1]:+.1f}} bps<extra></extra>"))
            self.put(tab, "violin", "Return by group, each day", figure("factor.results.violin", traces, violinmode="group", showlegend=True,
                                                                        yaxis={"side": "right", "range": [num(low, 2), num(high, 2)], "zeroline": True}))
        self.section(tab, "violin", violin, figure_keys=[("violin", "Return by group, each day")])

        def weighted():
            from alphalens import performance as perf
            series = found["factorReturns"][self.column(1)] if self.column(1) in found["factorReturns"] else found["factorReturns"].iloc[:, 0]
            path = perf.cumulative_returns(series.fillna(0.0))
            x = iso(path.index)
            self.put(tab, "factorWeighted", "Factor-weighted long–short", figure("factor.results.factor-weighted", [
                reference([x[0], x[-1]], [0.0, 0.0], "zero", "Start"),
                line(x, nums(path.to_numpy() - 1.0), "main", "factor-weighted", "Cumulative return", hovertemplate="Cumulative return %{y:+.1%}<extra></extra>")],
                hovermode="x unified", xaxis={"type": "date"}, yaxis={"side": "right", "tickformat": ".0%", "zeroline": True}))
        self.section(tab, "factorWeighted", weighted, figure_keys=[("factorWeighted", "Factor-weighted long–short")])

        def spread():
            column = self.column(self.h) if self.column(self.h) in found["spread"] else found["spread"].columns[-1]
            values = found["spread"][column] * BPS
            mean = values.rolling(window=ROLLING).mean()
            deviation = values.rolling(window=ROLLING).std()
            x = iso(values.index)
            limit = float(np.nanpercentile(np.abs(values.values), 95)) if values.notna().any() else 1.0
            self.put(tab, "spread", "Top minus bottom group", figure("factor.results.spread", [
                line(x, nums(mean + deviation, 3), "detail", "spread:upper", "1-month mean + 1 std", line={"width": 0}, hoverinfo="skip"),
                line(x, nums(mean - deviation, 3), "detail", "spread:lower", "1-month mean − 1 std", line={"width": 0}, fill="tonexty", hoverinfo="skip"),
                trace("bar", "updown", "spread", x=x, y=nums(values, 3), name="Spread", marker={"opacity": 0.35}, showlegend=False, hoverinfo="skip"),  # each day faint, by sign, under the mean
                line(x, nums(mean, 3), "main", "spread:mean", "1-month mean", hovertemplate="1-month mean %{y:+.1f} bps<extra></extra>")],
                hovermode="x unified", xaxis={"type": "date"}, yaxis={"side": "right", "range": [-limit, limit], "zeroline": True}, bargap=0))
            tab["spreadPeriod"] = int(column[:-1])
        self.section(tab, "spread", spread, figure_keys=[("spread", "Top minus bottom group")])
        tab["groupCount"] = count

    # ---------------------------------------------------------------- IC
    def tab_ic(self, tab):
        def information():
            from scipy import stats
            ic = self.ic()
            rows = []
            for p in self.periods:
                # Alphalens' information table, over the days this period has an IC.
                series = ic[self.column(p)].dropna().to_numpy(dtype=float)
                mean, std = (float(series.mean()), float(series.std(ddof=1))) if len(series) > 1 else (None, None)
                t, p_value = stats.ttest_1samp(series, 0) if len(series) > 1 else (None, None)
                t_overlap = M.newey_west_t(series, max(p - 1, 0))
                rows.append({"period": p, "mean": num(mean), "std": num(std), "riskAdjusted": num(mean / std) if std else None, "t": num(t, 4),
                             "p": num(p_value), "tOverlap": num(t_overlap, 4),
                             "pOverlap": None if t_overlap is None else num(math.erfc(abs(t_overlap) / math.sqrt(2))),
                             "skew": num(stats.skew(series), 4) if len(series) > 2 else None,
                             "kurtosis": num(stats.kurtosis(series), 4) if len(series) > 3 else None})
            tab["information"] = rows
        self.section(tab, "information", information)

        def daily():
            ic = self.ic()[self.column(self.h)]
            x = iso(ic.index)
            mean = ic.rolling(window=ROLLING).mean()
            text = f"Mean {ic.mean():.3f}<br>Std. {ic.std():.3f}"
            self.put(tab, "daily", "Rank IC each day", figure("factor.results.ic-daily", [
                trace("bar", "updown", "ic", x=x, y=nums(ic.to_numpy()), name="Rank IC", hovertemplate="Rank IC %{y:+.3f}<extra></extra>", marker={"line": {"width": 0}}),
                line(x, nums(mean.to_numpy()), "main", "ic:mean", "1-month mean", hovertemplate="1-month mean %{y:+.3f}<extra></extra>")],
                hovermode="x unified", xaxis={"type": "date"}, yaxis={"side": "right", "zeroline": True}, bargap=0,
                annotations=[{"x": 0.01, "y": 0.98, "xref": "paper", "yref": "paper", "xanchor": "left", "yanchor": "top", "showarrow": False, "align": "left",
                              "text": text, "name": "summary"}]))
            tab["daily"] = {"mean": num(ic.mean()), "std": num(ic.std()), "days": int(ic.notna().sum())}
        self.section(tab, "daily", daily, figure_keys=[("daily", "Rank IC each day")])

        def distribution():
            from scipy import stats
            observed = self.ic()[self.column(self.h)].dropna().to_numpy(dtype=float)
            if len(observed) < 10:
                raise Missing("Fewer than 10 days with a rank IC")
            counts, edges = np.histogram(observed, bins="auto")
            centres, width = (edges[:-1] + edges[1:]) / 2, float(edges[1] - edges[0])
            grid = np.linspace(observed.min(), observed.max(), 200)
            density = stats.gaussian_kde(observed)(grid) * len(observed) * width  # seaborn's kde, scaled to the counts
            mean = float(observed.mean())
            # The days the factor ranked the wrong way (below zero) in the down colour, the others in the up colour.
            bins = [(side, [c for c, k in zip(centres, counts, strict=True) if (c < 0) == (side == "down")], [int(k) for c, k in zip(centres, counts, strict=True) if (c < 0) == (side == "down")])
                    for side in ("down", "up")]
            self.put(tab, "histogram", "Distribution", figure("factor.results.ic-histogram", [
                *[trace("bar", "updown", f"count:{side}", x=nums(np.asarray(x)), y=y, width=width, name="Days", side=side, showlegend=False,
                        hovertemplate="IC %{x:+.3f}<br>Days %{y}<extra></extra>") for side, x, y in bins if x],
                line(nums(grid), nums(density, 3), "main", "density", "Density", hoverinfo="skip"),
                reference([mean, mean], [0, int(counts.max())], "mean", "Mean")],
                bargap=0, xaxis={"range": [-1, 1]}, yaxis={"side": "right"},
                annotations=[{"x": 0.01, "y": 0.98, "xref": "paper", "yref": "paper", "xanchor": "left", "yanchor": "top", "showarrow": False, "align": "left",
                              "text": f"Mean {mean:.3f}<br>Std. {observed.std(ddof=1):.3f}", "name": "summary"}]))
            import statsmodels.api as sm
            plot = sm.ProbPlot(observed, stats.norm, fit=True)
            theory, sample = plot.theoretical_quantiles, plot.sample_quantiles
            low, high = float(min(theory.min(), sample.min())), float(max(theory.max(), sample.max()))
            step = max(1, len(theory) // 1500)
            self.put(tab, "qq", "Against a normal distribution", figure("factor.results.ic-qq", [
                trace("scatter", "main", "quantiles", mode="markers", x=nums(theory[::step], 4), y=nums(sample[::step], 4), name="Observed", marker={"size": 3},
                      hovertemplate="Normal %{x:+.3f}<br>Observed %{y:+.3f}<extra></extra>"),
                reference([low, high], [low, high], "45", "45° line")],
                xaxis={"zeroline": False}, yaxis={"side": "right", "zeroline": False}))
        self.section(tab, "distribution", distribution, figure_keys=[("histogram", "Distribution"), ("qq", "Against a normal distribution")])

        def years():
            rows = self.years()
            tab["years"] = rows
            held = self.job.get("heldOutFrom")
            tab["heldOut"] = None if held is None else {"from": int(held)}
            names = [str(item["year"]) for item in rows]
            whole = self.ev.numbers["rankIc"]
            marks = [] if whole is None or not names else [reference([names[0], names[-1]], [num(whole), num(whole)], "whole-period", "Whole period")]
            self.put(tab, "years", "Rank IC by year", figure("factor.results.ic-years", [
                trace("bar", "updown", "rank-ic", x=names, y=[item["rankIc"] for item in rows], name="Rank IC", customdata=[item.get("t") for item in rows],
                      hovertemplate="%{x}<br>Rank IC %{y:+.4f}<br>t %{customdata:+.2f}<extra></extra>"), *marks],
                xaxis={"type": "category"}, yaxis={"side": "right", "tickformat": ".3f", "zeroline": True},
                annotations=[{"x": names[-1], "y": num(whole), "text": "whole period", "name": "whole-period", "showarrow": False, "xanchor": "right", "yanchor": "bottom"}] if marks else []))
        self.section(tab, "years", years, figure_keys=[("years", "Rank IC by year")])

        def monthly():
            ic = self.ic()[[self.column(self.h)]]
            # As Alphalens' mean_information_coefficient(by_time=MonthEnd): the daily IC averaged by calendar month.
            means = ic.reset_index().set_index("date").groupby([pd.Grouper(freq=pd.offsets.MonthEnd())]).mean()[self.column(self.h)]
            means = means[means.index.notna()]
            years_ = sorted({stamp.year for stamp in means.index})
            z = [[None] * 12 for _ in years_]
            for stamp, value in means.items():
                z[years_.index(stamp.year)][stamp.month - 1] = num(value, 4)
            names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            self.put(tab, "monthly", "Monthly mean rank IC", figure("factor.results.ic-monthly", [
                trace("heatmap", "updown", "monthly", x=names, y=[str(year) for year in years_], z=z, texttemplate="%{z:.3f}", showscale=False, xgap=1, ygap=1,
                      hovertemplate="%{x} %{y}<br>Mean rank IC %{z:+.4f}<extra></extra>")],
                xaxis={"side": "top", "type": "category"}, yaxis={"autorange": "reversed", "type": "category"}, margin={"l": 44, "r": 8, "t": 24, "b": 8}))
        self.section(tab, "monthly", monthly, figure_keys=[("monthly", "Monthly mean rank IC")])

    def years(self):
        """Each calendar year of the in-sample period: rank IC, its overlap-corrected t, long–short net, Sharpe and turnover."""
        ev, panel = self.ev, self.panel
        net = ev.returns["net"]
        top, bottom = ev.groups == ev.count, ev.groups == 1
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # days before the first rebalance have no turnover
            turnover = np.nanmean(np.vstack([M.replaced_share(top, self.h), M.replaced_share(bottom, self.h)]), axis=0) if len(panel.dates) > self.h else np.full(len(panel.dates), np.nan)
        out = []
        for item in M.signal_years(panel.dates, ev.ic, net, self.A, self.h):
            inside = panel.dates.year == item["year"]
            out.append({"year": item["year"], "whole": item["whole"], "rankIc": num(item["rankIc"]), "t": num(item["t"], 4), "longShortNet": num(item["longShortNet"]),
                        "sharpe": num(M.sharpe(net[inside], self.A), 4),  # the book pays for itself: no rate off, as the whole run's
                        "turnover": num(M.mean(turnover[inside]), 4), "days": item["days"]})
        return out

    # ---------------------------------------------------------------- Industry and size
    def tab_industry(self, tab):
        ev, panel = self.ev, self.panel
        has_industry = panel.industry is not None and any(panel.industry)

        def sectors():
            if not has_industry:
                raise Missing("Market gives no industry for these names, so there is nothing to split by")
            frame = self.factor_data()
            c = self.column(self.h)
            ic = self.group_ic(frame, c).dropna().sort_values()
            counts = frame.groupby("group").size()
            names = frame.reset_index().groupby("group")["asset"].nunique()
            # a bar alone does not say whether a sector's IC is more than noise: its t (overlap-corrected, as the run's) and its days
            daily = self.group_ic_daily(frame, c)
            daily = {name: series.droplevel("group").sort_index().to_numpy() for name, series in daily.groupby(level="group")}
            tab["sectors"] = [{"sector": str(name), "rankIc": num(value), "t": num(M.newey_west_t(daily[name], max(self.h - 1, 0)), 4),
                               "days": int(np.isfinite(daily[name]).sum()), "names": int(names.get(name, 0)), "observations": int(counts.get(name, 0))} for name, value in ic.items()]
            self.put(tab, "sectors", "Inside each sector", figure("factor.results.ic-by-sector", [
                trace("bar", "updown", "rank-ic", orientation="h", y=[str(name) for name in ic.index], x=nums(ic.to_numpy()), name="Rank IC",
                      customdata=[int(names.get(name, 0)) for name in ic.index],
                      hovertemplate="%{y}<br>Rank IC %{x:+.4f}<br>Names %{customdata}<extra></extra>")],
                xaxis={"zeroline": True, "tickformat": ".3f"}, yaxis={"type": "category", "automargin": True}, margin={"l": 8, "r": 16, "t": 8, "b": 26}))
        self.section(tab, "sectors", sectors, figure_keys=[("sectors", "Inside each sector")])

        def size_and_period():
            frame = self.factor_data()
            c = self.column(self.h)
            rows = []
            t, i = self._factor_data[1], self._factor_data[2]
            size = panel.derived("size")
            with np.errstate(invalid="ignore", divide="ignore"):
                size = np.log(np.where(size > 0, size, np.nan))
            ranks = M.rank_rows(np.where(np.isfinite(ev.after), size, np.nan))
            count = np.sum(np.isfinite(ranks), axis=1, keepdims=True).astype(float)
            with np.errstate(invalid="ignore", divide="ignore"):
                third = np.ceil(ranks * 3 / count)
            labels = np.asarray(["", "Small", "Mid", "Large"], dtype=object)
            bucket = third[t, i]
            if np.isfinite(bucket).any():
                sized = frame[[c, "factor", "factor_quantile"]].copy()
                sized["group"] = np.where(np.isfinite(bucket), labels[np.nan_to_num(bucket, nan=0).astype(int)], "")
                sized = sized[sized["group"] != ""]
                ic = self.group_ic(sized, c)
                measured = "close × shares outstanding" if panel.shares is not None else "30-bar mean dollar volume"
                for name in ("Small", "Mid", "Large"):
                    if name in ic.index:
                        rows.append({"name": f"{name} third by size", "kind": "size", "rankIc": num(ic[name]), "note": measured})
            # The daily IC is the same whichever group a whole day is put in, so the two halves are its means over each half of the days.
            daily = self.ic()[c].dropna()
            if len(daily) >= 20:
                middle = len(daily) // 2
                for part, name in ((daily.iloc[:middle], "First half"), (daily.iloc[middle:], "Second half")):
                    rows.append({"name": f"{name}, {part.index[0].year}–{part.index[-1].year}", "kind": "period", "rankIc": num(part.mean()),
                                 "note": f"{part.index[0].strftime('%b %Y')} to {part.index[-1].strftime('%b %Y')}"})
            if not rows:
                raise Missing("Too few days and names to split by size or by period")
            tab["sizeAndPeriod"] = rows
            self.put(tab, "sizeAndPeriod", "By size and period", figure("factor.results.ic-by-size", [
                trace("bar", "main", "rank-ic", x=[row["name"] for row in rows], y=[row["rankIc"] for row in rows], name="Rank IC",
                      hovertemplate="%{x}<br>Rank IC %{y:.4f}<extra></extra>")], xaxis={"type": "category"}, yaxis={"side": "right", "zeroline": True, "tickformat": ".3f"}))
        self.section(tab, "sizeAndPeriod", size_and_period, figure_keys=[("sizeAndPeriod", "By size and period")])

        def decay():
            run_neutral = self.settings["neutralize"]
            after_neutral = run_neutral if run_neutral != "none" else "industry+size"
            if not has_industry:
                raise Missing("Market gives no industry for these names")
            before = self.decay_with("none")
            after = ev.decay if run_neutral != "none" else self.decay_with(after_neutral)
            tab["decay"] = {"before": nums(before), "after": nums(after), "neutralize": after_neutral, "run": run_neutral}
            days = [f"{day}D" if self.unit == "day" else f"{day}h" if self.unit == "hour" else str(day) for day in range(1, len(before) + 1)]
            self.put(tab, "decay", "IC by holding days, after and before", figure("factor.results.decay-neutral", [
                trace("bar", "group", "before", x=days, y=nums(before), name="Before", index=0, of=2, hovertemplate="%{x}<br>Before %{y:+.4f}<extra></extra>"),
                trace("bar", "main", "after", x=days, y=nums(after), name="After", hovertemplate="%{x}<br>After %{y:+.4f}<extra></extra>")],
                barmode="group", showlegend=True, xaxis={"type": "category", "tickvals": [days[k] for k in (0, 4, 9, 14, 19) if k < len(days)]},
                yaxis={"side": "right", "tickformat": ".3f", "zeroline": True}))
        self.section(tab, "decay", decay, figure_keys=[("decay", "IC by holding days, after and before")])

        def method():
            about = " ".join(note for note in panel.notes or [] if "industry" in note.lower())  # only the notes about industry
            tab["method"] = {"neutralize": self.settings["neutralize"], "extremes": self.settings["extremes"], "scale": self.settings["scale"],
                             "industry": about or ("Current industry for every year; the source has no industry history (approximate)" if has_industry
                                                   else "No industry for these names"),
                             "size": "Log market value from close × shares outstanding" if panel.shares is not None else "Log of the 30-bar mean dollar volume (no shares outstanding)",
                             "notes": list(panel.notes or [])}
        self.section(tab, "method", method)

    def decay_with(self, neutralize):
        """Rank IC for holding 1 to 20 with the run's preprocessing but another way of taking out industry and size."""
        settings = {**self.settings, "neutralize": neutralize}
        after, _ = W.preprocess(self.raw, self.panel, settings, self.ev.eligible)
        found = W.rank_ics(after, self.panel.fields["close"], list(range(1, W.DECAY_DAYS + 1)), self.delay)
        return [M.mean(found[day][0]) for day in range(1, W.DECAY_DAYS + 1)]

    # ---------------------------------------------------------------- Style
    def tab_style(self, tab):
        ev, panel = self.ev, self.panel
        tab["version"] = S.VERSION
        definitions = S.definitions(self.unit if self.unit != "bar" else "bar", panel.shares is not None)
        found = self.section(tab, "styles", lambda: S.price_styles(panel))
        rows = []
        for item in definitions:
            row = dict(item)
            if item["family"] == "price" and found is not None:
                rho = M.mean(M.rank_correlation(ev.after, found[item["style"]], 10))
                if rho is None:
                    row["unavailable"] = "Too few names with both values on any day"
                else:
                    row["rho"] = num(rho, 4)
            if "rho" in row:  # only a style that can be measured is a row: what Market does not give yet is not listed
                rows.append(row)
        tab["styles"] = rows
        like = [{"name": item["name"], "version": item.get("version"), "kind": "library", "rho": num(item["rho"], 4)} for item in self.similar if item.get("rho") is not None and not item.get("style")]
        like += [{"name": row["style"], "kind": "style", "rho": row["rho"]} for row in rows if row.get("rho") is not None]
        like.sort(key=lambda item: -abs(item["rho"]))
        tab["like"] = like

        def chart():
            if not like:
                raise Missing("No library factor or style shares days and names with this run")
            limit = M.THRESHOLDS["library"]["value"]
            names = [f"{item['name']} {item['version']}" if item["kind"] == "library" and item.get("version") else item["name"] for item in like][::-1]
            values = [item["rho"] for item in like][::-1]
            kinds = [item["kind"] for item in like][::-1]
            traces = []
            for kind, role, label in (("library", "main", "Library"), ("style", "group", "Style")):
                traces.append(trace("bar", role, kind, orientation="h", y=names, x=[v if k == kind and not (k == "library" and abs(v) >= limit) else None for v, k in zip(values, kinds, strict=True)],
                                    name=label, hovertemplate="%{y}<br>%{x:+.3f}<extra></extra>", **({"index": 1, "of": 2} if role == "group" else {})))
            over = [v if k == "library" and abs(v) >= limit else None for v, k in zip(values, kinds, strict=True)]
            if any(v is not None for v in over):
                traces.append(trace("bar", "attention", "over-limit", orientation="h", y=names, x=over, name="Above the library limit",
                                    hovertemplate="%{y}<br>%{x:+.3f}<extra></extra>"))
            traces += [reference([limit, limit], [names[0], names[-1]], "limit:+", "Library limit"), reference([-limit, -limit], [names[0], names[-1]], "limit:-", "Library limit")]
            self.put(tab, "like", "Like this factor", figure("factor.results.like", traces, barmode="overlay",
                                                             xaxis={"range": [-1, 1], "zeroline": True, "tickformat": ".1f"}, yaxis={"type": "category", "automargin": True, "side": "left"},
                                                             margin={"l": 8, "r": 16, "t": 8, "b": 26}))
        self.section(tab, "like", chart, figure_keys=[("like", "Like this factor")])

        def residual():
            if found is None:
                raise Missing(tab["missing"]["styles"])
            left = S.residual_of(ev.after, found)
            ic, _ = W.rank_ic(left, panel.fields["close"], self.h, self.delay)
            if num(M.mean(ic)) is None:
                raise Missing("Too few names have the price styles on any day")
            tab["residual"] = {"rankIc": num(M.mean(ic)), "before": num(ev.numbers["rankIc"]), "t": num(M.newey_west_t(ic, max(self.h - 1, 0)), 4),
                               "styles": [name for name in found]}
        self.section(tab, "residual", residual)

    # ---------------------------------------------------------------- Turnover
    def tab_turnover(self, tab):
        def table():
            from alphalens import performance as perf
            from alphalens import plotting
            frame = self.factor_data()
            quantile = frame["factor_quantile"]
            groups = sorted(quantile.unique().tolist())
            turnover = {p: pd.concat([perf.quantile_turnover(quantile, q, p) for q in groups], axis=1) for p in self.periods}
            autocorrelation = pd.concat([perf.factor_rank_autocorrelation(frame, p) for p in self.periods], axis=1)
            table_, autocorr = plotting.plot_turnover_table(autocorrelation, turnover, return_df=True)
            rows = []
            for p in self.periods:
                rows.append({"period": p, "groups": [num(table_.loc[f"Quantile {q} Mean Turnover ", f"{p}D"], 4) for q in groups],
                             "autocorrelation": num(autocorr.loc["Mean Factor Rank Autocorrelation", f"{p}D"], 4)})
            tab["turnover"] = rows
            tab["groups"] = [int(q) for q in groups]
            return turnover, autocorrelation, groups
        found = self.section(tab, "turnover", table, figure_keys=[("topBottom", "Top and bottom group turnover"), ("autocorrelation", "Factor rank autocorrelation")])
        if found is None:
            return
        turnover, autocorrelation, groups = found

        def top_bottom():
            frame = turnover[self.h] if self.h in turnover else turnover[self.periods[-1]]
            x = iso(frame.index)
            top, bottom = frame[max(groups)], frame[min(groups)]
            self.put(tab, "topBottom", "Top and bottom group turnover", figure("factor.results.turnover", [
                line(x, nums(top.to_numpy(), 4), "detail", "top:daily", "Top group", hoverinfo="skip"),
                line(x, nums(bottom.to_numpy(), 4), "detail", "bottom:daily", "Bottom group", hoverinfo="skip"),
                # The top group warm and the bottom cold (index 1 and 0 of 2), as the groups chart draws its highest group warm.
                line(x, nums(top.rolling(window=ROLLING).mean().to_numpy(), 4), "group", "top", "Top group, 1-month mean", index=1, of=2, hovertemplate="Top %{y:.1%}<extra></extra>"),
                line(x, nums(bottom.rolling(window=ROLLING).mean().to_numpy(), 4), "group", "bottom", "Bottom group, 1-month mean", index=0, of=2,
                     hovertemplate="Bottom %{y:.1%}<extra></extra>")],
                hovermode="x unified", showlegend=True, legend={"x": 0, "xanchor": "left", "y": 1.02, "yanchor": "bottom"}, xaxis={"type": "date"},
                yaxis={"side": "right", "tickformat": ".0%"}))
        self.section(tab, "topBottom", top_bottom, figure_keys=[("topBottom", "Top and bottom group turnover")])

        def autocorr():
            series = autocorrelation[self.h] if self.h in autocorrelation else autocorrelation.iloc[:, -1]
            x = iso(series.index)
            self.put(tab, "autocorrelation", "Factor rank autocorrelation", figure("factor.results.rank-autocorrelation", [
                line(x, nums(series.to_numpy(), 4), "detail", "autocorrelation", "Rank autocorrelation", hoverinfo="skip"),
                line(x, nums(series.rolling(window=ROLLING).mean().to_numpy(), 4), "main", "autocorrelation:mean", "1-month mean",
                     hovertemplate="Rank autocorrelation %{y:.3f}<extra></extra>")],
                hovermode="x unified", xaxis={"type": "date"}, yaxis={"side": "right", "zeroline": True},
                annotations=[{"x": 0.01, "y": 0.98, "xref": "paper", "yref": "paper", "xanchor": "left", "yanchor": "top", "showarrow": False,
                              "text": f"Mean {series.mean():.3f}", "name": "summary"}]))
        self.section(tab, "autocorrelation", autocorr, figure_keys=[("autocorrelation", "Factor rank autocorrelation")])

    # ---------------------------------------------------------------- Costs
    def tab_costs(self, tab):
        ev, panel = self.ev, self.panel
        cost_bps, borrow_pct = self.settings["costBps"], self.settings["borrowPct"]

        def split():
            r = ev.returns
            top = W._held_fast(W.equal_weights(ev.groups == ev.count), self.h, self.delay)
            bottom = W._held_fast(W.equal_weights(ev.groups == 1), self.h, self.delay)
            long_leg = np.abs(np.diff(top, axis=0, prepend=np.zeros((1, top.shape[1])))).sum(axis=1)
            short_leg = np.abs(np.diff(bottom, axis=0, prepend=np.zeros((1, bottom.shape[1])))).sum(axis=1)
            active = r["active"]
            gross = M.annual_mean(r["gross"], self.A)
            # The trading cost is charged on the change of the whole book; each leg's share is its share of what was traded.
            trading = M.annual_mean(r["cost"], self.A)
            legs = float(np.nansum(np.where(active, long_leg, np.nan))), float(np.nansum(np.where(active, short_leg, np.nan)))
            share = legs[0] / sum(legs) if sum(legs) > 0 else 0.5
            trade_long = None if trading is None else trading * share
            trade_short = None if trading is None else trading * (1 - share)
            borrow = M.annual_mean(r["borrow"], self.A)
            tab["split"] = {"gross": num(gross), "tradingLong": num(trade_long), "tradingShort": num(trade_short), "borrow": num(borrow),
                            "net": num(M.annual_mean(r["net"], self.A)), "breakEven": num(ev.numbers["breakEven"], 3), "costBps": cost_bps, "borrowPct": borrow_pct,
                            "traded": num(M.annual_mean(r["traded"], self.A), 4)}
            traded = M.mean(r["traded"])
            highest = max(20.0, 4 * cost_bps, (ev.numbers["breakEven"] or 0) * 1.25)
            xs = np.round(np.arange(0, highest + 1e-9, 0.5 if highest <= 40 else 1.0), 2)
            nets = [None if gross is None or traded is None else num(gross - x / BPS * traded * self.A - (borrow or 0.0)) for x in xs]
            top_y = [value for value in nets if value is not None]
            even = ev.numbers["breakEven"]
            marks, notes = [], [{"x": cost_bps, "y": 1, "yref": "paper", "text": "assumed", "name": "assumed", "showarrow": False, "xanchor": "left", "yanchor": "top", "xshift": 4}]
            if even is not None and even >= 0 and gross is not None and traded is not None:
                at = num(gross - even / BPS * traded * self.A - (borrow or 0.0))
                marks.append(trace("scatter", "main", "break-even", mode="markers", x=[num(even, 1)], y=[at], name="Break-even", marker={"size": 7}, hoverinfo="skip"))
                notes.append({"x": num(even, 1), "y": at, "text": f"break-even {even:.1f}", "name": "break-even", "showarrow": False, "xanchor": "left", "yanchor": "bottom", "xshift": 6})
            elif even is not None:
                notes.append({"x": 0.99, "y": 0.02, "xref": "paper", "yref": "paper", "text": "loses before costs", "name": "loses", "showarrow": False, "xanchor": "right", "yanchor": "bottom"})
            self.put(tab, "byCost", "Net by cost a trade", figure("factor.results.net-by-cost", [
                line(nums(xs, 2), nets, "main", "net", "Net", hovertemplate="%{x} bps a trade<br>Long–short, net %{y:+.2%}<extra></extra>"),
                reference([cost_bps, cost_bps], [min(top_y, default=0), max(top_y, default=0)], "assumed", f"{cost_bps:g} bps assumed"), *marks],
                annotations=notes, xaxis={"zeroline": False}, yaxis={"side": "right", "tickformat": ".0%", "zeroline": True}))
        self.section(tab, "split", split, figure_keys=[("byCost", "Net by cost a trade")])

        def holdings():
            rows = []
            shared = self.ev.shared()
            for holding in HOLDINGS:
                if holding >= len(panel.labels) // 3:
                    continue
                if holding == self.h:
                    found = ev
                else:
                    found = W.Evaluation(panel, self.raw, {**self.settings, "holding": holding}, formula=self.formula, with_decay=False, with_similar=False,
                                         shared={**shared, "decay": ev.decay})
                n = found.numbers
                gross, net = n["longShortGross"], n["longShortNet"]
                rows.append({"holding": holding, "rankIc": num(n["rankIc"]), "gross": num(gross), "turnover": num(n["turnover"], 4),
                             "costs": None if gross is None or net is None else num(net - gross), "net": num(net), "sharpe": num(n["sharpe"], 4),
                             "breakEven": num(n["breakEven"], 3)})
            tab["holdings"] = rows
        self.section(tab, "holdings", holdings)

    # ---------------------------------------------------------------- Event study
    def tab_event(self, tab):
        """Mean return around each day's values, by group, 5 bars before to 20 after: the mean over days of each group's mean, relative to
        what the run's settings say (nothing for Raw, all names with a value that day for Demeaned, the names of the same industry for Group
        neutral), as Alphalens' average_cumulative_return_by_quantile with the run's demeaned and group_adjust (which in 0.5.1 averages each
        bar's return); the cumulative path adds those up from the day of the value."""
        ev, panel = self.ev, self.panel

        def event():
            close = panel.fields["close"]
            with np.errstate(invalid="ignore", divide="ignore"):
                daily = np.vstack([np.full((1, close.shape[1]), np.nan), close[1:] / close[:-1] - 1])
            daily[~np.isfinite(daily)] = np.nan
            # The names and days of Alphalens' factor table (a value, a group and every forward return), as its function would take them.
            self.factor_data()
            valued = np.zeros(ev.after.shape, dtype=bool)
            valued[self._factor_data[1], self._factor_data[2]] = True
            offsets = list(range(-EVENT_BEFORE, EVENT_AFTER + 1))
            codes = None
            if self.group_adjust:
                if panel.industry is None:
                    raise Missing("Group neutral needs an industry for each name, and Market gives none for these names")
                codes = pd.factorize(np.asarray([item or "Unknown" for item in panel.industry], dtype=object))[0]
            tab["basis"] = "group" if codes is not None else "all" if self.demeaned else "raw"
            groups = sorted(int(g) for g in np.unique(ev.groups[valued]))
            if not groups:
                raise Missing("No day has values in groups")
            means = np.full((len(groups), len(offsets)), np.nan)
            spreads = np.full_like(means, np.nan)
            T = len(daily)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                for j, k in enumerate(offsets):
                    shifted = np.full_like(daily, np.nan)
                    if k >= 0:
                        shifted[:T - k] = daily[k:]
                    else:
                        shifted[-k:] = daily[:T + k]
                    universe = np.where(valued, shifted, np.nan)
                    if codes is not None:
                        relative = np.array(shifted, copy=True)
                        for code in np.unique(codes):
                            inside = codes == code
                            relative[:, inside] = shifted[:, inside] - np.nanmean(universe[:, inside], axis=1, keepdims=True)
                    elif self.demeaned:
                        relative = shifted - np.nanmean(universe, axis=1, keepdims=True)
                    else:
                        relative = shifted
                    for g_index, g in enumerate(groups):
                        by_day = np.nanmean(np.where(valued & (ev.groups == g), relative, np.nan), axis=1)
                        means[g_index, j] = np.nanmean(by_day)
                        spreads[g_index, j] = np.nanstd(by_day, ddof=1)
            zero = offsets.index(0)
            path = np.zeros_like(means)
            for j in range(len(offsets)):
                if j > zero:
                    path[:, j] = np.nansum(means[:, zero + 1:j + 1], axis=1)
                elif j < zero:
                    path[:, j] = -np.nansum(means[:, j + 1:zero + 1], axis=1)
            tab["offsets"] = offsets
            tab["event"] = [{"group": g, "mean": nums(means[k], 8), "std": nums(spreads[k], 8), "cumulative": nums(path[k], 8)} for k, g in enumerate(groups)]
            pick = {f: offsets.index(f) for f in (-EVENT_BEFORE, 1, 5, 10, 20) if f in offsets}
            # "before" is the move over the days up to the signal (the path's value there, which is minus that move, read backwards)
            tab["table"] = [{"group": g, "before": num(-path[k, pick[-EVENT_BEFORE]], 8), "after1": num(path[k, pick[1]], 8), "after5": num(path[k, pick[5]], 8),
                             "after10": num(path[k, pick[10]], 8), "after20": num(path[k, pick[20]], 8)} for k, g in enumerate(groups)]
            labels = [f"+{k}" if k > 0 else str(k) for k in offsets]
            # Cumulative return in percent, as the table under it and the draft's chart (not bps: one tab, one unit).
            top = float(np.nanmax(np.abs(path))) if np.isfinite(path).any() else .01
            traces = [trace("scatter", "group", f"group:{g}", mode="lines", x=labels, y=nums(path[k], 8), name=f"Q{g}", index=k, of=len(groups),
                            hovertemplate=f"Q{g} %{{x}}: %{{y:+.2%}}<extra></extra>") for k, g in enumerate(groups)]
            traces.append(reference(["0", "0"], [-top, top], "signal", "Day of the value"))
            annotations = [{"x": labels[-1], "y": num(path[k, -1], 8), "text": f"Q{g}", "name": f"group:{g}", "showarrow": False, "xanchor": "left", "xshift": 4}
                           for k, g in enumerate(groups) if len(groups) <= 10 or k in (0, len(groups) - 1)]
            self.put(tab, "event", "Around the signal", figure("factor.results.event", traces, hovermode="x unified", annotations=annotations,
                                                               xaxis={"type": "category", "title": {"text": "Bars from the value"}},
                                                               yaxis={"side": "right", "zeroline": True, "tickformat": ".1%"}))
        self.section(tab, "event", event, figure_keys=[("event", "Around the signal")])


# ---------------------------------------------------------------------------------------------------------------- the job
def write_json(path, value):
    temporary = Path(str(path) + ".part")
    temporary.write_text(json.dumps(value, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def progress(output, stage, **values):
    with open(Path(output) / "progress.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": int(time.time() * 1000), "stage": stage, **values}) + "\n")


def versions():
    import importlib.metadata as metadata
    out = {"python": ".".join(map(str, sys.version_info[:3]))}
    for name in ("alphalens-reloaded", "pandas", "numpy", "qlib", "pyqlib"):
        try:
            out[name.replace("-reloaded", "").replace("pyqlib", "qlib")] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return out


def run(job, *, only=None):
    """Build every tab (or those in `only`) of one run's Results; each tab's file is written as soon as it is done."""
    from native.factor_resident import load_panel
    started = time.perf_counter()
    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    tabs = [tab for tab in BUILD_ORDER if only is None or tab in only]
    progress(output, "load", state="running", done=0, total=len(tabs))
    panel = load_panel({**job["data"], "libraryPath": None})
    raw = np.load(job["values"])
    results = Results(job, panel, raw)
    states = {}
    for done, name in enumerate(tabs):
        progress(output, name, state="running", done=done, total=len(tabs))
        began = time.perf_counter()
        try:
            tab = results.build(name)
            tab["seconds"] = round(time.perf_counter() - began, 2)
            write_json(output / f"{name}.json", tab)
            states[name] = "done"
        except Exception as error:  # noqa: BLE001 - one tab that breaks says why; the others are still built
            traceback.print_exc()
            write_json(output / f"{name}.json", {"tab": name, "holding": results.h, "unit": results.unit, "periods": results.periods,
                                                 "periodLabels": [results.label(p) for p in results.periods], "figures": {},
                                                 "missing": {"tab": reason_of(error)}, "failed": reason_of(error)})
            states[name] = "failed"
        progress(output, name, state=states[name], done=done + 1, total=len(tabs))
    seconds = round(time.perf_counter() - started, 2)
    answer = {"ok": True, "seconds": seconds, "tabs": states, "about": {"styles": S.VERSION, "versions": versions(), "seconds": seconds, "unit": results.unit,
                                                                        "shares": results.panel.shares is not None}}
    write_json(output / "answer.json", answer)
    progress(output, "done", state="done", done=len(tabs), total=len(tabs), seconds=seconds)
    return answer


def main():
    invocation = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if invocation.get("support"):
        sys.path.insert(0, invocation["support"])
    os.environ.setdefault("MPLBACKEND", "Agg")
    import warnings
    warnings.simplefilter("ignore", FutureWarning)  # pandas' notices about Alphalens' own calls
    warnings.simplefilter("ignore", RuntimeWarning)  # empty days in nan-means; those days stay empty
    job = invocation["job"]
    output = Path(job["output"])
    try:
        run(job)
        return 0
    except Exception as error:  # noqa: BLE001 - the run shows what broke, not a folder or an internal id
        traceback.print_exc()
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "answer.json", {"ok": False, "message": f"The Results stopped: {reason_of(error)}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
