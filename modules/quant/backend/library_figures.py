"""The three charts of a library item's page, drawn here as Plotly and shown by the Quant chart (`frontend/chart/Plot.tsx`): the cumulative record after publication (a factor or a model),
the realised volatility and the risk shares against publication (an allocation).

Every line says what it is (`meta.role`: main, benchmark, compare, earlier, group, attention, watch) and each chart which one it is (`layout.meta.key`), so the page colours them
the same everywhere. What the mouse reads out is written in each trace's `hovertemplate` as the draft's charts read: a signed figure carries + or −, an unsigned one
none, and a unit stands once. A chart that cannot be drawn is `{"missing": the reason}`, never left out.
"""
from __future__ import annotations

from datetime import UTC, datetime

from talyx_sdk.i18n import t

ROLES = {"main", "benchmark", "compare", "earlier", "group", "attention", "watch"}
OVER = 0.10  # a risk share this far (10 points) from what was published is the allocation's to look at
SHOWN = 10  # risk shares drawn for an allocation of many assets: the largest, and every one over 10 points besides


def day(label, timeframe="1d"):
    moment = datetime.fromtimestamp(label / 1000, UTC)
    return moment.strftime("%Y-%m-%d") if not timeframe or timeframe[-1] == "d" else moment.strftime("%Y-%m-%dT%H:%M")


def figure(key, traces, **layout):
    for trace in traces:
        if (trace.get("meta") or {}).get("role") not in ROLES:
            raise ValueError(t("{key}: every line needs a role ({name})", key=key, name=trace.get('name')))
    base = {"meta": {"key": key}, "margin": {"l": 8, "r": 44, "t": 12, "b": 24}, "showlegend": False}
    return {"data": traces, "layout": {**base, **layout}}


def days_axis(xs, timeframe):
    """A daily axis of a few days ticks once a day: left alone, Plotly ticks every twelve hours and writes each day twice."""
    return {"dtick": 86_400_000} if (not timeframe or timeframe[-1] == "d") and len(xs) <= 14 else {}


def line(x, y, name, role, template, **extra):
    return {"type": "scatter", "mode": "lines", "x": list(x), "y": [None if value is None else round(float(value), 6) for value in y], "name": name, "meta": {"role": role},
            **({"hovertemplate": template + "<extra></extra>"} if template else {}), **extra}


def of_version(version, points, *, metric="ic", span="since"):
    """The charts of one version: {"cumulative"} for a factor or a model, {"volatility", "riskShares"} for an allocation, nothing for one-off data."""
    kind = (version.get("publication") or {}).get("kind")
    if version.get("oneOff"):
        return {}
    if kind == "allocation":
        return {"volatility": volatility(version, points), "riskShares": risk_shares(version)}
    return {"cumulative": cumulative(version, points, metric, span)}


def nothing_settled(version, after, timeframe):
    """"Nothing settled yet: 3 periods since Nov 7, 2026, all waiting for their 5-day return" (library.js)."""
    import time
    # Counted from the last bar the publication held (a data day), as the periods after publication are.
    published = version.get("publishedAt")
    day = time.gmtime(published / 1000) if published else None
    when = f"{time.strftime('%b', day)} {day.tm_mday}, {day.tm_year}" if day else None
    since = f" since {when}" if when else " since publication"
    holding = (version.get("horizon") or {}).get("holding")
    unit = "day" if timeframe.endswith("d") else "bar"
    if not after and not version.get("auto", True):
        return f"No values after {when}: auto-update is off" if when else "No values after publication: auto-update is off"
    if not after:
        return f"Nothing settled yet: no period{since}"
    waiting = f", all waiting for their {holding}-{unit} return" if holding else ", all waiting for their forward return"
    return f"Nothing settled yet: {len(after)} {'period' if len(after) == 1 else 'periods'}{since}{waiting}"


