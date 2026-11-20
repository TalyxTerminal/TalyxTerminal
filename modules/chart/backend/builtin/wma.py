"""@pyne"""
from pynecore.lib import close, color, input, plot, script, ta

DESCRIPTION = 'Linearly weighted moving average of closing price.'
CATEGORY = 'Moving Averages'

@script.indicator('Moving Average Weighted', shorttitle='WMA', overlay=True)
def main(period: int = input.int(9, 'Period', minval=1)):
    plot(ta.wma(close, period), "wma", color=color.rgb(33, 150, 243))
