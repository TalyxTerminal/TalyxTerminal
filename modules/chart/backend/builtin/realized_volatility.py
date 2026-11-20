"""@pyne"""
from pynecore import Series
from pynecore.lib import close, input, plot, script, ta

DESCRIPTION = 'Trailing per-bar realized volatility of close returns.'
CATEGORY = 'Volatility'

@script.indicator('Realized Volatility', shorttitle='RV', overlay=False)
def main(period: int = input.int(20, 'Period', minval=2)):
    returns: Series[float] = close / close[1] - 1
    plot(ta.stdev(returns, period, biased=True), "realized_volatility")
