"""@pyne"""
from pynecore.lib import (
    barstate,
    box,
    chart,
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

from modules.chart.backend.pine.volume_profile import distribution, lower_timeframe

DESCRIPTION = ("Visible Range Volume Profile: volume by price over the bars on screen from intrabars, up and "
               "down volume, the value area and the point of control, drawn against the right edge of the "
               "range (or its left edge). It is computed again as the chart scrolls.")
CATEGORY = 'Volume'

UP = color.rgb(41, 98, 255)
DOWN = color.rgb(251, 192, 45)


def profile(start, end, highs, lows, opens, closes, volumes, rows, value_area, right=False):
    """Boxes (up and down volume per price row) and the point-of-control line of one range: Fixed Range
    Volume Profile's, with the volume per row counted by numpy; ``right`` draws the rows from the range's
    right edge leftwards, up volume against the edge (TradingView's default placement)."""
    up, down, bottom, step = distribution(highs, lows, opens, closes, volumes, rows)
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
        up_width, down_width = int(width * up[row] / peak), int(width * down[row] / peak)
        if right:
            up_span, down_span = (end - up_width, end), (end - up_width - down_width, end - up_width)
        else:
            up_span, down_span = (start, start + up_width), (start + up_width, start + up_width + down_width)
        row_bottom, row_top = bottom + row * step, bottom + (row + 1) * step
        if up[row] > 0:
            boxes.append(box.new(up_span[0], row_top, up_span[1], row_bottom, border_color=color.new(UP, 100),
                                 bgcolor=color.new(UP, strength), xloc=xloc.bar_time))
        if down[row] > 0:
            boxes.append(box.new(down_span[0], row_top, down_span[1], row_bottom, border_color=color.new(DOWN, 100),
                                 bgcolor=color.new(DOWN, strength), xloc=xloc.bar_time))
    middle = bottom + (poc + 0.5) * step
    return boxes, [line.new(start, middle, end, middle, xloc=xloc.bar_time, color=color.rgb(242, 54, 69), width=2)]


@script.indicator('Visible Range Volume Profile', shorttitle='VRVP', overlay=True, max_boxes_count=500,
                  max_lines_count=500)
def main(rows: int = input.int(24, "Row size", minval=1, maxval=100),
         value_area: float = input.float(70.0, "Value area volume %", minval=1.0, maxval=100.0),
         custom: bool = input.bool(False, "Use custom timeframe"),
         chosen: str = input.timeframe("1", "Timeframe"),
         placement: str = input.string("Right", "Placement", options=("Right", "Left"))):
    lower = chosen if custom else lower_timeframe(timeframe.in_seconds())
    highs, lows, opens, closes, volumes = request.security_lower_tf(
        syminfo.tickerid, lower, (high, low, open, close, volume))
    # Intrabars of the bars from the left edge on; when the right edge follows the newest bar, the left
    # one moves with it, so each kept bar's time and first intrabar say where the range starts.
    range_highs: Persistent[list[float]] = []
    range_lows: Persistent[list[float]] = []
    range_opens: Persistent[list[float]] = []
    range_closes: Persistent[list[float]] = []
    range_volumes: Persistent[list[float]] = []
    bar_times: Persistent[list[int]] = []
    bar_starts: Persistent[list[int]] = []
    end: Persistent[int] = 0
    drawn_boxes: Persistent[list] = []
    drawn_lines: Persistent[list] = []
    left, right = chart.left_visible_bar_time, chart.right_visible_bar_time
    if left <= time <= right:
        bar_times.append(time)
        bar_starts.append(len(range_highs))
        range_highs.extend(highs)
        range_lows.extend(lows)
        range_opens.extend(opens)
        range_closes.extend(closes)
        range_volumes.extend(volumes)
        end = time_close
    if barstate.islast:
        for item in drawn_boxes:
            box.delete(item)
        for item in drawn_lines:
            line.delete(item)
        drawn_boxes.clear()
        drawn_lines.clear()
        first = 0
        while first < len(bar_times) and bar_times[first] < left:
            first += 1
        if first < len(bar_times):
            offset = bar_starts[first]
            if offset < len(range_highs):
                boxes, lines = profile(bar_times[first], end, range_highs[offset:], range_lows[offset:],
                                       range_opens[offset:], range_closes[offset:], range_volumes[offset:], rows,
                                       value_area, placement == "Right")
                drawn_boxes.extend(boxes)
                drawn_lines.extend(lines)
