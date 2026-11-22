"""What a finished model run shows: the key numbers, the two baselines, the checks, and every Results section with its figures.

Numbers are measured on the test periods only (each window predicts the period after its training and validation), with the measures of
`metrics` and the long–short of `factor_work`, so a model and a factor are scored the same way. Baselines belong to the inputs and the
label: the strongest single input (chosen on each window's training segment, sign included) and the inputs ranked and averaged with
the signs they had in training. Checks use in-sample numbers only; the held-out period is never in them.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
from native import model_algorithms as A

from . import factor_work as W
from . import i18n
from . import metrics as M
from . import model_data as D
from . import model_figures as G

GROUPS = 5
REBALANCE = (1, 5, 10, 20)
COSTS = (0.0, 2.0, 5.0, 10.0, 20.0, 40.0)
EARLY_ROUNDS = 3   # a window best by this round learned almost nothing


def _iso(dates):
    return [stamp.strftime("%Y-%m-%d") for stamp in dates]


def _mean(values):
    return M.mean(values)


def daily_ic(values, label):
    return M.rank_correlation(values, label, W.WINDOW_NAMES)


class OpenToOpen:
    """The panel as the long–short reads it: its returns are open to open (opens held over a halt, as the label holds them), so a position taken on
    day t's signal is bought at the next open and earns the very return the label measures."""

    def __init__(self, panel):
        self.panel, self.annualization = panel, panel.annualization
        opens = D.held(pd.DataFrame(panel.fields["open"]), pd.DataFrame(panel.fields["close"])).to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            self.returns = np.vstack([np.full((1, opens.shape[1]), np.nan), opens[1:] / opens[:-1] - 1])

    def derived(self, name):
        return self.returns if name == "returns" else self.panel.derived(name)

    def __getattr__(self, name):
        return getattr(self.panel, name)


def ls_of(panel, pred, days, cost, borrow=M.DEFAULT_BORROW_PCT):
    """The long–short of the prediction held `days` bars from the next open: open-to-open returns with one bar's delay, the label's own return."""
    groups = W.group_of(pred, {"grouping": {"method": "equal", "count": GROUPS, "withinIndustry": False}})
    settings = {"holding": int(days), "delay": 1, "costBps": float(cost), "borrowPct": float(borrow)}
    return groups, W.returns_of(OpenToOpen(panel), groups, GROUPS, settings, pred)


