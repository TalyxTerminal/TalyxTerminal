"""A strategy's checks: held-out Sharpe, walk-forward efficiency, the neighbours of a sweep cell and whether it is on the grid's edge, costs doubled,
shuffled trade order, the best five trades and the deflated Sharpe. Each is a row `{id, name, value, need, state, title, go}`; the page reads them
with the run (`strategy_views.run`). They use in-sample numbers only: a run that stops before the held-out start, sweeps and walk-forward that
stop there too, and the held-out run is read only by the held-out row.

The grid helpers (`neighbours`, `edges`, `cell`) are plain functions over a sweep's axes and cases, so a test can give them a hand-written matrix."""
from __future__ import annotations

import itertools
import json
import math

import numpy as np

from ..native import metrics as M

# The numbers the checks read from a combination's headline numbers.
SHARPE = "sharpe"
# The axes of a rerun that keeps the strategy as it is (each year apart, costs scaled, more capital): its cells are neither tries nor neighbouring values.
RERUN_AXES = ("@period", "@costMultiple", "@capital")


def traded_sharpe(window):
    """A walk-forward window's out-of-sample Sharpe: 0 when it placed no trade (a flat line has none to compute), None when it is not known."""
    return window["sharpeTraded"] if window.get("sharpeTraded") is not None else 0.0 if window.get("trades") == 0 else None


def walk_efficiency(windows):
    """Mean Sharpe when traded over mean Sharpe when picked (out of sample over in sample), None when there is nothing to divide by.
    A window that placed no trade counts as Sharpe 0: it made nothing out of sample (left out, a walk passed on the one window of ten that traded)."""
    picked = [item["sharpePicked"] for item in windows if item["sharpePicked"] is not None]
    traded = [traded_sharpe(item) for item in windows if traded_sharpe(item) is not None]
    if not picked or not traded or sum(picked) <= 0:
        return None
    return (sum(traded) / len(traded)) / (sum(picked) / len(picked))


def walk_outcome(windows):
    """The walk-forward check on its windows: (state, efficiency, mean Sharpe the values were picked on). Picked on a Sharpe at or below zero,
    there is no efficiency to divide out, and the walk fails: nothing out of sample can earn back what the picking never had."""
    efficiency = walk_efficiency(windows)
    picked = [item["sharpePicked"] for item in windows if item["sharpePicked"] is not None]
    mean = sum(picked) / len(picked) if picked else None
    if efficiency is not None:
        return {"ok": "pass", "watch": "fail"}.get(M.strategy_check("walk", efficiency), "notrun"), efficiency, mean
    traded = any(traded_sharpe(item) is not None for item in windows)
    return ("fail" if mean is not None and mean <= 0 and traded else "notrun"), None, mean


def rerun(record):
    """Whether a sweep only reran the strategy as it is (the years, the cost multiples, the capital steps)."""
    axes = (record.get("sweep") or {}).get("axes") or []
    return bool(axes) and all(axis["name"] in RERUN_AXES for axis in axes)


# ---------------------------------------------------------------------------------------------------------------- the grid
def position(axis, value):
    """Where `value` sits among an axis's values, or None (a value outside what was swept)."""
    for number, item in enumerate(axis["values"]):
        if item == value:
            return number
    return None


def cell(axes, cases, values):
    """The combination whose value on every axis is the one given (`values` maps axis name to value), or None."""
    for case in cases:
        if all(case["params"].get(axis["name"]) == values.get(axis["name"]) for axis in axes):
            return case
    return None


def neighbours(axes, cases, index, key=SHARPE):
    """The cells one step away from combination `index` along any of the axes, diagonals included (a centre of a 2-D grid has 8, a corner 3, a cell
    of a one-axis sweep at most 2): how many the grid has, and the `key` number of those that completed with one."""
    own = next(case for case in cases if case["index"] == index)
    places = [position(axis, own["params"].get(axis["name"])) for axis in axes]
    if any(place is None for place in places):
        return {"total": 0, "ran": 0, "values": []}
    steps = [[place + shift for shift in (-1, 0, 1) if 0 <= place + shift < len(axis["values"])] for axis, place in zip(axes, places, strict=True)]
    by_place = {}
    for case in cases:
        found = tuple(position(axis, case["params"].get(axis["name"])) for axis in axes)
        by_place[found] = case
    values, total = [], 0
    for around in itertools.product(*steps):
        if list(around) == places:
            continue
        near = by_place.get(around)
        if near is None:
            continue
        total += 1
        number = (near.get("metrics") or {}).get(key)
        if near["state"] == "completed" and isinstance(number, (int, float)) and math.isfinite(number):
            values.append(float(number))
    return {"total": total, "ran": len(values), "values": values}


