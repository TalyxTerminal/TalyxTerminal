"""@pyne"""
from pynecore.lib import input, plot, script, ta

DESCRIPTION = 'Iterative parabolic stop-and-reverse points.'
CATEGORY = 'Trend'

@script.indicator('Parabolic SAR', shorttitle='SAR', overlay=True)
def main(start: float = input.float(0.02, 'Start', minval=0.0, maxval=1.0), increment: float = input.float(0.02, 'Increment', minval=0.0, maxval=1.0), maximum: float = input.float(0.2, 'Maximum', minval=0.0, maxval=1.0)):
    if start > maximum or increment > maximum:
        raise ValueError("SAR start and increment must not exceed maximum")
    plot(ta.sar(start, increment, maximum), "psar", style=plot.style_circles)
