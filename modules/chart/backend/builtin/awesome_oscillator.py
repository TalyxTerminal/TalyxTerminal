"""@pyne"""
from pynecore.lib import hl2, hline, input, plot, script, ta

DESCRIPTION = 'Fast-minus-slow midpoint simple moving average.'
CATEGORY = 'Oscillators'

@script.indicator('Awesome Oscillator', shorttitle='AO', overlay=False)
def main(fast_period: int = input.int(5, 'Fast Period', minval=1), slow_period: int = input.int(34, 'Slow Period', minval=2)):
    if fast_period >= slow_period:
        raise ValueError("Fast period must be below slow period")
    plot(ta.sma(hl2, fast_period) - ta.sma(hl2, slow_period), "awesome_oscillator")
    hline(0.0, "zero")
