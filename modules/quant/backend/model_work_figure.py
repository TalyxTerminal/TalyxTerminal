"""What the model's Work page draws of a run, from its summary (the experiment table's row): the test IC window by window with the training and
validation ICs beside it and the running total of the test IC against two baselines, also while the run is going; and the comparison of several
runs. It reads only the summary, so a run in progress draws the windows it has finished."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from talyx_sdk.i18n import t

from ..native import model_figures as F

MISSING = "This run's code keeps no windows (no q.record); numbers are for the whole period."


def _title_once(traces):
    """With one readout for every line at a window, the window's title is said once (on the first line that has it), not on each row."""
    seen = False
    for trace in traces:
        template = trace.get("hovertemplate") or ""
        if template.startswith("%{customdata}<br>"):
            if seen:
                trace["hovertemplate"] = template[len("%{customdata}<br>"):]
            seen = True
    return traces


def _month(stamp):
    return datetime.fromtimestamp(stamp / 1000, UTC).strftime("%b %Y")


def _title(window):
    test = window.get("test") or {}
    return f"Window {window['k']} · test {_month(test['from'])} – {_month(test['to'])}" if test else f"Window {window['k']}"


def _cumulative(values):
    total, out = 0.0, []
    for value in values:
        total += value or 0.0
        out.append(total)
    return out


def _bars(name, x, y, role, title, key, **extra):
    return {"type": "bar", "name": name, "x": x, "y": y, "customdata": title, "hovertemplate": f"%{{customdata}}<br>{name} %{{y:+.4f}}<extra></extra>",
            "meta": {"role": role, "key": key}, **extra}


def _failure_note(failure):
    title = (failure or {}).get("title") or "stopped"
    text = re.split(r" in window", title)[0]
    return text.removeprefix("Ran ").strip() or "stopped"


def work_figure(summary, compare=None):
    """`model.work.windows` for one run: bars of the three ICs a window above, and below them, on the same windows, the cumulative test IC with the two
    baselines; one value axis each (two axes on one plot read as one axis with two numbers a step). `compare` is another run's summary, drawn when both
    predict as far ahead and both are finished."""
    key = "model.work.windows"
    if summary.get("perWindow") is False:
        return {**F.figure(key, []), "missing": MISSING}
    windows = sorted(summary.get("windows") or [], key=lambda item: item["k"])
    x = [str(item["k"]) for item in windows]
    titles = [_title(item) for item in windows]
    baselines = summary.get("baselines") or {}
    best = {item["k"]: item.get("ic") for item in (baselines.get("bestInput") or {}).get("windows", [])}
    equal = (baselines.get("equalWeight") or {}).get("windows") or []
    traces = [_bars("Train", x, [item.get("trainIc") for item in windows], "group", titles, key, opacity=0.35),
              _bars("Validation", x, [item.get("validIc") for item in windows], "group", titles, key),
              _bars("Test", x, [item.get("testIc") for item in windows], "updown", titles, key)]
    cumulative = lambda name, values, role, **line: {"type": "scatter", "mode": "lines", "name": name, "x": x, "y": _cumulative(values), "yaxis": "y2",  # noqa: E731
                                                     "customdata": titles, "hovertemplate": f"%{{customdata}}<br>{name} %{{y:+.3f}}<extra></extra>", "line": line, "meta": {"role": role, "key": key}}
    # Named short, so the section's key holds all six on one row: the lower plot is the running totals, and "best input" is what the key numbers call it.
    traces.append(cumulative("Test, cumulative", [item.get("testIc") for item in windows], "main"))
    traces.append(cumulative("Best input", [best.get(item["k"]) for item in windows], "benchmark"))
    traces.append(cumulative("Inputs equal-weight", list(equal[:len(windows)]), "benchmark", dash="dot"))
    if compare is not None and compare.get("horizon") == summary.get("horizon") and compare.get("state") == "completed" and summary.get("state") == "completed":
        other = sorted(compare.get("windows") or [], key=lambda item: item["k"])
        traces.append({"type": "scatter", "mode": "lines", "name": f"#{compare['number']}, cumulative", "x": [str(item["k"]) for item in other], "yaxis": "y2",
                       "y": _cumulative([item.get("testIc") for item in other]), "line": {"dash": "dash"},
                       "hovertemplate": f"#{compare['number']}, cumulative %{{y:+.3f}}<extra></extra>", "meta": {"role": "compare", "key": key}})
    shapes, notes = [], []
    done = len(windows)
    total = summary.get("windowsTotal") or done
    if summary.get("status") == "running" and done < total:
        shapes.append({"type": "rect", "xref": "x", "yref": "paper", "x0": done + 0.5, "x1": done + 1.5, "y0": 0, "y1": 1, "meta": {"role": "benchmark", "key": key}})
        notes.append({"x": str(done + 1), "xref": "x", "yref": "paper", "y": 1, "text": t("training"), "showarrow": False})
    elif summary.get("status") in ("failed",) or summary.get("state") == "failed":
        slot = (summary.get("failure") or {}).get("window") or done + 1
        shapes.append({"type": "rect", "xref": "x", "yref": "paper", "x0": slot - 0.5, "x1": slot + 0.5, "y0": 0, "y1": 1, "meta": {"role": "attention", "key": key}})
        notes.append({"x": str(slot), "xref": "x", "yref": "paper", "y": 1, "text": _failure_note(summary.get("failure")), "showarrow": False})
    # One axis of windows under both plots; a window read anywhere reads its bars and its running totals together.
    return F.figure(key, _title_once(traces), shapes=shapes, annotations=notes, hovermode="x unified", hoversubplots="axis", barmode="group",
                    xaxis={"title": t("Window"), "type": "category", "anchor": "y2"}, yaxis={"tickformat": "+.3f", "domain": [0.5, 1], "nticks": 4},
                    yaxis2={"tickformat": "+.2f", "domain": [0, 0.4], "nticks": 3}, margin={"l": 8, "r": 52, "t": 12, "b": 26})


