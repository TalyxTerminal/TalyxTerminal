"""@pyne"""
from pynecore.lib import color, fill, high, input, low, plot, script, ta

DESCRIPTION = 'Trailing highest-high and lowest-low channel.'
CATEGORY = 'Volatility'

@script.indicator('Donchian Channels', shorttitle='DC', overlay=True)
def main(period: int = input.int(20, 'Period', minval=1)):
    upper = ta.highest(high, period)
    lower = ta.lowest(low, period)
    top = plot(upper, "upper", color=color.rgb(41, 98, 255))
    plot((upper + lower) / 2, "middle", color=color.rgb(255, 109, 0))
    bottom = plot(lower, "lower", color=color.rgb(41, 98, 255))
    fill(top, bottom, color=color.rgb(33, 150, 243, 95), title="Background")
