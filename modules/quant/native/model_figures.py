"""The model's figures, one set a run, each with its fixed identity (`layout.meta.key`) and every line saying what it is (`meta.role`).

Roles are what the Quant chart (`frontend/chart/paint.ts`) colours by: main (the body colour), benchmark (a thin grey line), compare (another run, dashed grey), group
(a neutral band), updown (gains and losses, following the user's up/down colours) and attention (only what needs handling). Qlib's
own figures are kept as Qlib draws them; only their lines are tagged here, by their names, and their fixed colours dropped.
"""
from __future__ import annotations

import base64
import math

import numpy as np

ROLES = {"main", "benchmark", "compare", "group", "updown", "drawdown", "attention", "detail"}


def _clean(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict) and "bdata" in value and "dtype" in value:
        array = np.frombuffer(base64.b64decode(value["bdata"]), dtype=np.dtype(value["dtype"]))
        if "shape" in value:
            array = array.reshape(tuple(int(size) for size in value["shape"].split(",")))
        return _clean(array)
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, np.ndarray):
        if value.dtype.kind == "M":  # dates stay dates: tolist() would turn nanosecond stamps into plain numbers
            return [None if str(item) == "NaT" else (text[:10] if text.endswith("T00:00:00") else text) for item in np.datetime_as_string(value, unit="s") for text in [str(item)]]
        return _clean(value.tolist())
    if isinstance(value, np.floating):
        return _clean(float(value))
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if hasattr(value, "isoformat"):  # pandas Timestamps and datetimes in native figures
        text = value.isoformat()
        return text[:10] if text.endswith("T00:00:00") else text
    if isinstance(value, np.datetime64):
        return str(value)[:10] if str(value).endswith("T00:00:00.000000000") else str(value)
    return value


def figure(key, traces, **layout):
    """A Plotly figure as data: `traces` already carry meta.role and meta.key."""
    for trace in traces:
        meta = trace.get("meta") or {}
        if meta.get("role") not in ROLES or not meta.get("key"):
            raise ValueError(f"{key}: every line needs a role and a key ({trace.get('name')})")
    base = {"meta": {"key": key}, "margin": {"l": 8, "r": 44, "t": 12, "b": 24}, "showlegend": False}
    return _clean({"data": traces, "layout": {**base, **layout}})


def line(x, y, name, role, key, **extra):
    return {"type": "scatter", "mode": "lines", "x": list(x), "y": [None if v is None or not np.isfinite(v) else round(float(v), 6) for v in y],
            "name": name, "meta": {"role": role, "key": key}, **extra}


def bars(x, y, name, role, key, **extra):
    return {"type": "bar", "x": list(x), "y": [None if v is None or not np.isfinite(v) else round(float(v), 6) for v in y],
            "name": name, "meta": {"role": role, "key": key}, **extra}


# ---------------------------------------------------------------------------------------------------------------- Qlib's own figures
def _qlib_role(name, index, of):
    text = (name or "").lower()
    if text.startswith("group"):
        number = int("".join(ch for ch in text if ch.isdigit()) or index + 1)
        return "group", f"group:{number}"
    if "long-short" in text or text == "ls":
        return "main", "long-short"
    if "long-average" in text:
        return "benchmark", "long-average"
    if text in ("rank_ic", "rank ic"):
        return "main", "rank-ic"
    if text == "ic":
        return "benchmark", "ic"
    if "top" in text:
        return "main", "top"
    if "bottom" in text:
        return "benchmark", "bottom"
    if "autocorr" in text or "auto" in text:
        return "main", "autocorr"
    if text in ("", "trace"):
        return ("main" if index == 0 else "group"), f"line:{index + 1}"
    return ("main" if of == 1 else "group"), "line:" + "".join(ch if ch.isalnum() else "-" for ch in text)


