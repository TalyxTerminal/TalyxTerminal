"""@pyne"""
from pynecore.lib import (
    array,
    close,
    color,
    format,
    input,
    math,
    na,
    open,
    plotcandle,
    request,
    script,
    syminfo,
    timeframe,
    volume,
)
from pynecore.types import Persistent

DESCRIPTION = ("Cumulative Volume Delta: volume of rising intrabars minus falling ones (an unchanged intrabar "
               "counts by its move from the one before), summed from the start of each anchor period, as candles.")
CATEGORY = 'Volume'

@script.indicator('Cumulative Volume Delta', shorttitle='CVD', overlay=False, format=format.volume)
def main(anchor: str = input.timeframe("1D", "Anchor period"),
         custom: bool = input.bool(False, "Use custom timeframe"),
         chosen: str = input.timeframe("1", "Timeframe"),
         up_color: color.Color = input.color(color.teal, "Up color"),
         down_color: color.Color = input.color(color.red, "Down color")):
    lower = chosen if custom else "1S" if timeframe.isseconds else "1" if timeframe.isintraday else \
        "5" if timeframe.isdaily else "60"  # TradingView's intrabar rule
    opens, closes, volumes = request.security_lower_tf(syminfo.tickerid, lower, (open, close, volume))
    total: Persistent[float] = 0.0
    previous: Persistent[float] = na(float)
    if timeframe.change(anchor):
        total = 0.0
    start = total
    top = total
    bottom = total
    for index in range(array.size(closes)):
        step_close = array.get(closes, index)
        step_open = array.get(opens, index)
        rising = step_close > step_open or step_close == step_open and not na(previous) and step_close > previous
        falling = step_close < step_open or step_close == step_open and not na(previous) and step_close < previous
        total += array.get(volumes, index) if rising else -array.get(volumes, index) if falling else 0.0
        top = math.max(top, total)
        bottom = math.min(bottom, total)
        previous = step_close
    shade = up_color if total >= start else down_color
    plotcandle(start, top, bottom, total, "CVD", color=shade, wickcolor=shade, bordercolor=shade)
