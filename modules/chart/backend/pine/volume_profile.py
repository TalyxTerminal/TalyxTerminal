"""What the built-in volume profiles share (plain Python, imported by their scripts)."""
from __future__ import annotations

import numpy as np


def lower_timeframe(seconds: float) -> str:
    """TradingView's lower timeframe for volume profiles, by the chart's (Session Volume Profile help):
    1S-10S 1S, 15S 5S, 30S 15S; 1-15 minutes 1, 16-30 5, 31-60 10, 61-120 15, 121-240 30, longer 60."""
    if seconds < 60:
        return "1S" if seconds <= 10 else "5S" if seconds <= 15 else "15S"
    for minutes, lower in ((15, "1"), (30, "5"), (60, "10"), (120, "15"), (240, "30")):
        if seconds <= minutes * 60:
            return lower
    return "60"


def distribution(highs, lows, opens, closes, volumes, rows: int) -> tuple[list[float], list[float], float, float]:
    """Up and down volume per price row between the intrabars' lowest low and highest high, each
    intrabar's volume spread over the rows it covers by overlap (one with no range goes to its
    row); up when it closed at or above its open. Returns ``(up, down, bottom, step)``."""
    rows = int(rows)  # a Pine int arrives as a float
    high, low = np.asarray(highs, dtype=float), np.asarray(lows, dtype=float)
    volume = np.asarray(volumes, dtype=float)
    top, bottom = float(high.max()), float(low.min())
    step = (top - bottom) / rows if top > bottom else 1.0
    edges = bottom + step * np.arange(rows + 1)
    span = high - low
    flat = span <= 0
    density = np.where(flat, 0.0, volume / np.where(flat, 1.0, span))
    rising = np.asarray(closes, dtype=float) >= np.asarray(opens, dtype=float)
    sides = []
    for side in (rising, ~rising):
        side_low, side_span, side_density = low[side], span[side], density[side]
        # the volume below each row edge: an intrabar's density times its range under the edge
        below = np.array([np.dot(side_density, np.clip(edge - side_low, 0.0, side_span)) for edge in edges])
        rows_volume = np.diff(below)
        points = side & flat
        if points.any():
            row = np.clip(((low[points] - bottom) / step).astype(int), 0, rows - 1)
            rows_volume = rows_volume + np.bincount(row, weights=volume[points], minlength=rows)
        sides.append(rows_volume.tolist())
    return sides[0], sides[1], bottom, step
