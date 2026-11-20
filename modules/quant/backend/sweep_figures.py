"""The pictures of a sweep, made on the Host from the combinations' numbers (a few hundred cells, milliseconds): the heat map, the equity of the
combination being looked at, the stitched walk-forward and the bars of a year-by-year sweep. Every trace says what it is (`meta.role`, `meta.key`);
no colour is written here: the heat map is a `group` (one neutral ramp, the size of a number; the up and down colours only mean direction)."""
from __future__ import annotations

import math
from datetime import UTC, datetime

from ..native import figures as F

# How a number a sweep ranks by reads out, as the draft reads it: Sharpe two digits, a return with its sign, a fall without one, counts whole.
FORMATS = {"sharpe": ("Sharpe", ".2f", ".2f"), "cagr": ("CAGR", "+.1%", "+.0%"), "totalReturn": ("Return", "+.0%", "+.0%"), "maxDrawdown": ("Max DD", ".0%", ".0%"),
           "trades": ("Trades", "d", "d"), "profitFactor": ("Profit factor", ".2f", ".2f"), "winRate": ("Win rate", ".0%", ".0%")}


def day(ms):
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def shown(value):
    """An axis value as its label: a whole number without a point, a period as its first day's year or days."""
    if isinstance(value, dict):
        return value["since"][:4] if value["since"][:4] == value["until"][:4] else f"{value['since']} – {value['until']}"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return (str(value) if isinstance(value, int) else f"{value:g}") if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def title_of(axes, params):
    return " · ".join(f"{axis.get('label') or axis['name']} {shown(params[axis['name']])}" for axis in axes)