def edges(axes, params):
    """The axes on which `params` holds the smallest or the largest value the sweep tried: `[{name, value, end, low, high}]`. An axis with one value
    has no edge; a swept period (calendar years) has none either."""
    out = []
    for axis in axes:
        values = axis["values"]
        if len(values) < 2 or axis["name"] == "@period" or axis.get("type") in ("bool", "choice"):  # a switch or a choice has no beyond
            continue
        place = position(axis, params.get(axis["name"]))
        if place is None or place not in (0, len(values) - 1):
            continue
        out.append({"name": axis["name"], "value": values[place], "end": "smallest" if place == 0 else "largest", "low": values[0], "high": values[-1],
                    **({"label": axis["label"]} if axis.get("label") else {})})
    return out


def neighbour_ratio(axes, cases, index):
    """Mean Sharpe of the neighbours over the cell's own, with the counts for the line under it; None for the ratio when the cell's Sharpe is not above zero
    (a share of a negative number says nothing) or no neighbour completed."""
    own = next(case for case in cases if case["index"] == index)
    found = neighbours(axes, cases, index)
    sharpe = (own.get("metrics") or {}).get(SHARPE)
    mean = sum(found["values"]) / len(found["values"]) if found["values"] else None
    ratio = mean / sharpe if mean is not None and isinstance(sharpe, (int, float)) and sharpe > 0 else None
    return {**found, "sharpe": sharpe, "mean": mean, "ratio": ratio}


def deflated(inputs, sharpes, tries, annualization):
    """The deflated Sharpe of a run's own per-period Sharpe, skew and kurtosis (`work.sharpeInputs`) after `tries` runs: the spread of the Sharpes the
    runs got (their annualized Sharpe over the square root of the bars a year, as a variance) sets how lucky the best of them is expected to be;
    with fewer than two Sharpes it is 1 / observations, one estimate's own variance."""
    if not inputs or inputs.get("perPeriod") is None:
        return None
    each = [number / math.sqrt(annualization) for number in sharpes if number is not None and math.isfinite(number)]
    variance = float(np.var(each, ddof=1)) if len(each) >= 2 else None
    return M.deflated_sharpe(inputs["perPeriod"], inputs["observations"], inputs["skew"], inputs["kurtosis"], tries, variance)


# ---------------------------------------------------------------------------------------------------------------- the rows
def fx(value, digits=2):
    return "–" if value is None else f"{value:.{digits}f}"


def pct(value, digits=0):
    return "–" if value is None else f"{value * 100:.{digits}f}%"


def number(value, unit=""):
    return f"{value:.2f}{unit}"


