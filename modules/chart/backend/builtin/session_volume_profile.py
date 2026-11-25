"""@pyne"""
from pynecore.lib import (
    array,
    barstate,
    box,
    close,
    color,
    high,
    input,
    line,
    low,
    open,
    request,
    script,
    syminfo,
    time,
    time_close,
    timeframe,
    volume,
    xloc,
)
from pynecore.types import Persistent

from modules.chart.backend.pine.volume_profile import lower_timeframe

DESCRIPTION = ("Session Volume Profile: each day's volume by price from intrabars, up and down volume, "
               "the value area and the point of control, drawn from the start of the session.")
CATEGORY = 'Volume'

def profile(start, end, highs, lows, opens, closes, volumes, rows, value_area, colors):
    """Boxes (up and down volume per price row) and the point-of-control line of one range (``colors``: up, down,
    point of control)."""
    top, bottom = max(highs), min(lows)
    step = (top - bottom) / rows if top > bottom else 1.0
    up, down = array.new_float(rows, 0.0), array.new_float(rows, 0.0)
    for index in range(len(highs)):
        upper, lower, amount = highs[index], lows[index], volumes[index]
        first = min(rows - 1, max(0, int((lower - bottom) / step)))
        last = min(rows - 1, max(0, int((upper - bottom) / step)))
        side = up if closes[index] >= opens[index] else down
        for row in range(first, last + 1):
            overlap = min(upper, bottom + (row + 1) * step) - max(lower, bottom + row * step)
            side[row] += amount * (max(overlap, 0.0) / (upper - lower) if upper > lower else 1.0 / (last - first + 1))
    totals = [up[row] + down[row] for row in range(rows)]
    peak = max(totals)
    if peak <= 0:
        return [], []
    poc = totals.index(peak)
    inside, low_row, high_row = totals[poc], poc, poc
    while inside < sum(totals) * value_area / 100 and (low_row > 0 or high_row < rows - 1):
        below = totals[low_row - 1] if low_row > 0 else -1.0
        above = totals[high_row + 1] if high_row < rows - 1 else -1.0
        if above >= below:
            high_row += 1
            inside += above
        else:
            low_row -= 1
            inside += below
    up_color, down_color, poc_color = colors
    width = (end - start) * 0.7
    boxes = []
    for row in range(rows):
        strength = 30 if low_row <= row <= high_row else 70
        up_right = start + int(width * up[row] / peak)
        down_right = up_right + int(width * down[row] / peak)
        row_bottom, row_top = bottom + row * step, bottom + (row + 1) * step
        if up[row] > 0:
            boxes.append(box.new(start, row_top, up_right, row_bottom, border_color=color.new(up_color, 100),
                                 bgcolor=color.new(up_color, strength), xloc=xloc.bar_time))
        if down[row] > 0:
            boxes.append(box.new(up_right, row_top, down_right, row_bottom, border_color=color.new(down_color, 100),
                                 bgcolor=color.new(down_color, strength), xloc=xloc.bar_time))
    middle = bottom + (poc + 0.5) * step
    return boxes, [line.new(start, middle, end, middle, xloc=xloc.bar_time, color=poc_color, width=2)]


@script.indicator('Session Volume Profile', shorttitle='SVP', overlay=True, max_boxes_count=4000,
                  max_lines_count=500)
def main(rows: int = input.int(24, "Row size", minval=1, maxval=100),
         value_area: float = input.float(70.0, "Value area volume %", minval=1.0, maxval=100.0),
         custom: bool = input.bool(False, "Use custom timeframe"),
         chosen: str = input.timeframe("1", "Timeframe"),
         up_volume: color.Color = input.color(color.rgb(41, 98, 255), "Up Volume"),
         down_volume: color.Color = input.color(color.rgb(251, 192, 45), "Down Volume"),
         poc: color.Color = input.color(color.rgb(242, 54, 69), "POC")):
    lower = chosen if custom else lower_timeframe(timeframe.in_seconds())
    highs, lows, opens, closes, volumes = request.security_lower_tf(
        syminfo.tickerid, lower, (high, low, open, close, volume))
    session_highs: Persistent[list[float]] = []
    session_lows: Persistent[list[float]] = []
    session_opens: Persistent[list[float]] = []
    session_closes: Persistent[list[float]] = []
    session_volumes: Persistent[list[float]] = []
    start: Persistent[int] = 0
    end: Persistent[int] = 0
    developing_boxes: Persistent[list] = []
    developing_lines: Persistent[list] = []
    if timeframe.change("D") or start == 0:
        if session_highs:
            for item in developing_boxes:
                box.delete(item)
            for item in developing_lines:
                line.delete(item)
            developing_boxes.clear()
            developing_lines.clear()
            profile(start, end, session_highs, session_lows, session_opens, session_closes, session_volumes, rows,
                    value_area, (up_volume, down_volume, poc))
        for values in (session_highs, session_lows, session_opens, session_closes, session_volumes):
            values.clear()
        start = time
    session_highs.extend(highs)
    session_lows.extend(lows)
    session_opens.extend(opens)
    session_closes.extend(closes)
    session_volumes.extend(volumes)
    end = time_close
    if barstate.islast and session_highs:
        for item in developing_boxes:
            box.delete(item)
        for item in developing_lines:
            line.delete(item)
        boxes, lines = profile(start, end, session_highs, session_lows, session_opens, session_closes,
                               session_volumes, rows, value_area, (up_volume, down_volume, poc))
        developing_boxes.clear()
        developing_boxes.extend(boxes)
        developing_lines.clear()
        developing_lines.extend(lines)
