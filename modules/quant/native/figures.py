"""Plotly figures that say what each line is, for every Quant report (factor Results, strategy Results ...).

Every trace carries `meta.role` (main, benchmark, compare, group with `meta.index`/`meta.of`, updown, drawdown, attention) and `meta.key`;
every figure carries `layout.meta.key`, its fixed identity. The backend writes no colours: the display colours by role. A section or
figure that cannot be made raises `Missing` with the reason, shown in its place.
"""
from __future__ import annotations

import math

import numpy as np


def num(value, digits=6):
    """A JSON number (rounded) or None."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def nums(values, digits=6):
    return [num(value, digits) for value in np.asarray(values, dtype=float).tolist()]


def iso(dates):
    return [stamp.strftime("%Y-%m-%d %H:%M") if stamp.hour or stamp.minute else stamp.strftime("%Y-%m-%d") for stamp in dates]


def trace(kind, role, key, **values):
    """One Plotly trace that says what it is: the display colours it by `role`; `key` is its identity inside the figure."""
    meta = {"role": role, "key": key}
    for name in ("index", "of", "side"):
        if name in values:
            meta[name] = values.pop(name)
    return {"type": kind, "meta": meta, **values}


def figure(key, traces, **layout):
    """A figure in the draft's frame: the value axis on the right, no Plotly legend (the display draws the key beside the section's title from the
    traces' names and roles) and no axis titles (the section's title says what is drawn). Axis settings given here are merged over the right-hand axis."""
    base = {"meta": {"key": key}, "showlegend": False, "margin": {"l": 8, "r": 52, "t": 12, "b": 26}, "yaxis": {"side": "right"}}
    for name, value in layout.items():
        if name.startswith(("xaxis", "yaxis")) and isinstance(value, dict):
            value = {**base.get(name, {}), **{part: setting for part, setting in value.items() if part != "title"}}
        base[name] = value
    base["showlegend"] = False
    return {"data": traces, "layout": base}


def hover(head, *rows):
    """The read-out of a trace, the draft's way: a head (the date or category, e.g. `%{x}`), then one `name: value` line per quantity, the unit only
    after the number. `hover("%{x}", ("Return", "%{y:+.1%}"))`; a head of None leaves the date to the display."""
    lines = ([f"<b>{head}</b>"] if head else []) + [f"{name}: {value}" if name else value for name, value in rows]
    return "<br>".join(lines) + "<extra></extra>"


def line(x, y, role, key, name, **values):
    return trace("scatter", role, key, mode="lines", x=x, y=y, name=name, **values)


def reference(x, y, key, name):
    """A thin reference line (zero, a mean, the 45-degree line): drawn like a benchmark, without hover."""
    return line(x, y, "benchmark", key, name, hoverinfo="skip")


class Missing(Exception):
    """A section that cannot be made for a stated reason (no industry for these names, too few days ...)."""


def reason_of(error):
    if isinstance(error, Missing):
        return str(error)
    text = str(error).strip().splitlines()[0] if str(error).strip() else ""
    return f"{type(error).__name__}{': ' + text[:300] if text else ''}"



# --- what each figure reads out on hover ----------------------------------------------------------------------------------------
# The draft's tooltips: a title, then the lines the figure carries; a quantity that has a sign shows + or -, one that has not shows none,
# and a unit is written once. A figure's lines are its traces; leverage rides on the balance line's `customdata`.
def read_out(figure_):
    """Give every trace of one figure its `hovertemplate` (and the `customdata` it reads), by the figure's fixed key."""
    key = figure_["layout"]["meta"]["key"]
    for item in figure_["data"]:
        name = item.get("name") or {"strategy.yearly": "Return", "strategy.monthly": "Return"}.get(key, "")
        kind = item.get("type")
        if item.get("hoverinfo") == "skip":
            continue
        head = "<b>%{x}</b><br>"
        if key == "strategy.equity" and kind == "scatter":
            item["customdata"] = [None if value is None else round(value - 1, 6) for value in item.get("y", [])]
            template = head + name + ": %{customdata:+.1%}"
        elif key == "strategy.drawdown":
            template = head + name + ": %{y:.1f}%"
        elif key == "strategy.yearly":
            template = head + name + ": %{y:+.1f}%"
        elif key == "strategy.monthly":
            template = "<b>%{y} %{x}</b><br>Return: %{z:+.2f}%"
        elif key == "strategy.tradeReturns":
            template = "Return: %{x:.1f}%<br>Trades: %{y}"
        elif key == "strategy.barsHeld":
            template = "%{x} bars<br>" + name + ": %{y}"
        elif key == "strategy.rollingSharpe":
            template = head + "Sharpe: %{y:.1f}"
        elif key == "strategy.rollingBeta":
            template = head + "Beta: %{y:+.2f}"
        elif key == "strategy.rollingVolatility":
            template = head + name + ": %{y:.0%}"
        elif key == "strategy.exposure":
            template = head + name + ": %{y:.2%}"
        elif key == "strategy.legs":
            template = head + name + ": %{y:+.1%}"
        elif key == "strategy.live":
            template = head + name + ": %{y:+.1%}"
        elif key == "strategy.turnover":
            template = head + name + ": %{y:.1f}×"
        elif key == "strategy.account":
            template = head + name + ": %{y:,.0f}" + ("<br>Leverage: %{customdata:.2f}×" if item.get("customdata") is not None else "")
        elif key == "strategy.leverage":
            template = head + "Leverage: %{y:.2f}×"
        elif key in ("strategy.mae", "strategy.mfe"):
            label = "MAE" if key == "strategy.mae" else "MFE"
            template = "<b>%{text}</b><br>Return: %{y:+.2f}%<br>" + label + ": %{x:+.2f}%<br>Exit: %{customdata}"
        elif key == "strategy.costs":
            template = "%{x}: %{y:+,.2f}"
        elif key == "strategy.instruments":
            template = head + name + ": %{y:+,.0f}"
        elif key.startswith("strategy.plot."):
            template = head + name + ": %{y:.4g}"
        else:
            continue
        item["hovertemplate"] = template + "<extra></extra>"
    plain_axes(figure_)
    return figure_


