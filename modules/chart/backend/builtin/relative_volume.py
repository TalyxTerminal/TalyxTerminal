"""@pyne"""
from pynecore.lib import hline, input, na, plot, script, ta, volume

DESCRIPTION = 'Volume relative to its trailing median.'
CATEGORY = 'Volume'

@script.indicator('Relative Volume', shorttitle='RVOL', overlay=False)
def main(period: int = input.int(20, 'Period', minval=1)):
    median = ta.median(volume, period)
    plot(volume / median - 1 if median != 0 else na, "relative_volume")
    hline(0.0, "median")
