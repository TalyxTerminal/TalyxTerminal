"""@pyne"""
from pynecore.lib import bar_index, input, plot, script, ta

DESCRIPTION = 'Traditional pivot levels from the previous completed fixed-size bar period.'
CATEGORY = 'Trend'

@script.indicator('Pivot Points Standard', shorttitle='Pivots', overlay=True)
def main(period_bars: int = input.int(1, 'Period Bars', minval=1)):
    levels = ta.pivot_point_levels("Traditional", bar_index % period_bars == 0)
    plot(levels[0], "pivot")
    plot(levels[1], "r1")
    plot(levels[3], "r2")
    plot(levels[5], "r3")
    plot(levels[2], "s1")
    plot(levels[4], "s2")
    plot(levels[6], "s3")