class StrategyChecks:
    def __init__(self, runs, views=None):
        self.runs = runs
        self.views = views

    # --- the other runs of the object ------------------------------------------------------------------------------------------
    def records(self, object_id, kind):
        rows = self.runs.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=? AND json_extract(content,'$.runKind')=? "
                                            "ORDER BY json_extract(content,'$.createdAt') DESC, id DESC", (object_id, kind)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def number(self, record):
        return self.views.number(record) if self.views else None

    def tries(self, object_id):
        """How many runs the object has been tried with: its completed backtests, the combinations that completed in its sweeps and walk-forward runs
        (a held-out validation is a look, not a try; a walk-forward's window runs and the case runs of a sweep are counted through their sweep; the years, cost and
        capital reruns try nothing new, and counting them made each robustness check lower the deflated Sharpe)."""
        rows = self.runs.records.db.execute("SELECT content FROM records WHERE kind='qrun' AND json_extract(content,'$.objectId')=?", (object_id,)).fetchall()
        # One combination is one try however many times it ran: the same code, values and data (a sweep and a walk-forward over the same
        # grid, a run that is one of the grid's cells) count once.
        seen, sharpes = set(), []

        def tried(record, params, sharpe):
            data = record.get("data") or {}
            key = json.dumps([(record.get("code") or {}).get("digest"), params, {name: data.get(name) for name in ("symbols", "index", "list", "timeframe", "since", "until")}],
                             sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                sharpes.append(sharpe)
        for record in (json.loads(row[0]) for row in rows):
            if record["state"] != "completed":
                continue
            if record["runKind"] == "run":
                tried(record, record.get("params") or {}, (record.get("stats") or record.get("metrics") or {}).get(SHARPE))
            elif record["runKind"] in ("sweep", "walk") and not rerun(record):
                for item in (record.get("sweep") or {}).get("cases", []):
                    if item["state"] == "completed":
                        tried(record, {**(record.get("params") or {}), **(item.get("params") or {})}, (item.get("metrics") or {}).get(SHARPE))
        return len(seen), sharpes

    def rows(self, record, work):
        """The checks of one run (a backtest, or the combination a sweep shows), from its own work and the object's other runs."""
        inputs = work.get("checks") or {}
        stats = work.get("stats") or {}
        sharpe = stats.get(SHARPE)
        out = []

        def add(check, state, value, need=None, title=None, go=None, detail=None):
            rule = M.STRATEGY_CHECKS[check]
            out.append({"id": check, "name": rule["name"], "value": value, "need": need or rule["need"], "state": state, "title": title or rule["meaning"], "go": go,
                        **({"detail": detail} if detail else {})})

        def judged(check, ratio):
            return {"ok": "pass", "watch": "fail"}.get(M.strategy_check(check, ratio), "notrun")

        object_id = record["objectId"]
        digest = (record.get("code") or {}).get("digest")
        half = f"≥ {fx(.5 * sharpe)}" if isinstance(sharpe, (int, float)) and sharpe > 0 else None
        # Held-out: the validation that scored this run (any time; it is not tied to code the run does not have).
        held = next((item for item in self.records(object_id, "heldout") if item.get("of") == record["id"] and item["state"] == "completed"), None)
        if held is not None:
            score = (self.views.report(held, "work") or {}).get("heldOut") if self.views else None
            seen = ((score or {}).get("heldOut") or {}).get(SHARPE)
            ratio = seen / sharpe if seen is not None and isinstance(sharpe, (int, float)) and sharpe > 0 else None
            add("heldout", judged("heldout", ratio) if ratio is not None else "notrun", fx(seen) if seen is not None else "no held-out Sharpe", half,
                f"Sharpe on the held-out period (#{self.number(held)}) against half the in-sample {fx(sharpe)}", {"kind": "open", "runId": held["id"]} if ratio is not None else None)
        else:
            add("heldout", "notrun", "Not run", half, go={"kind": "validate"})
        # Walk-forward: the newest completed one that ran this code.
        walks = [item for item in self.records(object_id, "walk") if item["state"] == "completed"]
        walk = next((item for item in walks if (item.get("code") or {}).get("digest") == digest), None)
        if walk is not None:
            info = walk.get("walk") or {}
            # From the windows, so a walk recorded before no-trade windows counted as Sharpe 0 is judged the same way.
            state, efficiency, picked = walk_outcome(info["windows"]) if info.get("windows") else (
                judged("walk", info.get("efficiency")) if info.get("efficiency") is not None else "notrun", info.get("efficiency"), None)
            value = fx(efficiency) if efficiency is not None else f"picked on Sharpe {fx(picked)}" if state == "fail" else "no efficiency"
            add("walk", state, value, title=f"#{self.number(walk)} re-picks the values each window: out-of-sample Sharpe over the Sharpe they were picked on",
                go={"kind": "open", "runId": walk["id"]} if state != "notrun" else None)
        else:
            add("walk", "notrun", "Not run" if not walks else f"Walk-forward #{self.number(walks[0])} ran older code", go={"kind": "walk"})
        # Neighbours and the edge: the newest completed sweep of this code that has this run's values as a cell. A sweep the page starts names the run it
        # started from (`of`), so that does not set it apart; the years and the cost and capital reruns do (their cells are not neighbouring values).
        sweeps = [item for item in self.records(object_id, "sweep") if item["state"] == "completed" and (item.get("sweep") or {}).get("format") == 2
                  and not any(axis["name"] in RERUN_AXES for axis in item["sweep"]["axes"])]
        same = [item for item in sweeps if (item.get("code") or {}).get("digest") == digest]
        found, grid = None, None
        for item in same:
            at = cell(item["sweep"]["axes"], item["sweep"]["cases"], self.current(record, item["sweep"]["axes"]))
            if at is not None:
                found, grid = item, at
                break
        if found is None:
            text = "Not run" if not sweeps else f"Sweep #{self.number(sweeps[0])} ran older code" if not same else "Not a cell of sweep #" + str(self.number(same[0]))
            add("neighbours", "notrun", text, go={"kind": "sweep"})
            add("edge", "notrun", text, go={"kind": "sweep"})
        else:
            axes, cases = found["sweep"]["axes"], found["sweep"]["cases"]
            near = neighbour_ratio(axes, cases, grid["index"])
            beyond = edges(axes, grid["params"])
            opened = {"kind": "open", "runId": found["id"], **({"resweep": True} if beyond else {})}
            own = near["sharpe"]
            need = f"≥ {fx(M.NEIGHBOUR_SHARE * own)}" if isinstance(own, (int, float)) and own > 0 else None
            if not isinstance(own, (int, float)) or own <= 0:
                add("neighbours", "notrun", "cell Sharpe ≤ 0", go=None)
            elif near["mean"] is None:
                add("neighbours", "notrun", "no neighbour ran", need, go=opened)
            else:
                state = "fail" if beyond else judged("neighbours", near["ratio"])
                add("neighbours", state, fx(near["mean"]), need, f"Mean Sharpe of the {near['total']} nearest values in sweep #{self.number(found)}", opened,
                    detail=f"{near['ran']} of {near['total']} ran" if near["ran"] < near["total"] else ("edge" if beyond else None))
            # The value stays short (the column is narrow and does not wrap); which values sit on the edge is in its hover.
            said = "; ".join(f"{item.get('label') or item['name']} {item['value']} is the {item['end']} value tried ({item['low']}–{item['high']})" for item in beyond)
            add("edge", "fail" if beyond else "pass", (f"{len(beyond)} values on it" if len(beyond) > 1 else f"{beyond[0]['end']} value") if beyond else "inside",
                title=f"{said} in sweep #{self.number(found)}; the best may lie beyond" if beyond else None, go=opened)
        # Costs doubled: a sweep of the cost multiple that started from this run's values.
        costs = next((item for item in self.records(object_id, "sweep") if item.get("of") == record["id"] and item["state"] == "completed" and item["sweep"].get("format") == 2
                      and [axis["name"] for axis in item["sweep"]["axes"]] == ["@costMultiple"]), None)
        doubled = base = None
        if costs is not None:
            by = {case["params"]["@costMultiple"]: (case.get("metrics") or {}).get(SHARPE) for case in costs["sweep"]["cases"] if case["state"] == "completed"}
            doubled, base = by.get(2), by.get(1)
        if costs is not None and doubled is not None and isinstance(base, (int, float)) and base > 0:
            add("costs", judged("costs", doubled / base), fx(doubled), f"≥ {fx(.5 * base)}", go={"kind": "open", "runId": costs["id"]})
        elif costs is not None and doubled is not None:
            add("costs", "notrun", "Sharpe as set ≤ 0", go=None)
        else:
            add("costs", "notrun", "Not run", half, go={"kind": "costs"})
        # Shuffled trades, best five: from the run's own trades.
        shuffle = inputs.get("shuffle") or {}
        times = shuffle["p95"] / shuffle["actual"] if shuffle.get("actual") and shuffle.get("p95") is not None else None
        if times is not None:
            add("shuffle", judged("shuffle", times), f"{times:.1f}×", go={"kind": "section", "id": "equity"})
        else:
            add("shuffle", "notrun", "fewer than 5 trades" if "reason" in shuffle else "Not run", go=None)
        top = inputs.get("top") or {}
        if top.get("value") is not None:
            add("top", judged("top", top["value"]), pct(top["value"]), go={"kind": "section", "id": "trades"})
        else:
            add("top", "notrun", "no net profit" if top else "Not run", go=None)
        # Deflated Sharpe: after every run the object has been tried with.
        tries, sharpes = self.tries(object_id)
        probability = deflated(work.get("sharpeInputs"), sharpes, tries, work.get("annualization") or 252)
        detail = "1 try" if tries == 1 else f"{tries} tries"
        if probability is not None:
            add("tries", judged("tries", probability), pct(probability), detail=detail, go={"kind": "section", "id": "summary"})
        else:
            add("tries", "notrun", "Not run", detail=detail, go=None)
        return out

    @staticmethod
    def current(record, axes):
        """The value this run has on each swept axis: a code parameter's value; for a run setting, the one it ran with (costs as set are 1×)."""
        run = ((record.get("data") or {}).get("runSettings")) or {}
        out = {}
        for axis in axes:
            name = axis["name"]
            if not name.startswith("@"):
                out[name] = (record.get("params") or {}).get(name)
            elif name == "@fill":
                out[name] = run.get("fill", "nextOpen")
            elif name == "@capital":
                out[name] = run.get("capital")
            elif name == "@costMultiple":
                out[name] = 1
        return out