def cumulative(version, points, metric, span):
    timeframe = version.get("timeframe") or "1d"
    key = "ic" if metric == "ic" else "ls"
    after = [point for point in points if point["after"]]
    settled = [point for point in after if point["settled"] and point.get(key) is not None]
    if metric == "ls" and span != "all":
        settled = [{**point, "ls": point["trackedLs"]} for point in points if point.get("trackedLs") is not None]
    pending = [point for point in after if not point["settled"]]
    if not settled and (span != "all" or not any(point.get(key) is not None for point in points)):
        return {"missing": nothing_settled(version, after, timeframe)}
    published = (version.get("published") or {})
    through = version["publishedAt"]
    # The IC's cumulative line is its sum (an IC is not a return); the long-short's compounds, as every return in Quant does.
    add = (lambda total, value: total + value) if metric == "ic" else (lambda total, value: (1 + total) * (1 + value) - 1)  # noqa: E731
    total, before, since = 0.0, [], []
    for point in points:
        if point["date"] <= through and point.get(key) is not None and (point["settled"] or key == "ls") and span == "all":
            total = add(total, point[key])
            before.append((point["date"], total))
    start = total
    since.append((through, start))
    curve = [point for point in points if point["date"] > through and point.get(key) is not None and (point["settled"] or key == "ls")] if span == "all" else settled
    for point in curve:
        total = add(total, point[key])
        since.append((point["date"], total))
    if metric == "ic":
        rate, fmt, unit = published.get("value"), "+.2f", "a day"
        level_format, level_value = "+.4f", rate
        level = [start + rate * index for index in range(len(since))] if rate is not None else None
    else:
        annualization = version["info"]["annualization"]
        rate = published["ls"] / annualization if published.get("ls") is not None else None
        fmt, unit = "+.0%", "a year"
        level_format, level_value = "+.1%", None if rate is None else rate * annualization
        level = [(1 + start) * (1 + rate) ** index - 1 for index in range(len(since))] if rate is not None else None
    xs = [day(date, timeframe) for date, _ in since]
    traces = []
    if span == "all" and before:
        traces.append(line([day(date, timeframe) for date, _ in before], [value for _, value in before], "before publication", "benchmark", f"Before publication %{{y:{fmt}}}",
                           line={"width": 1}))
    traces.append(line(xs, [value for _, value in since], "settled", "main", f"Since publication %{{y:{fmt}}}", line={"width": 1.5}))
    if rate is not None:
        # The dashed line is only drawn; what it reads out is a hidden line on a second axis whose value is the level itself (the read-out takes its numbers from y).
        traces.append(line(xs, level, "at the published level", "compare", "", line={"width": 1, "dash": "dash"}, hoverinfo="skip"))
        traces.append(line(xs, [level_value] * len(since), "at the published level", "compare", f"At the published level %{{y:{level_format}}} {unit}", yaxis="y2", opacity=0, line={"width": 1, "dash": "dash"}))
    shapes = [{"type": "line", "xref": "x", "yref": "paper", "x0": day(through, timeframe), "x1": day(through, timeframe), "y0": 0, "y1": 1, "meta": {"role": "benchmark"},
               "line": {"width": 1, "dash": "dot"}}]
    annotations = [{"x": day(through, timeframe), "y": 1, "xref": "x", "yref": "paper", "text": t("v{version} published", version=version.get('version')), "showarrow": False,
                    # library.js writes it up the publication line, on its left
                    "textangle": -90, "xanchor": "right", "yanchor": "top"}]
    if pending:
        first, last = pending[0]["date"], pending[-1]["date"]
        shapes.append({"type": "rect", "xref": "x", "yref": "paper", "x0": day(first - 43_200_000, "1h" if timeframe[-1] != "d" else "1d"), "x1": day(last + 43_200_000, "1h" if timeframe[-1] != "d" else "1d"),
                       "y0": 0, "y1": 1, "layer": "below", "line": {"width": 0}, "meta": {"role": "group"}})
        annotations.append({"x": day(first, timeframe), "y": 1, "xref": "x", "yref": "paper", "text": t("{pending} pending", pending=len(pending)), "showarrow": False, "xanchor": "left"})
        # The section's key names the shaded stretch too (library.js: settled, at the published level, pending): an empty box trace in its colour.
        traces.append({"type": "bar", "x": [], "y": [], "name": "pending", "meta": {"role": "group"}, "hoverinfo": "none"})
    return figure("library.cumulative", traces, hovermode="x unified", shapes=shapes, annotations=annotations,
                  yaxis={"side": "right", "tickformat": ".2f" if metric == "ic" else ".0%"}, yaxis2={"overlaying": "y", "visible": False}, xaxis=days_axis([day(date, timeframe) for date, _ in before] + xs, timeframe))


