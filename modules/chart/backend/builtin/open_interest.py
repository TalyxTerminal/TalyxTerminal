"""@pyne"""
from pynecore.lib import (
    close,
    color,
    format,
    high,
    input,
    low,
    open,
    plotcandle,
    request,
    script,
    syminfo,
    timeframe,
)

DESCRIPTION = ("Open interest of the chart's contract as candles, teal when it rose and red when it fell. "
               "Market keeps it in 5-minute points, so below 5 minutes it steps every few bars.")
CATEGORY = 'Volume'

@script.indicator('Open Interest', shorttitle='OI', overlay=False, format=format.volume)
def main(up_color: color.Color = input.color(color.teal, "Up color"),
         down_color: color.Color = input.color(color.red, "Down color")):
    oi_open, oi_high, oi_low, oi_close, rising = request.security(
        syminfo.tickerid + "_OI", timeframe.period, (open, high, low, close, close > close[1]))
    shade = up_color if rising else down_color
    plotcandle(oi_open, oi_high, oi_low, oi_close, "Open Interest", color=shade, wickcolor=shade, bordercolor=shade)
