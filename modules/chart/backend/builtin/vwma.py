"""@pyne"""
from pynecore.lib import close, color, input, plot, script, ta

DESCRIPTION = 'Volume-weighted moving average over a trailing bar window.'
CATEGORY = 'Moving Averages'

@script.indicator('Volume Weighted Moving Average', shorttitle='VWMA', overlay=True)
def main(period: int = input.int(20, 'Period', minval=1)):
    plot(ta.vwma(close, period), "vwma", color=color.rgb(41, 98, 255))