def volatility(version, points):
    published = (version.get("published") or {}).get("value")
    # Since publication, as the section says (library.js): the days the version made, not the history it was published with.
    shown = [point for point in points if point.get("value") is not None and point.get("after")]
    if not shown or published is None:
        return {"missing": "No realised volatility yet: the allocation has not made enough periods since publication." if not shown else "The publication recorded no volatility to compare with."}
    timeframe = version.get("timeframe") or "1d"
    xs = [day(point["date"], timeframe) for point in shown]
    traces = [line(xs, [point["value"] for point in shown], "63-day realised", "main", "Realised 63-day %{y:.1%}", line={"width": 1.4}),
              line(xs, [published] * len(xs), "published", "compare", "Published %{y:.1%}", line={"width": 1, "dash": "dash"}),
              line(xs, [1.5 * published] * len(xs), "1.5×", "watch", "1.5× %{y:.1%}", line={"width": 1.2, "dash": "dot"})]  # amber, as the draft's: worth a look
    return figure("library.volatility", traces, hovermode="x unified", yaxis={"side": "right", "tickformat": ".0%"}, xaxis=days_axis(xs, timeframe))


def risk_shares(version):
    shares = ((version.get("after") or {}).get("shares")) or {}
    then, now = shares.get("published") or {}, shares.get("now") or {}
    every = set(then) | set(now)
    if not every or not now:
        return {"missing": "No risk shares yet: the allocation has made no period since publication."}
    # Each asset as one row (library.js): with many assets, the largest by share then or now and every one over 10 points, the largest first.
    size = lambda asset: max(then.get(asset, 0.0), now.get(asset, 0.0))  # noqa: E731
    moved = {asset for asset in every if abs(now.get(asset, 0.0) - then.get(asset, 0.0)) > OVER}
    assets = sorted(set(sorted(every, key=lambda asset: (-size(asset), asset))[:SHOWN]) | moved, key=lambda asset: (-then.get(asset, 0.0), -now.get(asset, 0.0), asset))
    names = {item["instrumentId"]: item.get("displaySymbol") or item["column"] for item in ((version.get("info") or {}).get("instruments") or [])}
    labels = [names.get(asset, asset) for asset in assets]
    delta = [(now.get(asset, 0.0) - then.get(asset, 0.0)) * 100 for asset in assets]
    over = [abs(change) > OVER * 100 for change in delta]
    # The change written at the end of each bar of now, as the draft does (−4.4, +12.4).
    ends = [f"{change:+.1f}".replace("-", "−") for change in delta]

    def bar(name, role, values, template, **extra):
        return {"type": "bar", "orientation": "h", "y": labels, "x": [None if value is None else round(float(value), 6) for value in values], "name": name, "meta": {"role": role},
                "hovertemplate": template + "<extra></extra>", **extra}
    shown = [now.get(asset, 0.0) for asset in assets]
    # Now and over 10 pt share one slot (one of them is empty for each asset), so each asset is two bars, as thick as the draft's; the change is
    # written at full size beside the bar, not shrunk to its thickness.
    ends_of = {"textposition": "outside", "cliponaxis": False, "constraintext": "none", "offsetgroup": "now"}
    traces = [bar("published", "earlier", [then.get(asset, 0.0) for asset in assets], "Published %{x:.1%}", offsetgroup="published"),  # grey, as the draft's
              bar("now", "main", [value if not flag else None for value, flag in zip(shown, over, strict=True)], "Now %{x:.1%}",
                  text=[text if not flag else "" for text, flag in zip(ends, over, strict=True)], **ends_of),
              bar("over 10 pt", "watch", [value if flag else None for value, flag in zip(shown, over, strict=True)], "Now %{x:.1%}",
                  text=[text if flag else "" for text, flag in zip(ends, over, strict=True)], **ends_of),
              # The change in points is read out from a hidden bar on a second axis, whose value it is.
              bar("Change", "main", delta, "Change %{x:+.1f} pt", xaxis="x2", opacity=0, showlegend=False)]
    return figure("library.risk_shares", traces, meta={"key": "library.risk_shares", "shown": len(assets), "of": len(every)}, barmode="group", bargap=0.3, bargroupgap=0.05, hovermode="y unified",
                  yaxis={"autorange": "reversed", "side": "left"}, xaxis={"tickformat": ".0%", "side": "bottom"}, xaxis2={"overlaying": "x", "visible": False})