def tag_native(plotly_figure, key, *, roles=None):
    """A Qlib figure as data with its key, each trace's role and key from its name (or from `roles`: name → (role, key)), its fixed
    colours removed so the display colours it."""
    found = plotly_figure.to_plotly_json() if hasattr(plotly_figure, "to_plotly_json") else dict(plotly_figure)
    traces = []
    count = len(found.get("data", []))
    seen = set()
    for index, trace in enumerate(found.get("data", [])):
        trace = _clean(dict(trace))
        name = trace.get("name")
        role, trace_key = (roles or {}).get(name) or _qlib_role(name, index, count)
        if trace.get("type") == "heatmap":
            role = "group"
        while trace_key in seen:
            trace_key += "'"
        seen.add(trace_key)
        for colour in ("marker", "line"):
            if isinstance(trace.get(colour), dict):
                trace[colour] = {k: v for k, v in trace[colour].items() if k != "color"}
        trace.pop("colorscale", None) if trace.get("type") != "heatmap" else None
        trace["meta"] = {"role": role, "key": trace_key}
        traces.append(trace)
    layout = dict(found.get("layout") or {})
    layout.pop("template", None)
    layout["meta"] = {"key": key}
    trimmed = _from_first_value(traces)
    if trimmed is not traces and isinstance(layout.get("xaxis"), dict):  # the axis follows the cut lines, not the range Qlib fixed from the whole frame
        layout["xaxis"] = {name: value for name, value in layout["xaxis"].items() if name not in ("range", "autorange")}
    return _clean({"data": trimmed, "layout": layout})


def _from_first_value(traces):
    """Cumulative lines start at the first day that has a value (and one flat day before the first move): Qlib draws them over the whole frame,
    so a model whose predictions begin later gets empty (or flat) days before them. Only figures made of lines are cut; bars and heatmaps stay whole."""
    lines = [trace for trace in traces if trace.get("type", "scatter") == "scatter" and trace.get("mode") in (None, "lines") and trace.get("x") is not None and trace.get("y") is not None]
    if not lines or len(lines) != len(traces):
        return traces
    start = None
    for trace in lines:
        y = [None if value is None else float(value) for value in list(trace["y"])]
        shown = [index for index, value in enumerate(y) if value is not None and math.isfinite(value)]
        if not shown:
            continue
        base = y[shown[0]]
        moved = next((index for index in shown if y[index] != base), None)
        here = max(shown[0], (moved - 1) if moved is not None else shown[0])
        start = here if start is None else min(start, here)
    if not start:
        return traces
    cut = ("x", "y", "customdata", "text", "hovertext")
    return [{**trace, **{key: list(trace[key])[start:] for key in cut if trace.get(key) is not None and not isinstance(trace[key], (str, bytes, dict)) and hasattr(trace[key], "__len__")}} for trace in traces]


def _ic_means(pred_label):
    """The daily Rank IC and the 1-month means of Rank IC and IC, as the draft's IC chart reads them out; None when there is no day to score."""
    import pandas as pd
    frame = pd.DataFrame(pred_label)
    days_of = frame.groupby(level="datetime")
    daily = days_of.apply(lambda day: day["label"].corr(day["score"], method="spearman")).dropna()
    if daily.empty:
        return None
    pearson = days_of.apply(lambda day: day["label"].corr(day["score"])).reindex(daily.index)
    days = [pd.Timestamp(stamp).strftime("%Y-%m-%d") for stamp in daily.index]
    plain = lambda values: [None if v != v else round(float(v), 4) for v in values]  # noqa: E731
    bar = {"type": "bar", "x": days, "y": plain(daily.values), "name": "Rank IC", "meta": {"role": "updown", "key": "rank-ic"}, "hovertemplate": "Rank IC %{y:+.3f}<extra></extra>"}
    means = [{"type": "scatter", "mode": "lines", "x": days, "y": plain(daily.rolling(21, min_periods=5).mean().values), "name": "Rank IC, 1-month mean",
              "meta": {"role": "main", "key": "rank-ic-mean"}, "hovertemplate": "Rank IC, 1-month mean %{y:+.3f}<extra></extra>"},
             {"type": "scatter", "mode": "lines", "x": days, "y": plain(pearson.rolling(21, min_periods=5).mean().values), "name": "IC, 1-month mean",
              "meta": {"role": "group", "key": "ic-mean", "index": 1, "of": 2}, "hovertemplate": "IC, 1-month mean %{y:+.3f}<extra></extra>"}]
    return bar, means


def with_ic_means(native, pred_label):
    """Qlib's IC figure with the draft's two 1-month means added: its daily lines become the faint detail, the daily Rank IC reads out, the daily IC does not."""
    found = _ic_means(pred_label)
    if found is None:
        return native
    _, means = found
    for trace in native["data"]:
        trace["meta"] = {**trace.get("meta", {}), "role": "detail"}
        name = trace.get("name") or ""
        trace["hovertemplate"] = f"{name} %{{y:+.3f}}<extra></extra>"
        if name != "Rank IC":
            trace["hoverinfo"] = "skip"
    native["data"] = [*native["data"], *means]
    native.setdefault("layout", {})["hovermode"] = "x unified"
    return native


