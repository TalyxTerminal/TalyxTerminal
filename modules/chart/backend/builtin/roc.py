"""@pyne"""
from pynecore.lib import close, hline, input, plot, script, ta

DESCRIPTION = 'Percentage rate of change from a prior close.'
CATEGORY = 'Oscillators'

@script.indicator('Rate Of Change', shorttitle='ROC', overlay=False)
def main(period: int = input.int(9, 'Period', minval=1)):
    plot(ta.roc(close, period), "roc")
    hline(0.0, "zero")
