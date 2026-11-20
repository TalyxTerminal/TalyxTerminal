"""@pyne"""
from pynecore.lib import high, hline, input, low, math, na, plot, script, ta

DESCRIPTION = 'Log-scaled true-range concentration over a trailing window.'
CATEGORY = 'Volatility'

@script.indicator('Choppiness Index', shorttitle='CHOP', overlay=False)
def main(period: int = input.int(14, 'Period', minval=2)):
    range_total = ta.sma(ta.tr(True), period) * period
    price_range = ta.highest(high, period) - ta.lowest(low, period)
    ratio = range_total / price_range if price_range > 0 else na
    plot(100 * math.log10(ratio) / math.log10(period) if ratio > 0 else na, "choppiness")
    hline(38.2, "trend")
    hline(61.8, "chop")
