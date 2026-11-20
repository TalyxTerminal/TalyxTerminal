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

DESCRIPTION = ("Fixed Range Volume Profile: volume by price between two times from intrabars, up and "
               "down volume, the value area and the point of control, drawn from the start of the range. Without a "
               "start it begins with the chart's data; without an end it follows the latest bar.")
CATEGORY = 'Volume'

UP = color.rgb(41, 98, 255)
DOWN = color.rgb(251, 192, 45)


def profile(start, end, highs, lows, opens, closes, volumes, rows, value_area):
    """Boxes (up and down volume per price row) and the point-of-control line of one range."""
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
    width = (end - start) * 0.7
    boxes = []
    for row in range(rows):
        strength = 30 if low_row <= row <= high_row else 70
        up_right = start + int(width * up[row] / peak)
        down_right = up_right + int(width * down[row] / peak)
        row_bottom, row_top = bottom + row * step, bottom + (row + 1) * step
        if up[row] > 0:
            boxes.append(box.new(start, row_top, up_right, row_bottom, border_color=color.new(UP, 100),
                                 bgcolor=color.new(UP, strength), xloc=xloc.bar_time))
        if down[row] > 0:
            boxes.append(box.new(up_right, row_top, down_right, row_bottom, border_color=color.new(DOWN, 100),
                                 bgcolor=color.new(DOWN, strength), xloc=xloc.bar_time))
    middle = bottom + (poc + 0.5) * step
    return boxes, [line.new(start, middle, end, middle, xloc=xloc.bar_time, color=color.rgb(242, 54, 69), width=2)]


@script.indicator('Fixed Range Volume Profile', shorttitle='FRVP', overlay=True, max_boxes_count=500,
                  max_lines_count=500)
def main(start_time: int = input.time(0, "From"), end_time: int = input.time(0, "To"),
         rows: int = input.int(24, "Row size", minval=1, maxval=100),
         value_area: float = input.float(70.0, "Value area volume %", minval=1.0, maxval=100.0),
         custom: bool = input.bool(False, "Use custom timeframe"),
         chosen: str = input.timeframe("1", "Timeframe")):
    lower = chosen if custom else lower_timeframe(timeframe.in_seconds())
    highs, lows, opens, closes, volumes = request.security_lower_tf(
        syminfo.tickerid, lower, (high, low, open, close, volume))
    range_highs: Persistent[list[float]] = []
    range_lows: Persistent[list[float]] = []
    range_opens: Persistent[list[float]] = []
    range_closes: Persistent[list[float]] = []
    range_volumes: Persistent[list[float]] = []
    start: Persistent[int] = 0
    end: Persistent[int] = 0
    done: Persistent[bool] = False
    drawn_boxes: Persistent[list] = []
    drawn_lines: Persistent[list] = []
    inside = time >= start_time and (end_time == 0 or time < end_time)
    if inside and not done:
        if start == 0:
            start = time
        range_highs.extend(highs)
        range_lows.extend(lows)
        range_opens.extend(opens)
        range_closes.extend(closes)
        range_volumes.extend(volumes)
        end = time_close
    finished = end_time != 0 and time >= end_time and not done
    if (finished or barstate.islast) and range_highs:
        for item in drawn_boxes:
            box.delete(item)
        for item in drawn_lines:
            line.delete(item)
        boxes, lines = profile(start, end, range_highs, range_lows, range_opens, range_closes, range_volumes, rows,
                               value_area)
        drawn_boxes.clear()
        drawn_boxes.extend(boxes)
        drawn_lines.clear()
        drawn_lines.extend(lines)
        done = finished
