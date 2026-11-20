"""@pyne"""
from pynecore.lib import close, hline, input, plot, script, ta

DESCRIPTION = 'Shared Wilder RSI centered around zero.'
CATEGORY = 'Oscillators'

@script.indicator('Centered RSI', shorttitle='CRSI', overlay=False)
def main(period: int = input.int(14, 'Period', minval=1)):
    plot(ta.rsi(close, period) / 50 - 1, "rsi_centered")
    hline(0.0, "neutral")