def compare_figure(summaries):
    """`model.work.compare`: each run's test IC window by window; the first (the selected one) in the body colour, the second dashed, the rest neutral."""
    key = "model.work.compare"
    traces = []
    for index, item in enumerate(summaries):
        windows = sorted(item.get("windows") or [], key=lambda entry: entry["k"])
        role = "main" if index == 0 else "compare" if index == 1 else "group"
        name = f"#{item['number']}"
        traces.append({"type": "scatter", "mode": "lines+markers", "name": name, "x": [str(entry["k"]) for entry in windows], "y": [entry.get("testIc") for entry in windows],
                       "line": {"dash": "dash" if role == "compare" else "solid"}, "customdata": [_title(entry) for entry in windows],
                       "hovertemplate": f"%{{customdata}}<br>{name} %{{y:+.4f}}<extra></extra>", "meta": {"role": role, "key": key}})
    return F.figure(key, _title_once(traces), shapes=[{"type": "line", "xref": "paper", "yref": "y", "x0": 0, "x1": 1, "y0": 0, "y1": 0, "meta": {"role": "benchmark", "key": key}}],
                    hovermode="x unified", xaxis={"title": t("Window"), "type": "category"})


def epoch_figure(summary):
    """`model.work.epochs`: the training and validation loss by epoch of the window now training, with the best epoch marked."""
    key = "model.work.epochs"
    progress = summary.get("progress") or {}
    curve = progress.get("curve") or {"train": [], "valid": []}
    x = list(range(1, len(curve["valid"]) + 1))
    traces = [{"type": "scatter", "mode": "lines", "name": "Training loss", "x": x, "y": curve["train"][:len(x)], "hovertemplate": "Epoch %{x}<br>Training loss %{y:.4f}<extra></extra>",
               "meta": {"role": "benchmark", "key": key}},
              {"type": "scatter", "mode": "lines", "name": "Validation loss", "x": x, "y": curve["valid"], "hovertemplate": "Validation loss %{y:.4f}<extra></extra>",
               "meta": {"role": "main", "key": key}}]
    shapes = []
    best = progress.get("bestEpoch")
    if best:
        shapes.append({"type": "line", "xref": "x", "yref": "paper", "x0": best, "x1": best, "y0": 0, "y1": 1, "line": {"dash": "dash"}, "meta": {"role": "benchmark", "key": key}})
    return F.figure(key, traces, shapes=shapes, hovermode="x unified", xaxis={"title": t("Epoch"), **({"dtick": 1} if len(x) <= 15 else {})}, yaxis={"tickformat": ".4f"})
