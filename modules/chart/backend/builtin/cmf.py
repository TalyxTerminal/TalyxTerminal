"""@pyne"""
from pynecore.lib import close, high, hline, input, low, na, plot, script, ta, volume

DESCRIPTION = 'Trailing close-location volume divided by trailing volume.'
CATEGORY = 'Volume'

@script.indicator('Chaikin Money Flow', shorttitle='CMF', overlay=False)
def main(period: int = input.int(20, 'Period', minval=1)):
    location = ((close - low) - (high - close)) / (high - low) if high != low else 0.0
    weighted = ta.sma(location * volume, period)
    mean_volume = ta.sma(volume, period)
    plot(weighted / mean_volume if mean_volume > 0 else na, "cmf")
    hline(0.0, "zero")
