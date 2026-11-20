"""@pyne"""
from pynecore.lib import color, display, fill, hlc3, input, plot, script, ta, time

DESCRIPTION = 'Volume Weighted Average Price of the typical price, reset at the start of each trading day in the exchange time zone (UTC midnight for crypto).'
CATEGORY = 'Volume'

@script.indicator('VWAP', shorttitle='VWAP', overlay=True)
def main(band_1: bool = input.bool(True, 'Bands #1'), mult_1: float = input.float(1.0, 'Bands Multiplier #1', minval=0, step=0.5),
         band_2: bool = input.bool(False, 'Bands #2'), mult_2: float = input.float(2.0, 'Bands Multiplier #2', minval=0, step=0.5),
         band_3: bool = input.bool(False, 'Bands #3'), mult_3: float = input.float(3.0, 'Bands Multiplier #3', minval=0, step=0.5)):
    value, upper, _ = ta.vwap(hlc3, ta.change(time("D")) != 0, 1.0)
    deviation = upper - value
    plot(value, "vwap", color=color.rgb(41, 98, 255))
    # TradingView's standard deviation bands: the first on, in green; the second olive and the third teal, off.
    look_1 = display.all if band_1 else display.none
    top_1 = plot(value + deviation * mult_1, "Upper Band #1", color=color.green, display=look_1)
    bottom_1 = plot(value - deviation * mult_1, "Lower Band #1", color=color.green, display=look_1)
    fill(top_1, bottom_1, color=color.new(color.green, 95), title="Bands Fill #1", display=look_1)
    look_2 = display.all if band_2 else display.none
    top_2 = plot(value + deviation * mult_2, "Upper Band #2", color=color.olive, display=look_2)
    bottom_2 = plot(value - deviation * mult_2, "Lower Band #2", color=color.olive, display=look_2)
    fill(top_2, bottom_2, color=color.new(color.olive, 95), title="Bands Fill #2", display=look_2)
    look_3 = display.all if band_3 else display.none
    top_3 = plot(value + deviation * mult_3, "Upper Band #3", color=color.teal, display=look_3)
    bottom_3 = plot(value - deviation * mult_3, "Lower Band #3", color=color.teal, display=look_3)
    fill(top_3, bottom_3, color=color.new(color.teal, 95), title="Bands Fill #3", display=look_3)