def heat(cases, axes, view, default_metric, looked_at=None):
    """The ranking number over two axes (one axis is one row). With more axes the others are held at the values of the combination looked at, or
    of the best one when none is. `layout.meta` carries the axes drawn and the values held, for the picker above the picture."""
    metric = view.get("metric") or default_metric
    names = [axis["name"] for axis in axes]
    x = view.get("x") if view.get("x") in names else names[0]
    if len(names) == 1:
        y = None
    elif view.get("y") in names and view.get("y") != x:
        y = view["y"]
    else:
        y = next(name for name in names if name != x)
    by = {axis["name"]: axis for axis in axes}
    anchor = next((case for case in cases if case["index"] == looked_at), None)
    if anchor is None:
        ranked = [case for case in cases if case["state"] == "completed" and isinstance((case["metrics"] or {}).get(metric), (int, float))]
        anchor = max(ranked, key=lambda case: case["metrics"][metric], default=cases[0])
    held = {name: anchor["params"][name] for name in names if name not in (x, y)}
    rows = by[y]["values"] if y else [None]
    label, tick, cell_tick = FORMATS[metric]
    z, custom, failed_x, failed_y, failed_text = [], [], [], [], []
    for row in rows:
        line, notes = [], []
        for column in by[x]["values"]:
            wanted = {**held, x: column, **({y: row} if y else {})}
            case = next((item for item in cases if item["params"] == wanted), None)
            value = (case["metrics"] or {}).get(metric) if case and case["state"] == "completed" else None
            line.append(value)
            title = title_of([by[x], *([by[y]] if y else [])], wanted)
            if case is not None and case["state"] in ("failed", "cancelled", "interrupted") or case is not None and case["state"] == "completed" and value is None:
                reason = (case.get("message") or case["state"]).splitlines()[0] if case.get("message") or case["state"] else case["state"]
                failed_x.append(shown(column))
                failed_y.append(shown(row) if y else "")
                failed_text.append([title, f"Failed: {reason}"] if case["state"] == "failed" else [title, case["state"].capitalize()])
            notes.append([title, case["index"] if case else None])
        z.append(line)
        custom.append(notes)
    template = f"<b>%{{customdata[0]}}</b><br>{label}: %{{z:{tick}}}<extra></extra>"
    traces = [F.trace("heatmap", "group", "heat", x=[shown(item) for item in by[x]["values"]], y=[shown(item) for item in rows] if y else [""], z=z, customdata=custom,
                      hovertemplate=template, texttemplate=f"%{{z:{cell_tick}}}", showscale=False, xgap=1, ygap=1, hoverongaps=False)]
    if failed_x:
        traces.append(F.trace("scatter", "detail", "failed", mode="markers", x=failed_x, y=failed_y, customdata=failed_text, marker={"symbol": "line-ne", "size": 14, "line": {"width": 1}},
                              hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<extra></extra>"))
    layout = {"meta": {"key": "strategy.sweep.heat", "x": x, "y": y, "metric": metric, "held": {name: shown(value) for name, value in held.items()},
                       "heldValues": held, "label": label},
              "xaxis": {"title": {"text": by[x].get("label") or x}, "type": "category"}, "yaxis": {"title": {"text": (by[y].get("label") or y) if y else ""}, "type": "category", "autorange": "reversed"}}
    out = F.figure("strategy.sweep.heat", traces, **{key: value for key, value in layout.items() if key != "meta"})
    out["layout"]["meta"] = layout["meta"]
    return out


def level(points):
    base = points[0][1] if points and points[0][1] else None
    return [round(point[1] / base, 6) if base and point[1] is not None else None for point in points]


def returns_line(points, role, key, name, **more):
    values = level(points)
    gain = [None if value is None else round(value - 1, 6) for value in values]
    item = F.line([day(point[0]) for point in points], gain, role, key, name, customdata=gain, **more)
    item["hovertemplate"] = f"<b>%{{x}}</b><br>{name}: %{{customdata:+.1%}}<extra></extra>"
    return item


def preview(work, source, case, source_id, source_number):
    """The equity of the combination being looked at (and the run the sweep started from, dashed), from the day the combination began, as a return."""
    if not work or not work.get("equity"):
        return None
    traces = [returns_line(work["equity"], "main", "picked", "Picked cell")]
    if source and source.get("equity") and source_number:
        traces.insert(0, returns_line(source["equity"], "compare", "source", f"#{source_number}"))
    return F.figure("strategy.sweep.preview", traces, showlegend=True, yaxis={"tickformat": "+.0%"})


def walk(walk_record, source, source_id, source_number, number):
    """The out-of-sample equity of a walk-forward (the windows' trades joined end to end, from 1), with the run it started from dashed, and a band
    over each trading window labelled with the values picked for it."""
    stitched = walk_record["stitched"]
    traces = []
    name = f"#{number}" if number else "Walk-forward"
    main = F.line([day(at) for at, _ in stitched], [round(value - 1, 6) for _, value in stitched], "main", "stitched", name, customdata=[round(value - 1, 6) for _, value in stitched])
    main["hovertemplate"] = f"<b>%{{x}}</b><br>{name}: %{{customdata:+.1%}}<extra></extra>"
    if source and source.get("equity") and source_number:
        start, end = stitched[0][0], stitched[-1][0]
        inside = [point for point in source["equity"] if start <= point[0] <= end]
        if len(inside) > 1:
            other = returns_line(inside, "compare", "source", f"#{source_number}")
            other["name"] = f"#{source_number} fixed values"
            traces.append(other)
    traces.append(main)
    annotations = []
    for window in walk_record["windows"]:
        first, last = window["traded"]
        traces.append(F.trace("scatter", "group", f"band:{window['n']}", index=window["n"] % 2, of=2, mode="lines", x=[day(first), day(first), day(last), day(last)],
                              y=[0, 1, 1, 0], yaxis="y2", fill="toself", hoverinfo="skip", showlegend=False, line={"width": 0}))
        text = " · ".join(shown(value) for value in window["values"].values())
        annotations.append({"x": day((first + last) // 2), "y": 1, "xref": "x", "yref": "paper", "text": text, "showarrow": False, "yanchor": "bottom"})
    return F.figure("strategy.walk.stitched", traces, showlegend=True, yaxis={"tickformat": "+.0%"}, yaxis2={"overlaying": "y", "range": [0, 1], "visible": False}, annotations=annotations)


def years(cases, axes):
    """Each year on its own: the strategy's return and the benchmark's, as bars, with the years in order."""
    axis = next(item for item in axes if item["name"] == "@period")
    rows = []
    for case in sorted(cases, key=lambda item: item["params"]["@period"]["since"]):
        metrics = case["metrics"] or {}
        rows.append((shown(case["params"]["@period"]), metrics.get("totalReturn"), metrics.get("benchmarkReturn")))
    names = [row[0] for row in rows]
    traces = [F.trace("bar", "main", "strategy", name="Strategy", x=names, y=[None if row[1] is None else round(row[1] * 100, 3) for row in rows],
                      hovertemplate="<b>%{x}</b><br>Strategy: %{y:+.1f}%<extra></extra>")]
    if any(row[2] is not None for row in rows):
        traces.append(F.trace("bar", "benchmark", "benchmark", name="Benchmark", x=names, y=[None if row[2] is None else round(row[2] * 100, 3) for row in rows],
                              hovertemplate="<b>%{x}</b><br>Benchmark: %{y:+.1f}%<extra></extra>"))
    return F.figure("strategy.sweep.years", traces, showlegend=True, barmode="group", yaxis={"ticksuffix": "%"}, xaxis={"type": "category"}) if axis else None


def held_split(work, of_number, number):
    """A held-out validation's equity as one return line from the start, in-sample part and held-out part drawn apart and the held-out part shaded."""
    held = work.get("heldOut") or {}
    start, equity = held.get("from"), work.get("equity") or []
    if not start or len(equity) < 2:
        return None
    values = level(equity)
    parts = {"in": [], "out": []}
    for point, value in zip(equity, values, strict=True):
        if point[0] < start:
            parts["in"].append((point[0], value))
        else:
            if not parts["out"] and parts["in"]:
                parts["in"].append((point[0], value))  # the lines meet
            parts["out"].append((point[0], value))
    traces = []
    for part, key, name in (("in", "insample", f"#{of_number}, in-sample"), ("out", "heldout", f"#{number}, held out")):
        points = parts[part]
        if len(points) < 2:
            continue
        gain = [None if value is None else round(value - 1, 6) for _, value in points]
        line = F.line([day(at) for at, _ in points], gain, "main", key, name, customdata=gain)
        line["hovertemplate"] = f"<b>%{{x}}</b><br>{name}: %{{customdata:+.1%}}<extra></extra>"
        traces.append(line)
    last = equity[-1][0]
    traces.append(F.trace("scatter", "group", "band:held", mode="lines", x=[day(start), day(start), day(last), day(last)], y=[0, 1, 1, 0], yaxis="y2", fill="toself", hoverinfo="skip", showlegend=False, line={"width": 0}))
    return F.figure("strategy.heldout.split", traces, showlegend=True, yaxis={"tickformat": "+.0%"}, yaxis2={"overlaying": "y", "range": [0, 1], "visible": False})


def held_rows(held, bench="BTC"):
    """The side-by-side table of a held-out validation: [name, in-sample, held out] as the page writes them."""
    def text(part, key, kind):
        value = (part or {}).get(key)
        if value is None:
            return "n/a"
        # A held-out Sharpe or Sortino is often below zero: it takes the same minus sign as every other number of the table.
        return (f"{value:+.1%}".replace("-", "−") if kind == "pc" else f"{value:+.2f}".replace("-", "−") if kind == "s" else f"{value:.2f}".replace("-", "−") if kind == "n" else f"{value:.1%}" if kind == "u"
                else f"{value:.0%}".replace("-", "−") if kind == "dd" else str(int(value)))
    inside, outside = held.get("inSample"), held.get("heldOut")
    return [[name, text(inside, key, kind), text(outside, key, kind)] for name, key, kind in
            (("Return", "totalReturn", "pc"), ("CAGR", "cagr", "pc"), ("Sharpe", "sharpe", "n"), ("Sortino", "sortino", "n"), ("Max drawdown", "maxDrawdown", "dd"),
             ("Volatility", "volatility", "u"), (f"Beta to {bench}", "beta", "s"), ("Days", "days", "i"), ("Trades", "trades", "i"), ("Win rate", "winRate", "u"))]


def nice_axis(values, want=5, suffix=""):
    """A value axis whose ticks are round numbers (a step of 1, 2 or 5 times a power of ten) that bracket the data, each written with exactly the digits the step needs,
    so what a tick says is what the data is at that height. None when there is nothing to draw."""
    found = [value for value in values if value is not None]
    if not found:
        return None
    low, high = min(found), max(found)
    if low == high:
        low, high = low - (abs(low) * 0.05 or 1.0), high + (abs(high) * 0.05 or 1.0)
    span = high - low
    raw = span / max(want - 1, 1)
    power = 10 ** math.floor(math.log10(raw))
    step = next(factor * power for factor in (1, 2, 5, 10) if factor * power >= raw)
    first, last = math.floor(low / step + 1e-9), math.ceil(high / step - 1e-9)
    text = f"{step:.10g}"
    decimals = len(text.split(".")[1]) if "." in text else 0
    ticks = [round(index * step, decimals) for index in range(first, last + 1)]
    return {"range": [ticks[0], ticks[-1]], "tickvals": ticks, "ticktext": [f"{tick:.{decimals}f}{suffix}" for tick in ticks]}


def capacity(rows):
    """How the run does at larger capital (the impact rerun at each step): the CAGR at each as the main line and the Sharpe as the dashed one on the left axis, capital on a log scale."""
    ordered = sorted(rows, key=lambda row: row["capital"])
    capital = [row["capital"] for row in ordered]
    cagr = F.trace("scatter", "main", "cagr", mode="lines+markers", name="CAGR", x=capital, y=[None if row["cagr"] is None else round(row["cagr"] * 100, 3) for row in ordered],
                   marker={"size": 5}, hovertemplate="<b>Capital %{x:,.0f}</b><br>CAGR: %{y:+.1f}%<extra></extra>")
    sharpe = F.trace("scatter", "compare", "sharpe", mode="lines+markers", name="Sharpe", x=capital, y=[None if row["sharpe"] is None else round(row["sharpe"], 3) for row in ordered],
                     yaxis="y2", marker={"size": 4}, hovertemplate="<b>Capital %{x:,.0f}</b><br>Sharpe: %{y:+.2f}<extra></extra>")
    return F.figure("strategy.costs.capacity", [cagr, sharpe], yaxis={**(nice_axis(cagr["y"], suffix="%") or {"ticksuffix": "%"})}, yaxis2={"overlaying": "y", "side": "left", "showgrid": False, **(nice_axis(sharpe["y"]) or {})},
                    xaxis={"type": "log", "tickformat": "~s"}, margin={"l": 40, "r": 52, "t": 12, "b": 26})
