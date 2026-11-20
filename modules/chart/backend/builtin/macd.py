"""@pyne"""
from pynecore import Series
from pynecore.lib import close, color, hline, input, plot, script, ta

DESCRIPTION = 'Moving Average Convergence Divergence (MACD): the difference of two EMAs, its EMA signal line and their histogram.'
CATEGORY = 'Oscillators'

@script.indicator('MACD', shorttitle='MACD', overlay=False)
def main(fast: int = input.int(12, "Fast length", minval=1), slow: int = input.int(26, "Slow length", minval=1), signal: int = input.int(9, "Signal smoothing", minval=1)):
    value, average, delta = ta.macd(close, fast, slow, signal)
    histogram: Series[float] = delta
    rising = histogram > histogram[1]
    shade = (color.rgb(38, 166, 154) if rising else color.rgb(178, 223, 219)) if histogram >= 0 else \
        (color.rgb(255, 205, 210) if rising else color.rgb(255, 82, 82))
    hline(0.0, "Zero Line", color=color.new(color.rgb(120, 123, 134), 50))
    plot(histogram, "hist", style=plot.style_columns, color=shade)
    plot(value, "macd", color=color.rgb(41, 98, 255))
    plot(average, "signal", color=color.rgb(255, 109, 0))
