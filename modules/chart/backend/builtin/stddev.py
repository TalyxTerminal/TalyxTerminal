"""@pyne"""
from pynecore.lib import close, input, plot, script, ta

DESCRIPTION = 'Population standard deviation of trailing closing prices.'
CATEGORY = 'Volatility'

@script.indicator('Standard Deviation', shorttitle='StdDev', overlay=False)
def main(period: int = input.int(20, 'Period', minval=1)):
    plot(ta.stdev(close, period, biased=True), "stddev")
