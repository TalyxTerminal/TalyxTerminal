"""@pyne"""
from pynecore.lib import close, color, display, fill, input, na, open, plot, script, ta

DESCRIPTION = 'ATR trailing trend line that switches between bullish and bearish bands.'
CATEGORY = 'Trend'

@script.indicator('Supertrend', shorttitle='Supertrend', overlay=True)
def main(atr_period: int = input.int(10, 'Atr Period', minval=1), multiplier: float = input.float(3.0, 'Multiplier', minval=0.0)):
    value, direction = ta.supertrend(multiplier, atr_period)
    # TradingView draws the trend as two broken lines over a shaded body; the joined line stays as a value.
    plot(value, "supertrend", display=display.none)
    up = plot(value if direction < 0 else na, "Up Trend", color=color.green, style=plot.style_linebr)
    down = plot(na if direction < 0 else value, "Down Trend", color=color.red, style=plot.style_linebr)
    body = plot((open + close) / 2, "Body Middle", display=display.none)
    fill(body, up, color.new(color.green, 90), title="Uptrend background")
    fill(body, down, color.new(color.red, 90), title="Downtrend background")