def plain_axes(figure_):
    """No axis titles ("Date", "Equity") and no Plotly legend: the section's head says what is drawn and carries the key. The equity figure's value axis reads as a signed return, as the draft does."""
    layout = figure_.setdefault("layout", {})
    layout["showlegend"] = False   # the key is drawn beside the section's title
    for name in [name for name in layout if name.startswith(("xaxis", "yaxis"))]:
        if isinstance(layout[name], dict):
            layout[name].pop("title", None)
    if layout.get("meta", {}).get("key") == "strategy.drawdown":
        layout.setdefault("yaxis", {}).update({"ticksuffix": "%", "tickformat": ".0f"})
    if layout.get("meta", {}).get("key") == "strategy.equity":
        layout["meta"]["scales"] = ["linear", "log"]   # the display offers Linear | Log beside the title; its levels stay above zero
        levels = [value for item in figure_["data"] for value in item.get("y", []) if isinstance(value, (int, float))]
        if levels:
            low, high = min(levels) - 1, max(levels) - 1
            raw = max((high - low) / 5, 1e-9)
            step = next((nice for nice in (.01, .02, .05, .1, .2, .25, .5, 1, 2, 5, 10, 20, 50, 100) if nice >= raw), 100)
            first, last = math.ceil(low / step - 1e-9), math.floor(high / step + 1e-9)
            ticks = [round(k * step, 6) for k in range(first, last + 1)]
            layout.setdefault("yaxis", {}).update({"tickmode": "array", "tickvals": [round(1 + tick, 6) for tick in ticks],
                                                   "ticktext": [("+" if tick > 0 else "−" if tick < 0 else "") + f"{abs(tick) * 100:.0f}%" for tick in ticks]})
    return figure_
