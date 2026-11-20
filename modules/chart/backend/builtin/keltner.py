"""@pyne"""
from pynecore.lib import close, color, fill, input, plot, script, ta

DESCRIPTION = 'EMA envelope using a causal Wilder average true range.'
CATEGORY = 'Volatility'

@script.indicator('Keltner Channels', shorttitle='KC', overlay=True)
def main(ema_period: int = input.int(20, 'Ema Period', minval=1), atr_period: int = input.int(10, 'Atr Period', minval=1), multiplier: float = input.float(2.0, 'Multiplier', minval=0.0)):
    middle = ta.ema(close, ema_period)
    width = ta.atr(atr_period) * multiplier
    top = plot(middle + width, "upper", color=color.rgb(41, 98, 255))
    plot(middle, "middle", color=color.rgb(41, 98, 255))
    bottom = plot(middle - width, "lower", color=color.rgb(41, 98, 255))
    fill(top, bottom, color=color.rgb(33, 150, 243, 95), title="Background")
