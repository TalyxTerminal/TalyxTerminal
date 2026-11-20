"""@pyne"""
from pynecore import Series
from pynecore.lib import close, color, fill, hline, input, plot, script, ta

DESCRIPTION = 'Stochastic oscillator of causal Wilder RSI.'
CATEGORY = 'Oscillators'

@script.indicator('Stochastic RSI', shorttitle='Stoch RSI', overlay=False)
def main(rsi_period: int = input.int(14, 'Rsi Period', minval=1), stoch_period: int = input.int(14, 'Stoch Period', minval=1), smooth_k: int = input.int(3, 'Smooth K', minval=1), smooth_d: int = input.int(3, 'Smooth D', minval=1)):
    strength: Series[float] = ta.rsi(close, rsi_period)
    raw = ta.stoch(strength, strength, strength, stoch_period)
    k = ta.sma(raw, smooth_k)
    plot(k, "k", color=color.rgb(41, 98, 255))
    plot(ta.sma(k, smooth_d), "d", color=color.rgb(255, 109, 0))
    upper = hline(80.0, "Upper Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    hline(50.0, "Middle Band", color=color.new(color.rgb(120, 123, 134), 50), linestyle=hline.style_dashed)
    lower = hline(20.0, "Lower Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    fill(upper, lower, color=color.rgb(33, 150, 243, 90))