class Report:
    """One run's numbers and Results from its predictions (bars × names, missing outside the test periods)."""

    def __init__(self, data, pred, windows, *, run_settings, per_window=True, ridge=None, importance=None, curves=None, train_pred=None, valid_pred=None):
        self.data, self.panel = data, data.panel
        self.pred = np.where(data.rows, pred, np.nan)
        self.windows = windows
        self.per_window = per_window
        self.run = run_settings
        self.cost = float(run_settings.get("cost", M.DEFAULT_TRADE_BPS))
        self.top_n = int(run_settings.get("topN", 50))
        self.ridge = ridge
        self.importance = importance or []
        self.curves = curves or []
        self.days = data.days
        self.label = np.where(data.rows, data.label_raw, np.nan)
        self.ic = daily_ic(self.pred, self.label)
        self.A = self.panel.annualization
        self.train_pred, self.valid_pred = train_pred, valid_pred
        self.missing = {}

    # ------------------------------------------------------------------------------------------------------------ inputs
    def feature_ics(self):
        """Each feature's daily rank IC against the label (on its rows)."""
        if not hasattr(self, "_feature_ics"):
            x = self.data.x
            self._feature_ics = np.stack([daily_ic(np.where(self.data.rows, x[:, :, f], np.nan), self.label) for f in range(x.shape[2])], axis=1)
        return self._feature_ics

    def spans(self):
        """Each window's training and test positions; without windows the predicted days are one window."""
        if self.windows:
            return [(w["trainSpan"], w["testSpan"]) for w in self.windows]
        days = np.nonzero(np.isfinite(self.pred).any(axis=1))[0]
        return [((int(days[0]), int(days[-1])), (int(days[0]), int(days[-1])))] if len(days) else []

    def baselines(self):
        """The strongest single input and the inputs ranked and averaged, each with the signs of its training segment, on the test periods."""
        ics = self.feature_ics()
        best = np.full(len(self.ic), np.nan)
        equal = np.full(len(self.ic), np.nan)
        chosen = []
        x = self.data.x
        for train, test in self.spans():
            means = np.array([M.mean(ics[train[0]:train[1] + 1, f]) or 0.0 for f in range(ics.shape[1])])
            pick = int(np.argmax(np.abs(means)))
            sign = 1.0 if means[pick] >= 0 else -1.0
            chosen.append((pick, sign))
            first, last = test
            best[first:last + 1] = ics[first:last + 1, pick] * sign
            signs = np.where(means >= 0, 1.0, -1.0)
            block = np.where(self.data.rows[first:last + 1, :, None], x[first:last + 1], np.nan)
            ranked = np.stack([D._rank(block[:, :, f]) for f in range(block.shape[2])], axis=2)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                combined = np.nanmean(ranked * signs[None, None, :], axis=2)
            equal[first:last + 1] = daily_ic(combined, self.label[first:last + 1])
        counts = {}
        for pick, _sign in chosen:
            counts[pick] = counts.get(pick, 0) + 1
        top = max(counts, key=counts.get) if counts else 0
        self.best_series, self.equal_series = best, equal
        spans = self.spans()
        each = lambda series, index: M.mean(series[spans[index][1][0]:spans[index][1][1] + 1])  # noqa: E731
        name = self.data.features[top] if self.data.features else None
        if name is not None and len(counts) > 1:   # each window takes its own strongest input: the name says how often this one was it
            name = f"{name} in {counts[top]} of {len(chosen)} windows"
        return {"bestInput": {"name": name, "ic": M.mean(best),
                              "windows": [{"k": index + 1, "name": self.data.features[pick], "sign": sign, "ic": each(best, index)}
                                          for index, (pick, sign) in enumerate(chosen)]},
                "equalWeight": {"mean": M.mean(equal), "windows": [each(equal, index) for index in range(len(spans))]}, "ridge": M.mean(daily_ic(self.ridge, self.label)) if self.ridge is not None else None}

    def like(self):
        """The input the prediction is most like: the highest mean daily rank correlation with a feature."""
        best = None
        x = self.data.x
        test = np.isfinite(self.pred)
        for f, name in enumerate(self.data.features):
            rho = M.mean(M.rank_correlation(self.pred, np.where(test, x[:, :, f], np.nan), 10))
            if rho is not None and (best is None or abs(rho) > abs(best["rho"])):
                best = {"name": name, "rho": rho}
        return best or {"name": None, "rho": None}

    # ------------------------------------------------------------------------------------------------------------ key numbers
    def numbers(self):
        groups, returns = ls_of(self.panel, self.pred, self.days, self.cost)
        self.groups, self.returns = groups, returns
        windows = self.windows
        train_ics = [w["trainIc"] for w in windows if w.get("trainIc") is not None]
        positive = sum(1 for w in windows if (w.get("testIc") or 0) > 0)
        base = self.baselines()
        ric = M.mean(self.ic)
        like = self.like()
        self.base = base
        found = self.key_numbers(ric, returns, base, groups, positive, windows, train_ics, like)
        found["trainTestGap"] = None if found["trainIc"] is None or ric is None else found["trainIc"] - ric
        found["testDays"] = found["days"]
        if found["lsGross"] is None or found["lsGross"] <= 0:
            found["breakeven"], found["breakevenNote"] = None, "Gross long–short is not positive"
        return found

    def key_numbers(self, ric, returns, base, groups, positive, windows, train_ics, like):
        # Every return the model page measures is a book that pays for itself (long-short, top against the average; the portfolio backtest's excess over
        # its benchmark): it is already a return over cash, so no Sharpe here takes a rate off (design.md, risk-free rate; the factor's spread the same)
        return {"ric": ric, "t": M.newey_west_t(self.ic, max(self.days - 1, 0)), "icir": _ir(self.ic),
                "vsInput": None if ric is None or base["bestInput"]["ic"] is None else ric - base["bestInput"]["ic"],
                "lsNet": M.annual_mean(returns["net"], self.A), "lsGross": M.annual_mean(returns["gross"], self.A),
                "turnover": W.turnover_of(groups, GROUPS, self.days), "trainIc": _mean(train_ics) if train_ics else None,
                "positive": positive, "windows": len(windows), "breakeven": M.break_even_bps(returns["gross"], returns["traded"]),
                "like": like, "sharpe": M.sharpe(returns["net"], self.A),
                "days": int(np.sum(np.isfinite(self.ic))), "names": int(np.median(np.sum(np.isfinite(self.pred), axis=1)[np.isfinite(self.pred).any(axis=1)]))
                if np.isfinite(self.pred).any() else 0}

    def checks(self, numbers, future=None):
        """The model's checks: t, beating both baselines, windows above zero, train to test, learning past the first rounds, copying an input, break-even cost and
        future data (run when published). In-sample numbers only."""
        T = M.THRESHOLDS
        out = []

        def add(identity, name, value, need, passed, title, go):
            out.append({"id": identity, "name": name, "value": value, "need": need, "state": "notrun" if passed is None else "ok" if passed else "watch",
                        "title": title, "go": go})

        def sign(value, digits=4):
            return "—" if value is None else ("+" if value > 0 else "−" if value < 0 else "") + f"{abs(value):.{digits}f}"

        t = numbers["t"]
        add("significance", "t", "—" if t is None else f"{t:.2f}", T["significance"]["need"], None if t is None else M.passes("significance", t),
            f"t of the mean daily rank IC on the test periods, Newey–West for overlapping {self.days}-bar labels", "r:information")
        named = [(value, which) for value, which in ((self.base["bestInput"]["ic"], "strongest input"), (self.base["equalWeight"]["mean"], "inputs averaged")) if value is not None]
        best, which = max(named, key=lambda item: item[0]) if named else (None, None)
        ric = numbers["ric"]
        add("baselines", "Beats the baselines", f"{sign(ric)} vs {sign(best)}" + (f" ({which})" if which else ""), "above both", None if ric is None or best is None else ric > best,
            f"Strongest single input {sign(self.base['bestInput']['ic'])}, inputs ranked and averaged {sign(self.base['equalWeight']['mean'])}", "r:inputs")
        if self.per_window and numbers["windows"]:
            share = numbers["positive"] / numbers["windows"]
            add("windows", "Windows above 0", f"{numbers['positive']} of {numbers['windows']}", "≥ 75%", share >= 0.75, "Test periods whose mean rank IC is above zero", "r:windows")
            train = numbers["trainIc"]
            keeps = None if train in (None, 0) or ric is None else ric / train
            add("train_test", "Train to test", f"{sign(train, 3)} → {sign(ric, 3)}", "keeps ≥ 10%", None if keeps is None else keeps >= 0.10,
                "The test rank IC against the training rank IC (trees fit their training rows closely, so this is a ratio, not a difference)", "p:algorithm")
            stopped = [w["best"] for w in self.windows if w.get("best") is not None]
            if stopped:   # a model whose validation loss is best by round 3 has learned almost nothing past its first trees or epochs
                early = sum(1 for best in stopped if best <= EARLY_ROUNDS)
                add("learned", "Learned past the first rounds", f"{early} of {len(stopped)} windows best by round {EARLY_ROUNDS}", "under half", early * 2 < len(stopped),
                    "Windows whose validation loss was lowest within the first rounds: their model is one or two trees (or epochs), so the batch compares little",
                    "p:algorithm")
        like = numbers["like"]
        rho = None if like["rho"] is None else abs(like["rho"])
        add("copies", "Copies an input", "—" if rho is None else f"{like['name']} {rho:.2f}", T["library"]["need"], None if rho is None else rho < 0.7,
            "The highest mean daily rank correlation of the prediction with one input", "p:inputs")
        breakeven = numbers["breakeven"]
        none = breakeven is None and numbers.get("breakevenNote")  # the long–short before cost is not positive: no cost makes it zero
        add("cost_room", "Break-even cost", "none" if none else "—" if breakeven is None else f"{breakeven:.1f} bps", f"≥ {2 * self.cost:g} bps",
            False if none else None if breakeven is None else M.passes("cost_room", breakeven, self.cost), f"Break-even cost a trade against twice the {self.cost:g} bps assumed", "p:run")
        if future is None:
            add("future_data", "Future data", "runs when published", "unchanged", None, "Publishing retrains the cut-off window on data cut there and compares", "publish")
        else:
            add("future_data", "Future data", future["value"], "unchanged", future["passed"], future["title"], "publish")
        return out

    # ------------------------------------------------------------------------------------------------------------ Results
    def results(self, numbers, *, library=None):
        """Every Results section: tables and figures; a figure that could not be drawn is listed with why."""
        sections = {}
        figures = {}
        dates = self.panel.dates
        first_day = int(np.argmax(np.isfinite(self.pred).any(axis=1))) if np.isfinite(self.pred).any() else 0

        def comp(series):  # running totals start with the first prediction: the days before it have none
            out = np.asarray(M.compounded(series), dtype=float)
            out[:first_day] = np.nan
            return out

        def dd(series):
            out = np.asarray(_drawdown(series), dtype=float)
            out[:first_day] = np.nan
            return out

        def attempt(key, build):
            try:
                found = build()
                if found is not None:
                    figures[key] = found
            except Exception as error:  # noqa: BLE001 - the section says which figure is missing and why
                self.missing[key] = f"Not drawn: {str(error)[:200] or type(error).__name__}"

        # Windows
        rows = [{key: w[key] for key in ("k", "train", "valid", "test", "trainIc", "validIc", "testIc", "best", "rows", "secs")} for w in self.windows]
        sections["windows"] = {"table": rows, "perWindow": self.per_window}
        # An algorithm that trains on its validation rows keeps no validation IC (model_algorithms, include_valid): no dots and no validation bars.
        validated = any(w.get("validIc") is not None for w in self.windows)
        if self.windows and not validated:
            self.missing["model.windows.valid-test"] = "This algorithm trains on its validation rows, so it keeps no validation IC"
        attempt("model.windows.valid-test", lambda: G.figure("model.windows.valid-test", [
            {"type": "scatter", "mode": "markers+text", "x": [w["validIc"] for w in self.windows], "y": [w["testIc"] for w in self.windows],
             "text": [str(w["k"]) for w in self.windows], "textposition": "top center", "name": "Windows", "meta": {"role": "main", "key": "windows"},
             "hovertemplate": "Window %{text}<br>Validation Rank IC %{x:+.4f}<br>Test Rank IC %{y:+.4f}<extra></extra>"},
            G.line(*_diagonal(self.windows), "Same", "benchmark", "same", hovertemplate="Test equals validation<extra></extra>")], xaxis={"title": {"text": i18n.t("Validation rank IC")}}, yaxis={"title": {"text": i18n.t("Test rank IC")}}) if validated else None)
        if self.windows:
            names = [str(w["k"]) for w in self.windows]
            attempt("model.windows.each", lambda: G.figure("model.windows.each", [
                *([{"type": "bar", "name": "Validation", "x": names, "y": [w["validIc"] for w in self.windows], "marker": {"color": "rgba(0,0,0,0)", "line": {"width": 1.2}},
                    "meta": {"role": "group", "key": "validation"}}] if validated else []),
                {"type": "bar", "name": "Test", "x": names, "y": [w["testIc"] for w in self.windows], "meta": {"role": "updown", "key": "test"}}],
                barmode="group", xaxis={"type": "category", "title": {"text": i18n.t("Window")}}, yaxis={"tickformat": ".3f", "side": "right"},
                shapes=[{"type": "line", "xref": "paper", "yref": "y", "x0": 0, "x1": 1, "y0": numbers["ric"] or 0, "y1": numbers["ric"] or 0, "meta": {"role": "benchmark", "key": "mean"}}]))
        neural = any(w.get("algorithm") in A.NEURAL_IDS for w in self.windows)
        if self.importance:
            top = _top_features(self.importance, 20)
            sections["windows"]["importance"] = {"features": top, "windows": [w["k"] for w in self.windows],
                                                 "values": [[round(entry.get(name, 0.0), 6) for name in top] for entry in self.importance]}
            attempt("model.windows.importance", lambda: G.figure("model.windows.importance", [
                {"type": "heatmap", "x": [f"W{w['k']}" for w in self.windows], "y": top, "z": [[entry.get(name, 0.0) for entry in self.importance] for name in top],
                 "customdata": _rank_among(top, self.importance),
                 "name": "Share of gain", "meta": {"role": "group", "key": "importance"},
                 "hovertemplate": "%{y} · window %{x}" + (" · permutation" if neural else "") + "<br>Share %{z:.1%}<br>Rank among these %{customdata}<extra></extra>"}],
                yaxis={"autorange": "reversed"}))
        if any(curve for curve in self.curves):
            attempt("model.windows.learning", lambda: _learning(self.curves, self.windows))
        else:
            self.missing.setdefault("model.windows.learning", "This algorithm keeps no loss by round (linear models fit in one step)")

        # Returns
        net, gross, traded = self.returns["net"], self.returns["gross"], self.returns["traded"]
        relative = self.returns["relative"]
        base_return = self._universe_return()
        alpha, beta = _alpha_beta(gross, base_return, self.A)
        risk = _risk_table({"Long–short": gross, "Long–short after cost": net, "Top against the average": relative[:, -1]}, self.A)
        top = self._top_n()
        top_net = top["excess"] - self.cost / 1e4 * top["turnover"] * 2

        def line(name, series, extra=None):
            return {"name": name, "annual": M.annual_mean(series, self.A), "sharpe": M.sharpe(series, self.A),
                    "maxDrawdown": float(np.min(_drawdown(series))) if np.isfinite(series).any() else None, **(extra or {})}

        def stats(series):
            valid = np.asarray(series, dtype=float)[np.isfinite(series)]
            return {"meanBps": float(valid.mean() * 1e4) if len(valid) else None, "stdBps": float(valid.std(ddof=1) * 1e4) if len(valid) > 1 else None,
                    "annual": M.annual_mean(series, self.A), "ir": _ir(series), "maxDrawdown": float(np.min(_drawdown(series))) if len(valid) else None}
        sections["returns"] = {
            "longShort": {**risk, "alpha": alpha, "beta": beta, "rows": [
                line("Long–short", gross, {"alpha": alpha, "beta": beta}), line("Long–average", relative[:, -1]), line("Long–short, net", net),
                line(f"Top {top['n']}, excess", top["excess"], dict(zip(("alpha", "beta"), _alpha_beta(top["excess"], base_return, self.A), strict=True))), line(f"Top {top['n']}, excess, net", top_net)]},
            "rebalance": [self._rebalance(days) for days in REBALANCE],
            "byCost": [{"costBps": cost, "lsNet": M.annual_mean(gross - cost / 1e4 * traded - self.returns["borrow"], self.A)} for cost in COSTS],
            "topN": {"n": self.top_n, "excess": M.annual_mean(top["excess"], self.A), "topRank": M.mean(top["topRank"]), "bottomRank": M.mean(top["bottomRank"]),
                     "before": stats(top["excess"]), "after": stats(top_net)},
            "groupMeans": [M.annual_mean(relative[:, g], self.A) for g in range(GROUPS)], "costBps": self.cost,
            # the top group's mean bar over the bottom's, not a year's spread over 252 (a crypto or hourly series has other bars a year)
            "groupSpreadPerBar": None if M.mean(relative[:, -1]) is None or M.mean(relative[:, 0]) is None else M.mean(relative[:, -1]) - M.mean(relative[:, 0])}
        attempt("model.returns.groups", lambda: G.figure("model.returns.groups", [
            *[{**G.line(_iso(dates), comp(relative[:, g]), f"Q{g + 1}", "group", f"q{g + 1}"), "meta": {"role": "group", "key": f"q{g + 1}", "index": g, "of": GROUPS}} for g in range(GROUPS)],
            G.line(_iso(dates), comp(net), "L–S net", "main", "ls-net")], yaxis={"tickformat": ".0%", "side": "right"}, xaxis={"type": "date"}))
        attempt("model.returns.ls-cumulative", lambda: G.figure("model.returns.ls-cumulative", [
            G.line(_iso(dates), comp(gross), "Long–short", "benchmark", "ls-gross"), G.line(_iso(dates), comp(relative[:, -1]), "Long–average", "benchmark", "long-average"),
            G.line(_iso(dates), comp(net), "L–S net", "main", "ls-net")], yaxis={"tickformat": ".0%", "side": "right"}, xaxis={"type": "date"}))
        attempt("model.returns.group-mean", lambda: G.figure("model.returns.group-mean", [
            G.bars([f"Q{g + 1}" for g in range(GROUPS)], [float(np.nanmean(relative[:, g]) * 1e4) for g in range(GROUPS)], "Mean a bar against the average", "updown", "group-mean")],
            yaxis={"side": "right"}))
        attempt("model.returns.ls-drawdown", lambda: G.figure("model.returns.ls-drawdown", [
            G.line(_iso(dates), dd(net), "Long–short, net", "drawdown", "drawdown"), G.line(_iso(dates), dd(top_net), f"Top {top['n']}, excess, net", "benchmark", "drawdown-top")], yaxis={"tickformat": ".0%", "side": "right"}, xaxis={"type": "date"}))
        attempt("model.returns.net-by-cost", lambda: G.figure("model.returns.net-by-cost", [
            G.line([f"{cost:g}" for cost in COSTS], [item["lsNet"] for item in sections["returns"]["byCost"]], "Long–short, net", "main", "ls-net", mode="lines+markers"),
            {"type": "scatter", "mode": "markers", "x": [f"{self.cost:g}"], "y": [numbers["lsNet"]], "name": "Assumed", "meta": {"role": "benchmark", "key": "assumed"}}],
            # the assumed cost sits in its place among the others, not first because its point is a trace of its own
            xaxis={"title": {"text": i18n.t("Cost a trade, bps")}, "type": "category", "categoryorder": "array",
                   "categoryarray": [f"{cost:g}" for cost in sorted({*COSTS, self.cost})]}, yaxis={"tickformat": ".0%", "side": "right"}))
        attempt("model.returns.top-excess", lambda: G.figure("model.returns.top-excess", [
            G.line(_iso(dates), comp(top["excess"]), "Before cost", "main", "top-before"), G.line(_iso(dates), comp(top_net), "After cost", "benchmark", "top-after")],
            yaxis={"tickformat": ".0%", "side": "right"}, xaxis={"type": "date"}))
        attempt("model.returns.top-rank", lambda: G.figure("model.returns.top-rank", [
            G.line(_iso(dates), _rolling(top["topRank"], 63), f"Top {self.top_n}", "main", "top"),
            G.line(_iso(dates), _rolling(top["bottomRank"], 63), f"Bottom {self.top_n}", "benchmark", "bottom")],
            yaxis={"tickformat": ".0%", "side": "right", "title": {"text": i18n.t("Rank of the later return")}}, xaxis={"type": "date"}))

        # Information
        ic_pearson = M.row_correlation(self.pred, self.label, W.WINDOW_NAMES)
        rank = self.ic[np.isfinite(self.ic)]
        t = numbers["t"]
        sections["information"] = {
            "table": {"ic": M.mean(ic_pearson), "icir": _ir(ic_pearson), "rankIc": numbers["ric"], "rankIcir": _ir(self.ic), "t": t,
                      "p": None if t is None else math.erfc(abs(t) / math.sqrt(2)), "skew": _moment(rank, 3), "kurtosis": _moment(rank, 4),
                      "days": int(len(rank)), "lags": max(self.days - 1, 0),
                      "rows": [self._information_row("IC", ic_pearson, None), self._information_row("Rank IC", self.ic, t)]},
            "decay": self._decay(), "industry": self._industry_ic()}
        daily = rank
        if len(daily) > 5:
            edges = np.histogram_bin_edges(daily, bins=30)
            counts, _ = np.histogram(daily, bins=edges)
            mids = (edges[:-1] + edges[1:]) / 2
            # Each bar reads out the interval it counts ("Rank IC +0.020 to +0.030"), not only its middle.
            attempt("model.information.distribution", lambda: G.figure("model.information.distribution", [G.bars(
                [round(float(v), 4) for v in mids], counts, "Days", "group", "days", width=float(edges[1] - edges[0]),
                customdata=[[round(float(low), 4), round(float(high), 4)] for low, high in zip(edges[:-1], edges[1:])],
                hovertemplate="Rank IC %{customdata[0]:+.3f} to %{customdata[1]:+.3f}<br>Days %{y}<extra></extra>")],
                bargap=0, hovermode="closest", xaxis={"type": "linear"}, yaxis={"side": "right"}))
            from scipy import stats as _stats
            ordered = np.sort(daily)
            theory = _stats.norm.ppf((np.arange(1, len(ordered) + 1) - 0.5) / len(ordered)) * ordered.std() + ordered.mean()
            attempt("model.information.qq", lambda: G.figure("model.information.qq", [
                {"type": "scatter", "mode": "markers", "x": [float(v) for v in theory[::max(1, len(theory) // 200)]], "y": [float(v) for v in ordered[::max(1, len(ordered) // 200)]], "name": "Observed", "marker": {"size": 4}, "meta": {"role": "main", "key": "observed"}},
                G.line([float(theory[0]), float(theory[-1])], [float(theory[0]), float(theory[-1])], "Normal", "benchmark", "normal")], yaxis={"side": "right"}))
        attempt("model.information.decay", lambda: _decay_figure(sections["information"]["decay"], self.days, self.run.get("number")))
        if sections["information"]["industry"]:
            # A bar a sector, as the draft's: the sector's full name, then its Rank IC and how many names it has.
            rows_ = sections["information"]["industry"]
            attempt("model.information.industry", lambda: G.figure("model.information.industry", [{
                "type": "bar", "orientation": "h", "y": [row["industry"] for row in rows_], "x": [None if row["rankIc"] is None else round(float(row["rankIc"]), 6) for row in rows_],
                "customdata": [row["names"] for row in rows_], "name": "Rank IC", "meta": {"role": "group", "key": "industry"},
                "hovertemplate": "%{y}<br>Rank IC %{x:+.4f}<br>Names %{customdata}<extra></extra>"}],
                hovermode="closest", xaxis={"tickformat": ".3f"}, yaxis={"autorange": "reversed", "type": "category"}))
        else:
            self.missing["model.information.industry"] = "Market gives no industry for these names"

        # Turnover
        ranked = D._rank(self.pred)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            auto = M.row_correlation(ranked[1:], ranked[:-1], 3) if len(ranked) > 2 else np.array([np.nan])
        sections["turnover"] = {"autocorr": M.mean(auto), "groups": [{"group": g + 1, "turnover": M.mean(M.replaced_share(self.groups == g + 1, self.days))} for g in range(GROUPS)],
                                "holding": self.days}

        # Qlib's own signal figures
        try:
            native, missing = G.qlib_graphs(self._pred_label(), GROUPS)
            native.pop("model.returns.groups", None)  # drawn above: relative to all names, net long–short in white
            native.pop("model.information.distribution", None)  # drawn above: each bar reads out its interval
            missing.pop("model.information.distribution", None)
            figures.update(native)
            self.missing.update(missing)
        except Exception as error:  # noqa: BLE001
            for key in ("model.returns.groups", "model.information.ic", "model.turnover.autocorr", "model.turnover.top-bottom"):
                self.missing[key] = f"Qlib could not draw it: {type(error).__name__}: {str(error)[:200]}"
        # Where Qlib's turnover figures did not come out, the draft's own from the same numbers: the top and bottom groups' turnover at each rebalance, the rank autocorrelation each day.
        if "model.turnover.top-bottom" not in figures:
            rebalance = np.arange(self.days, len(dates), max(self.days, 1))
            share = {g: M.replaced_share(self.groups == g, self.days) for g in (GROUPS, 1)}
            attempt("model.turnover.top-bottom", lambda: G.figure("model.turnover.top-bottom", [
                    G.line(_iso(dates[rebalance]), share[GROUPS][rebalance], f"Top group (Q{GROUPS})", "main", "top", hovertemplate=f"Top group (Q{GROUPS}) %{{y:.1%}}<extra></extra>"),
                    G.line(_iso(dates[rebalance]), share[1][rebalance], "Bottom group (Q1)", "benchmark", "bottom", hovertemplate="Bottom group (Q1) %{y:.1%}<extra></extra>")],
                    yaxis={"tickformat": ".0%", "side": "right"}, xaxis={"type": "date"}, hovermode="x unified"))
        if "model.turnover.autocorr" not in figures and len(ranked) > 2:
            attempt("model.turnover.autocorr", lambda: G.figure("model.turnover.autocorr", [
                    G.line(_iso(dates[1:]), auto, "Autocorrelation", "main", "autocorr", hovertemplate="Autocorrelation %{y:.3f}<extra></extra>")],
                    yaxis={"side": "right"}, xaxis={"type": "date"}, hovermode="x unified"))
        for key, own in (("model.information.ic", G._own_ic), ("model.information.monthly", G._own_monthly)):
            if key not in figures:
                attempt(key, lambda own=own, key=key: own(self._pred_label(), key))
        for key in ("model.turnover.top-bottom", "model.turnover.autocorr", "model.information.ic", "model.information.monthly"):
            if key in figures:
                self.missing.pop(key, None)

        # Inputs
        sections["inputs"] = {"baselines": self.base, "importance": self._importance_table(), "styles": self._styles(), "industry": self._industry_tilt(),
                              "library": self._library(library)}
        sections["inputs"]["destyled"] = sections["inputs"]["styles"].pop("destyled", None)
        sections["inputs"]["table"] = self._inputs_table()
        attempt("model.inputs.baselines", lambda: G.figure("model.inputs.baselines", [
            G.line(_iso(dates), _cum(self.ic), "This model", "main", "model"),
            G.line(_iso(dates), _cum(self.best_series), "Strongest single input", "benchmark", "best-input"),
            G.line(_iso(dates), _cum(self.equal_series), "Inputs ranked and averaged", "benchmark", "equal-weight", line={"dash": "dot"}),
            *([G.line(_iso(dates), _cum(daily_ic(self.ridge, self.label)), "Ridge each window", "benchmark", "ridge", line={"dash": "dash"})] if self.ridge is not None else [])],
            yaxis={"side": "right", "title": {"text": i18n.t("Cumulative rank IC")}}, xaxis={"type": "date"}))
        measured = [row for row in sections["inputs"]["styles"]["exposure"] if isinstance(row["rho"], (int, float)) and np.isfinite(row["rho"])]
        if sections["inputs"]["styles"]["exposure"] and not measured:
            # a day's rank correlation needs 10 names with a prediction; with fewer every style is empty and the chart would be bare axes
            self.missing["model.inputs.styles"] = "Too few names a day to measure (a day needs 10)"
        if measured:
            attempt("model.inputs.styles", lambda: G.figure("model.inputs.styles", [
                G.bars([row["style"] for row in sections["inputs"]["styles"]["exposure"]], [row["rho"] for row in sections["inputs"]["styles"]["exposure"]],
                       "Rank correlation with the style", "group", "styles")], yaxis={"tickformat": ".2f", "side": "right"}))
        if sections["inputs"]["industry"]:
            attempt("model.inputs.industry", lambda: G.figure("model.inputs.industry", [
                G.bars([row["industry"] for row in sections["inputs"]["industry"]], [row["percentile"] for row in sections["inputs"]["industry"]],
                       "Mean prediction percentile less 50", "updown", "tilt")], yaxis={"tickformat": "+.1f", "side": "right"}))
        else:
            self.missing["model.inputs.industry"] = "Market gives no industry for these names"

        # Robustness
        years = self._years()
        sections["robustness"] = {"years": years, "bySize": self._terciles("size"), "byLiquidity": self._terciles("amount"), "coverage": self._coverage(),
                                  "heldOut": None}
        attempt("model.robustness.years", lambda: G.figure("model.robustness.years", [
            G.bars([str(row["year"]) for row in years], [row["rankIc"] for row in years], "Rank IC", "main", "rank-ic")], yaxis={"tickformat": ".3f", "side": "right"},
            xaxis={"type": "category"}))
        attempt("model.robustness.size", lambda: G.figure("model.robustness.size", [
            G.bars([row["part"] for row in sections["robustness"]["bySize"]], [row["rankIc"] for row in sections["robustness"]["bySize"]], "By size", "group", "size"),
            G.bars([row["part"] for row in sections["robustness"]["byLiquidity"]], [row["rankIc"] for row in sections["robustness"]["byLiquidity"]], "By liquidity", "group",
                   "liquidity")], barmode="group", yaxis={"tickformat": ".3f", "side": "right"}))
        week = pd.Series(np.arange(len(dates))).groupby(pd.DatetimeIndex(dates).to_period("W")).last().to_numpy()
        attempt("model.robustness.coverage", lambda: G.figure("model.robustness.coverage", [
            G.line(_iso(dates[week]), self.data.rows.sum(axis=1)[week], "In the index", "benchmark", "index"),
            G.line(_iso(dates[week]), np.isfinite(self.pred).sum(axis=1)[week], "With a prediction", "main", "predicted")], yaxis={"side": "right"}, xaxis={"type": "date"}))
        # B20: the three figures the draft had that the report did not yet draw
        library_rows = sections["inputs"]["library"]
        if library_rows:
            attempt("model.inputs.library", lambda: G.figure("model.inputs.library", [
                G.bars([row["name"] for row in library_rows], [row["rho"] for row in library_rows], "Rank correlation with the library factor", "group", "library")],
                yaxis={"tickformat": ".2f", "side": "right"}))
        else:
            self.missing["model.inputs.library"] = "The library has no factor on these names to compare with"
        residual_ic = getattr(self, "destyled_series", None)
        if residual_ic is not None:
            attempt("model.inputs.destyled", lambda: G.figure("model.inputs.destyled", [
                G.line(_iso(dates), _rolling(self.ic, 63), "Before, 3-month mean", "benchmark", "before"), G.line(_iso(dates), _rolling(residual_ic, 63), "After, 3-month mean", "main", "after")],
                yaxis={"tickformat": ".3f", "side": "right"}, xaxis={"type": "date"}))
        self.missing["model.robustness.held-out"] = "Not validated on the held-out period yet"
        _hover(figures)
        sections["missing"] = [{"key": key, "reason": reason} for key, reason in sorted(self.missing.items()) if key not in figures]
        return sections, figures

    # ------------------------------------------------------------------------------------------------------------ parts
    def _pred_label(self):
        t, i = np.nonzero(np.isfinite(self.pred) & np.isfinite(self.label))
        index = pd.MultiIndex.from_arrays([pd.Index(np.asarray(self.panel.columns, dtype=object)[i]), self.panel.dates[t].tz_localize(None)], names=["instrument", "datetime"])
        frame = pd.DataFrame({"score": self.pred[t, i], "label": self.label[t, i]}, index=index).sort_index()
        return frame

    def _information_row(self, name, series, t):
        valid = np.asarray(series, dtype=float)[np.isfinite(series)]
        return {"name": name, "mean": float(valid.mean()) if len(valid) else None, "std": float(valid.std(ddof=1)) if len(valid) > 1 else None, "ir": _ir(valid),
                "t": t, "p": None if t is None else math.erfc(abs(t) / math.sqrt(2)), "skew": _moment(valid, 3), "kurtosis": _moment(valid, 4),
                "positive": float((valid > 0).mean()) if len(valid) else None}

    def _universe_return(self):
        returns = np.nan_to_num(OpenToOpen(self.panel).derived("returns"))
        held = W._held_fast(W.equal_weights(np.isfinite(self.pred)), self.days, 1)
        out = (held * returns).sum(axis=1)
        out[~held.any(axis=1)] = np.nan
        return out

    def _rebalance(self, days):
        groups = W.group_of(self.pred, {"grouping": {"method": "equal", "count": GROUPS, "withinIndustry": False}})
        settings = {"holding": int(days), "delay": 1, "costBps": self.cost, "borrowPct": M.DEFAULT_BORROW_PCT}
        found = W.returns_of(OpenToOpen(self.panel), groups, GROUPS, settings, self.pred)
        gross = M.annual_mean(found["gross"], self.A)
        net = M.annual_mean(found["net"], self.A)
        return {"days": days, "lsNet": net, "gross": gross, "costs": None if gross is None or net is None else gross - net, "turnover": W.turnover_of(groups, GROUPS, days),
                "sharpe": M.sharpe(found["net"], self.A), "breakeven": M.break_even_bps(found["gross"], found["traded"])}

    def _top_n(self):
        pred = self.pred
        count = np.sum(np.isfinite(pred), axis=1)
        n = max(1, min(self.top_n, int(np.median(count[count > 0]) // 2) if (count > 0).any() else 1))
        ranks = M.rank_rows(pred)
        top = ranks > (count - n)[:, None]
        bottom = ranks <= n
        returns = np.nan_to_num(OpenToOpen(self.panel).derived("returns"))
        held_top = W._held_fast(W.equal_weights(top), self.days, 1)
        held_all = W._held_fast(W.equal_weights(np.isfinite(pred)), self.days, 1)
        excess = (held_top * returns).sum(axis=1) - (held_all * returns).sum(axis=1)
        excess[~held_all.any(axis=1)] = np.nan
        later = D._pct_rank(self.label)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            top_rank = np.nanmean(np.where(top, later, np.nan), axis=1)
            bottom_rank = np.nanmean(np.where(bottom, later, np.nan), axis=1)
        replaced = np.zeros(len(top))
        replaced[1:] = (top[1:] & ~top[:-1]).sum(axis=1) / max(n, 1)
        return {"excess": excess, "topRank": top_rank, "bottomRank": bottom_rank, "n": n, "turnover": replaced / max(self.days, 1)}

    def _decay(self):
        out = []
        for days in range(1, 21):
            label = np.where(self.data.rows, D.label_of(self.panel, days), np.nan)
            out.append({"days": days, "rankIc": M.mean(daily_ic(self.pred, label))})
        return out

    def _industry_ic(self):
        if self.panel.industry is None:
            return []
        codes, names = self.panel.industry_codes()
        out = []
        for code, name in enumerate(names):
            inside = codes == code  # each name's industry that day
            count = int(inside.any(axis=0).sum())
            if count < 5:
                continue
            value = M.mean(M.rank_correlation(np.where(inside, self.pred, np.nan), np.where(inside, self.label, np.nan), 3))
            out.append({"industry": str(name), "names": count, "rankIc": value})
        return sorted(out, key=lambda row: -(row["rankIc"] or -9))

    def _inputs_table(self):
        """Every input feature by its importance: where it comes from, its share, its own rank IC on the test periods (signed as the training periods chose) and t, and how often it was missing."""
        if not self.importance:
            return []
        features = self.data.features
        mean = {name: float(np.mean([entry.get(name, 0.0) for entry in self.importance])) for name in features}
        ics = self.feature_ics()
        source = {}
        for item in self.data.inputs:
            for name in item.features:
                source[name] = item.name if item.source != "builtin" else item.name
        test_days = np.zeros(len(self.panel.labels), dtype=bool)
        for _train, (first, last) in self.spans():
            test_days[first:last + 1] = True
        rows = []
        for f, name in enumerate(features):
            train_mean = M.mean(ics[:, f][~test_days]) or 0.0
            sign = 1.0 if train_mean >= 0 else -1.0
            own = ics[test_days, f] * sign
            valid = own[np.isfinite(own)]
            missing = float(np.isnan(self.data.coverage_raw[:, :, f])[self.data.rows].mean()) if self.data.rows.any() else None
            rows.append({"feature": name, "source": source.get(name, ""), "share": mean[name], "split": None, "ownIc": float(valid.mean()) if len(valid) else None,
                         "t": M.newey_west_t(own, max(self.days - 1, 0)) if len(valid) > 5 else None, "missing": missing})
        return sorted(rows, key=lambda row: -row["share"])

    def _importance_table(self):
        if not self.importance:
            return []
        features = self.data.features
        mean = {name: float(np.mean([entry.get(name, 0.0) for entry in self.importance])) for name in features}
        spread = {name: float(np.std([entry.get(name, 0.0) for entry in self.importance])) for name in features}
        by_input = []
        for item in self.data.inputs:
            share = sum(mean.get(name, 0.0) for name in item.features)
            by_input.append({"input": item.name, "features": len(item.features), "share": share})
        rows = sorted(({"feature": name, "share": mean[name], "spread": spread[name]} for name in features), key=lambda row: -row["share"])
        return {"features": rows[:60], "inputs": sorted(by_input, key=lambda row: -row["share"]), "basis": self._importance_basis()}

    def _importance_basis(self):
        algorithm = (self.windows[0].get("algorithm") if self.windows else None) or ""
        return {"lightgbm": "split gain", "xgboost": "total gain", "catboost": "prediction values change", "double_ensemble": "split gain over sub-models"}.get(
            algorithm, "|coefficient| × spread of the input")

    def _styles(self):
        found = D.styles(self.panel)
        exposure = []
        for name in D.STYLE_NAMES:
            values = found.get(f"Styles.{name}")
            rho = None if values is None else M.mean(M.rank_correlation(self.pred, np.where(np.isfinite(self.pred), values, np.nan), 10))
            exposure.append({"style": name.replace("_", " "), "rho": rho})
        residual = np.full(self.pred.shape, np.nan)
        stack = np.stack([found[f"Styles.{name}"] for name in D.STYLE_NAMES], axis=2)
        for t in np.nonzero(np.isfinite(self.pred).any(axis=1))[0]:
            y = self.pred[t]
            x = stack[t]
            ok = np.isfinite(y) & np.isfinite(x).all(axis=1)
            if ok.sum() < len(D.STYLE_NAMES) + 5:
                continue
            design = np.hstack([np.ones((ok.sum(), 1)), (x[ok] - x[ok].mean(axis=0)) / np.where(x[ok].std(axis=0) > 0, x[ok].std(axis=0), 1)])
            beta, *_ = np.linalg.lstsq(design, y[ok], rcond=None)
            residual[t, ok] = y[ok] - design @ beta
        residual_ic = daily_ic(residual, self.label)
        self.destyled_series = residual_ic
        return {"exposure": exposure, "version": "price styles v1", "destyled": {"rankIc": M.mean(residual_ic), "of": M.mean(self.ic)}}

    def _industry_tilt(self):
        if self.panel.industry is None:
            return []
        codes, names = self.panel.industry_codes("No industry")
        top = self.groups == GROUPS
        valued = np.isfinite(self.pred)
        ranks = D._pct_rank(self.pred)
        out = []
        days = valued.any(axis=1)
        for code, name in enumerate(names):
            inside = codes == code  # each name's industry that day
            with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                in_top = (top & inside)[days].sum(axis=1) / np.maximum(top[days].sum(axis=1), 1)
                in_all = (valued & inside)[days].sum(axis=1) / np.maximum(valued[days].sum(axis=1), 1)
                percentile = M.mean(np.nanmean(np.where(inside, ranks, np.nan), axis=1) * 100 - 50)
            out.append({"industry": str(name), "tilt": M.mean(in_top - in_all), "share": M.mean(in_all), "percentile": percentile})
        return sorted(out, key=lambda row: -(row["tilt"] or 0))

    def _library(self, library):
        out = []
        for name, item in (library or self.panel.library or {}).items():
            rho = M.mean(M.rank_correlation(self.pred, np.where(np.isfinite(self.pred), item["values"], np.nan), 10))
            if rho is not None:
                out.append({"name": name, "version": item.get("version", ""), "rho": rho})
        return sorted(out, key=lambda row: -abs(row["rho"]))[:10]

    def _years(self):
        out = []
        for row in M.signal_years(self.panel.dates, self.ic, self.returns["net"], self.A, self.days):
            in_year = self.panel.dates.year == row["year"]
            out.append({"year": row["year"], "whole": row["whole"], "rankIc": row["rankIc"], "days": row["days"], "t": row["t"],
                        "lsNet": row["longShortNet"], "partial": not row["whole"],
                        "sharpe": self._year_sharpe(in_year)})
        return out

    def _year_sharpe(self, in_year):
        """A year's Sharpe of the long-short net, by the whole run's definition (metrics.sharpe; no rate off a book that pays for itself), from six bars on."""
        series = np.asarray(self.returns["net"], dtype=float)[in_year]
        return M.sharpe(series, self.A) if np.sum(np.isfinite(series)) > 5 else None

    def _terciles(self, field):
        values = self.panel.derived("size" if field == "size" else "amount")
        if field == "amount":
            values = pd.DataFrame(values).rolling(20, min_periods=5).mean().to_numpy()
        ranks = D._pct_rank(np.where(np.isfinite(self.pred), values, np.nan))
        out = []
        for part, (low, high) in (("small" if field == "size" else "least traded", (0, 1 / 3)), ("middle", (1 / 3, 2 / 3)), ("large" if field == "size" else "most traded", (2 / 3, 1.01))):
            inside = (ranks >= low) & (ranks < high)
            out.append({"part": part, "rankIc": M.mean(M.rank_correlation(np.where(inside, self.pred, np.nan), np.where(inside, self.label, np.nan), 3))})
        return out

    def _coverage(self):
        valued = np.isfinite(self.pred).sum(axis=1)
        members = self.data.rows.sum(axis=1)
        days = valued > 0
        if not days.any():
            return {"min": 0, "median": 0, "max": 0, "share": None}
        return {"min": int(valued[days].min()), "median": int(np.median(valued[days])), "max": int(valued[days].max()),
                "share": float(np.sum(valued[days]) / max(np.sum(members[days]), 1))}


# ---------------------------------------------------------------------------------------------------------------- hover texts
# What each figure reads out when the pointer is over it (the draft's own): a sign where the value has one, none where it has not, the unit once.
HOVER = {
    "model.windows.each": "+.4f", "model.windows.valid-test": "+.4f", "model.windows.learning": "+.4f",
    "model.returns.groups": "+.1%", "model.returns.ls-cumulative": "+.1%", "model.returns.group-mean": "+.2f", "model.returns.top-excess": "+.1%", "model.returns.ls-drawdown": "+.1%",
    "model.returns.top-rank": ".1%", "model.returns.net-by-cost": "+.2%", "model.returns.portfolio": "+.2%",
    "model.information.ic": "+.3f", "model.information.monthly": "+.4f", "model.information.decay": "+.4f", "model.information.industry": "+.4f",
    "model.turnover.top-bottom": ".1%", "model.turnover.autocorr": ".3f",
    "model.inputs.baselines": "+.3f", "model.inputs.library": "+.3f", "model.inputs.styles": "+.3f", "model.inputs.industry": "+.1f", "model.inputs.destyled": "+.4f",
    "model.robustness.years": "+.4f", "model.robustness.size": "+.3f", "model.robustness.coverage": "d", "model.robustness.held-out": "+.1%",
}


def _hover(figures):
    """A read-out for every trace that has none: the trace's name and its value in the figure's format; lines share the pointer's vertical (x unified)."""
    for key, figure in figures.items():
        form = HOVER.get(key)
        if form is None:
            continue
        for trace in figure.get("data", []):
            if trace.get("hovertemplate") or trace.get("type") == "heatmap":
                continue
            axis = "x" if trace.get("orientation") == "h" else "y"
            trace["hovertemplate"] = f"{trace.get('name') or ''} %{{{axis}:{form}}}<extra></extra>".strip()
        figure.setdefault("layout", {}).setdefault("hovermode", "x unified")


# ---------------------------------------------------------------------------------------------------------------- helpers
def _rank_among(top, importance):
    """For each feature shown and each window, its place (1 = largest share) among the features shown in that window."""
    out = [[1] * len(importance) for _ in top]
    for column, entry in enumerate(importance):
        order = sorted(range(len(top)), key=lambda row: -entry.get(top[row], 0.0))
        for place, row in enumerate(order, start=1):
            out[row][column] = place
    return out


def _diagonal(windows):
    values = [v for w in windows for v in (w.get("validIc"), w.get("testIc")) if v is not None]
    low, high = (min(values), max(values)) if values else (0, 0)
    return [low, high], [low, high]


def _top_features(importance, count):
    total = {}
    for entry in importance:
        for name, value in entry.items():
            total[name] = total.get(name, 0.0) + value
    return [name for name, _ in sorted(total.items(), key=lambda item: -item[1])[:count]]


def _learning(curves, windows=None):
    """The validation loss by round of the last four windows, each against its first round; a dot marks the round each stopped at."""
    chosen = [(index, curve) for index, curve in enumerate(curves) if curve and curve.get("valid")][-4:]
    traces = []
    for place, (index, curve) in enumerate(chosen):
        valid = np.asarray(curve["valid"], dtype=float)
        start = valid[0] if valid[0] else 1.0
        rounds = list(range(1, len(valid) + 1))
        relative = valid / start
        traces.append({**G.line(rounds, relative, f"Window {index + 1}", "group", f"window:{index + 1}"), "meta": {"role": "group", "key": f"window:{index + 1}", "index": place, "of": len(chosen)}})
        best = (windows[index].get("best") if windows and index < len(windows) else None) or len(valid)
        best = int(min(max(best, 1), len(valid)))
        traces.append({"type": "scatter", "mode": "markers", "x": [best], "y": [float(relative[best - 1])], "name": f"Window {index + 1} stopped", "showlegend": False,
                       "marker": {"size": 6}, "meta": {"role": "group", "key": f"stop:{index + 1}", "index": place, "of": len(chosen)}})
    return G.figure("model.windows.learning", traces, xaxis={"title": {"text": i18n.t("Epoch") if any(c.get("unit") == "epoch" for c in curves if c) else i18n.t("Round")}}, yaxis={"title": {"text": i18n.t("Validation loss, against round 1")}, "side": "right"})


def _alpha_beta(series, base, annualization):
    ok = np.isfinite(series) & np.isfinite(base)
    if ok.sum() < 10 or np.var(base[ok]) == 0:
        return None, None
    beta = float(np.cov(series[ok], base[ok])[0, 1] / np.var(base[ok], ddof=1))
    alpha = float(np.mean(series[ok] - beta * base[ok]) * annualization)
    return alpha, beta


def _risk_table(series, annualization):
    """Qlib's risk analysis of each daily series (mean, std, annualized return, information ratio, max drawdown)."""
    out = {}
    try:
        from qlib.contrib.evaluate import risk_analysis
        for name, values in series.items():
            frame = pd.Series(values).dropna()
            if len(frame) < 3:
                continue
            found = risk_analysis(frame, N=int(round(annualization)), freq=None)
            out[name] = {key: M.finite(found.loc[key].iloc[0]) for key in found.index}
    except Exception as error:  # noqa: BLE001 - the table says why it is missing
        out["unavailable"] = f"Qlib's risk analysis failed: {str(error)[:160] or type(error).__name__}"
    return {"risk": out}


def _drawdown(per_bar):
    path = np.cumprod(1 + np.nan_to_num(np.asarray(per_bar, dtype=float)))
    return M.drawdown_path(path, 1.0)


def _rolling(series, window):
    return pd.Series(series).rolling(window, min_periods=max(1, window // 2)).mean().to_numpy()


def _cum(series):
    """The running total of a daily series, drawn from its first value (days before it have none)."""
    values = np.asarray(series, dtype=float)
    out = np.nancumsum(np.nan_to_num(values))
    first = np.argmax(np.isfinite(values)) if np.isfinite(values).any() else len(values)
    out[:first] = np.nan
    return out


def _ir(series):
    series = np.asarray(series, dtype=float)
    series = series[np.isfinite(series)]
    return float(series.mean() / series.std(ddof=1)) if len(series) > 2 and series.std(ddof=1) > 0 else None


def _moment(series, order):
    series = np.asarray(series, dtype=float)
    if len(series) < 4 or series.std() == 0:
        return None
    centred = (series - series.mean()) / series.std()
    value = float(np.mean(centred ** order))
    return value - 3 if order == 4 else value


def _decay_figure(decay, days, number=None):
    """Rank IC against the return over 1 to 20 days, the label's own length marked; it reads out as the draft's: "Return over 5D", then the run ("#12")."""
    x = [f"{row['days']}D" for row in decay]
    name = f"#{number}" if number else "This run"
    read = f"Return over %{{x}}<br>{name} %{{y:+.4f}}<extra></extra>"
    rest = [None if row["days"] == days else row["rankIc"] for row in decay]
    chosen = [row["rankIc"] if row["days"] == days else None for row in decay]
    return G.figure("model.information.decay", [G.bars(x, [np.nan if v is None else v for v in rest], name, "group", "decay", hovertemplate=read),
                                                G.bars(x, [np.nan if v is None else v for v in chosen], name, "main", "decay:label", hovertemplate=read)],
                    barmode="overlay", hovermode="closest", yaxis={"tickformat": ".3f", "side": "right"}, xaxis={"type": "category"})
