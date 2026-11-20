"""@pyne"""
from pynecore.lib import close, color, input, plot, script, ta

DESCRIPTION = 'Hull moving average composed from causal weighted averages.'
CATEGORY = 'Moving Averages'

@script.indicator('Hull Moving Average', shorttitle='HMA', overlay=True)
def main(period: int = input.int(9, 'Period', minval=1)):
    plot(ta.hma(close, period), "hma", color=color.rgb(33, 150, 243))
