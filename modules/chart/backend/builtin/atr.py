"""@pyne"""
from pynecore.lib import color, input, plot, script, ta

DESCRIPTION = 'Average True Range (ATR): Wilder-smoothed true range.'
CATEGORY = 'Volatility'

@script.indicator('Average True Range', shorttitle='ATR', overlay=False)
def main(period: int = input.int(14, "Period", minval=1)):
    plot(ta.atr(period), "atr", color=color.rgb(183, 28, 28))
