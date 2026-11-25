"""@pyne"""
from pynecore import Series
from pynecore.lib import close, color, hline, input, plot, script, ta

DESCRIPTION = 'Moving Average Convergence Divergence (MACD): the difference of two EMAs, its EMA signal line and their histogram.'
CATEGORY = 'Oscillators'

@script.indicator('MACD', shorttitle='MACD', overlay=False)
def main(fast: int = input.int(12, "Fast length", minval=1), slow: int = input.int(26, "Slow length", minval=1), signal: int = input.int(9, "Signal smoothing", minval=1),
         grow_above: color.Color = input.color(color.rgb(38, 166, 154), "Above Grow", group="Histogram"),
         fall_above: color.Color = input.color(color.rgb(178, 223, 219), "Above Fall", group="Histogram"),
         grow_below: color.Color = input.color(color.rgb(255, 205, 210), "Below Grow", group="Histogram"),
         fall_below: color.Color = input.color(color.rgb(255, 82, 82), "Below Fall", group="Histogram")):
    value, average, delta = ta.macd(close, fast, slow, signal)
    histogram: Series[float] = delta
    rising = histogram > histogram[1]
    shade = (grow_above if rising else fall_above) if histogram >= 0 else (grow_below if rising else fall_below)
    hline(0.0, "Zero Line", color=color.new(color.rgb(120, 123, 134), 50))
    plot(histogram, "hist", style=plot.style_columns, color=shade)
    plot(value, "macd", color=color.rgb(41, 98, 255))
    plot(average, "signal", color=color.rgb(255, 109, 0))
