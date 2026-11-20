"""@pyne"""
from pynecore.lib import close, color, fill, hline, input, plot, script, ta

DESCRIPTION = 'Relative Strength Index (RSI): Wilder-smoothed momentum oscillator.'
CATEGORY = 'Oscillators'

@script.indicator('Relative Strength Index', shorttitle='RSI', overlay=False)
def main(period: int = input.int(14, "Period", minval=1)):
    plot(ta.rsi(close, period), "rsi", color=color.rgb(126, 87, 194))
    upper = hline(70.0, "Upper Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    hline(50.0, "Middle Band", color=color.new(color.rgb(120, 123, 134), 50), linestyle=hline.style_dashed)
    lower = hline(30.0, "Lower Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    fill(upper, lower, color=color.new(color.rgb(126, 87, 194), 90))
