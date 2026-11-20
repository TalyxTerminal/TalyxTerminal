"""@pyne"""
from pynecore.lib import close, color, fill, input, plot, script, ta

DESCRIPTION = 'Bollinger Bands: a simple moving average with bands at a multiple of the population standard deviation.'
CATEGORY = 'Volatility'

@script.indicator('Bollinger Bands', shorttitle='BB', overlay=True)
def main(period: int = input.int(20, "Period", minval=1), mult: float = input.float(2.0, "Deviation", minval=0)):
    middle, upper, lower = ta.bb(close, period, mult)
    plot(middle, "Basis", color=color.rgb(41, 98, 255))
    p1 = plot(upper, "Upper", color=color.rgb(242, 54, 69))
    p2 = plot(lower, "Lower", color=color.rgb(8, 153, 129))
    fill(p1, p2, color=color.rgb(33, 150, 243, 95), title="Background")
