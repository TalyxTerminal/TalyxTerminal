"""@pyne"""
from pynecore.lib import close, hline, input, na, plot, script, ta

DESCRIPTION = 'Close distance from its trailing simple moving average.'
CATEGORY = 'Trend'

@script.indicator('Trend Distance', shorttitle='TD', overlay=False)
def main(period: int = input.int(20, 'Period', minval=1)):
    average = ta.sma(close, period)
    plot(close / average - 1 if average != 0 else na, "trend_distance")
    hline(0.0, "average")
