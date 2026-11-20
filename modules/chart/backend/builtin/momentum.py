"""@pyne"""
from pynecore.lib import close, hline, input, plot, script, ta

DESCRIPTION = 'Trailing close-to-close return over a configurable bar window.'
CATEGORY = 'Oscillators'

@script.indicator('Momentum', shorttitle='Mom', overlay=False)
def main(period: int = input.int(20, 'Period', minval=1)):
    plot(ta.roc(close, period) / 100, "momentum")
    hline(0.0, "zero")
