"""@pyne"""
from pynecore.lib import close, color, input, plot, script, ta

DESCRIPTION = "Average closing price over a rolling window."
CATEGORY = 'Moving Averages'

@script.indicator('Moving Average Simple', shorttitle='SMA', overlay=True)
def main(period: int = input.int(20, "Period", minval=1)):
    plot(ta.sma(close, period), "sma", color=color.rgb(33, 150, 243))
