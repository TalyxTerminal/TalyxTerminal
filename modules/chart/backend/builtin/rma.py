"""@pyne"""
from pynecore.lib import close, input, plot, script, ta

DESCRIPTION = 'Native Wilder moving average with SMA initialization and PyneCore missing-value handling.'
CATEGORY = 'Moving Averages'

@script.indicator('Smoothed Moving Average', shorttitle='SMMA', overlay=True)
def main(period: int = input.int(9, 'Period', minval=1)):
    plot(ta.rma(close, period), "rma")
