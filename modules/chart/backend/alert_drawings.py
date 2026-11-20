"""A drawing's price at a bar time, for alerts.

The geometry is the alert's copy of the drawing (kept in step when the chart changes it).
Lines go through their two points on the chart's bar grid (``TimeMap``), beyond them only
where the tool extends (ray, extended line, the extend settings); a channel gives its two
lines. No value where the time maps to no bar index (past Market's calendar coverage).
"""
from __future__ import annotations

from .timemap import TimeMap, line_value

ALERT_TOOLS = ("hline", "horizontal_ray", "trendline", "info_line", "trend_angle", "ray", "extended_line",
               "parallel_channel")
CHANNEL_TOOLS = ("parallel_channel",)


def geometry(drawing: dict) -> dict:
    return {"tool": drawing["tool"], "points": [{"time": point["time"], "price": point["price"]}
                                                for point in drawing["points"]],
            "extendLeft": bool(drawing.get("settings", {}).get("extendLeft")),
            "extendRight": bool(drawing.get("settings", {}).get("extendRight"))}


def line_at(points: list[dict], timemap: TimeMap, time: int, left: bool, right: bool) -> float | None:
    low, high = sorted((points[0]["time"], points[1]["time"]))
    if time < low and not left or time > high and not right:
        return None
    return line_value(points, timemap, time)


def prices(shape: dict, timemap: TimeMap, time: int) -> list[float | None]:
    """The drawing's price at ``time``: one value, or a channel's two."""
    tool, points = shape["tool"], shape["points"]
    if tool == "hline":
        return [points[0]["price"]]
    if tool == "horizontal_ray":
        return [points[0]["price"] if time >= points[0]["time"] else None]
    if tool == "ray":
        forward = points[1]["time"] >= points[0]["time"]
        return [line_at(points, timemap, time, not forward, forward)]
    if tool == "extended_line":
        return [line_at(points, timemap, time, True, True)]
    base = line_at(points[:2], timemap, time, shape["extendLeft"], shape["extendRight"])
    if tool not in CHANNEL_TOOLS:
        return [base]
    third = points[2]
    through = line_value(points[:2], timemap, third["time"])
    if base is None or through is None:
        return [None, None]
    return [base, base + third["price"] - through]


def furthest(shape: dict) -> int:
    return max(point["time"] for point in shape["points"])