def _own_ic(pred_label, key):
    """Daily Rank IC as bars (up and down colours) with the 1-month means of Rank IC and of IC, from the prediction and the label (the draft's chart)."""
    try:
        found = _ic_means(pred_label)
        if found is None:
            return None
        bar, means = found
        return figure(key, [bar, *means], hovermode="x unified")
    except Exception:  # noqa: BLE001 - a figure that cannot be drawn says why where it is shown
        return None


def _own_monthly(pred_label, key):
    """The daily Rank IC averaged by calendar month, a year a row (as Qlib's monthly IC heatmap)."""
    try:
        import pandas as pd
        frame = pd.DataFrame(pred_label)
        daily = frame.groupby(level="datetime").apply(lambda day: day["label"].corr(day["score"], method="spearman")).dropna()
        if daily.empty:
            return None
        daily.index = pd.DatetimeIndex(daily.index)
        means = daily.groupby([daily.index.year, daily.index.month]).mean()
        years = sorted({year for year, _ in means.index})
        z = [[None] * 12 for _ in years]
        for (year, month), value in means.items():
            z[years.index(year)][month - 1] = round(float(value), 4)
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return figure(key, [{"type": "heatmap", "x": names, "y": [str(year) for year in years], "z": z, "showscale": False, "xgap": 1, "ygap": 1, "name": "Mean Rank IC",
                             "meta": {"role": "updown", "key": "monthly"}, "hovertemplate": "%{x} %{y}<br>Mean Rank IC %{z:+.4f}<extra></extra>"}],
                      xaxis={"side": "top", "type": "category"}, yaxis={"autorange": "reversed", "type": "category"}, margin={"l": 44, "r": 8, "t": 24, "b": 8})
    except Exception:  # noqa: BLE001 - a figure that cannot be drawn says why where it is shown
        return None


def qlib_graphs(pred_label, groups, *, prefix="model"):
    """Qlib's signal analysis figures (group returns and the long-short distribution, IC with its distribution and months, the
    prediction's auto-correlation, the top and bottom groups' turnover), each tagged. Returns {key: figure} and {key: reason} for
    any Qlib could not draw."""
    from qlib.contrib.report.analysis_model.analysis_model_performance import (
        model_performance_graph,
    )
    names ={"group_return": ["returns.groups", "returns.ls-distribution"], "pred_ic": ["information.ic", "information.monthly", "information.distribution"],
             "pred_autocorr": ["turnover.autocorr"], "pred_turnover": ["turnover.top-bottom"]}
    out, missing = {}, {}
    for graph, keys in names.items():
        try:  # drawing and tagging both: on this plotly Qlib's figures can come out but fail to read back ("could not convert string to float: 'dtype'")
            figures = model_performance_graph(pred_label, N=groups, graph_names=[graph], show_notebook=False)
            tagged = {(f"{prefix}.{keys[index]}" if index < len(keys) else f"{prefix}.{keys[-1]}:{index}"): found for index, found in enumerate(figures)}
            tagged = {key: tag_native(found, key) for key, found in tagged.items()}
            if f"{prefix}.information.ic" in tagged:  # the draft's chart is Qlib's with two 1-month means on it
                tagged[f"{prefix}.information.ic"] = with_ic_means(tagged[f"{prefix}.information.ic"], pred_label)
            if f"{prefix}.information.monthly" in tagged:  # the same months, read out as the draft's ("Mar 2021 · Mean Rank IC +0.0123"): Qlib's heatmap has no read-out
                own = _own_monthly(pred_label, f"{prefix}.information.monthly")
                if own is not None:
                    tagged[f"{prefix}.information.monthly"] = own
        except Exception as error:  # noqa: BLE001 - a figure that cannot be drawn says why, in its section
            if graph == "pred_ic":  # Qlib's drawing broke on this plotly: the daily IC and the months are TALYX's own to draw, the distribution the report draws itself
                for key, own in ((keys[0], _own_ic(pred_label, f"{prefix}.{keys[0]}")), (keys[1], _own_monthly(pred_label, f"{prefix}.{keys[1]}"))):
                    if own is not None:
                        out[f"{prefix}.{key}"] = own
                keys = [key for key in keys[:2] if f"{prefix}.{key}" not in out]
            for key in keys:
                missing[f"{prefix}.{key}"] = f"Qlib could not draw it: {type(error).__name__}: {str(error)[:200]}"
            continue
        out.update(tagged)
    return out, missing
