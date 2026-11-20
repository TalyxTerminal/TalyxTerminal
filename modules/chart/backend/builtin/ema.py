"""@pyne"""
from pynecore.lib import close, color, input, plot, script, ta

DESCRIPTION = 'Exponential Moving Average (EMA).'
CATEGORY = 'Moving Averages'

@script.indicator('Moving Average Exponential', shorttitle='EMA', overlay=True)
def main(period: int = input.int(20, "Period", minval=1)):
    plot(ta.ema(close, period), "ema", color=color.rgb(33, 150, 243))
