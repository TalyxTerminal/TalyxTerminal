"""@pyne"""
from pynecore import Series
from pynecore.lib import close, high, hline, input, low, na, plot, script, ta

DESCRIPTION = 'Weighted buying pressure over three trailing ranges.'
CATEGORY = 'Oscillators'

@script.indicator('Ultimate Oscillator', shorttitle='UO', overlay=False)
def main(short_period: int = input.int(7, 'Short Period', minval=1), medium_period: int = input.int(14, 'Medium Period', minval=2), long_period: int = input.int(28, 'Long Period', minval=3)):
    if not short_period < medium_period < long_period:
        raise ValueError("Periods must increase from short to long")
    previous: Series[float] = close[1]
    pressure: Series[float] = close - min(low, previous)
    range_value: Series[float] = max(high, previous) - min(low, previous)
    short_pressure = ta.sma(pressure, short_period)
    short_range = ta.sma(range_value, short_period)
    medium_pressure = ta.sma(pressure, medium_period)
    medium_range = ta.sma(range_value, medium_period)
    long_pressure = ta.sma(pressure, long_period)
    long_range = ta.sma(range_value, long_period)
    value = 100 / 7 * (4 * short_pressure / short_range + 2 * medium_pressure / medium_range + long_pressure / long_range) if short_range > 0 and medium_range > 0 and long_range > 0 else na
    plot(value, "ultimate_oscillator")
    hline(30.0, "oversold")
    hline(70.0, "overbought")
