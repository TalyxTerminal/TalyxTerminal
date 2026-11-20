"""@pyne"""
from pynecore.lib import close, color, fill, high, hline, input, low, plot, script, ta

DESCRIPTION = 'Stochastic oscillator: %K and its moving average %D.'
CATEGORY = 'Oscillators'

@script.indicator('Stochastic', shorttitle='Stoch', overlay=False)
def main(k: int = input.int(14, "K length", minval=1), d: int = input.int(3, "D smoothing", minval=1)):
    value = ta.stoch(close, high, low, k)
    plot(value, "k", color=color.rgb(41, 98, 255))
    plot(ta.sma(value, d), "d", color=color.rgb(255, 109, 0))
    upper = hline(80.0, "Upper Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    hline(50.0, "Middle Band", color=color.new(color.rgb(120, 123, 134), 50), linestyle=hline.style_dashed)
    lower = hline(20.0, "Lower Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    fill(upper, lower, color=color.rgb(33, 150, 243, 90))
