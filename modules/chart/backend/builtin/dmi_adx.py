"""@pyne"""
from pynecore.lib import color, input, plot, script, ta

DESCRIPTION = 'Directional movement lines and Wilder average directional index.'
CATEGORY = 'Trend'

@script.indicator('Directional Movement Index', shorttitle='DMI', overlay=False)
def main(di_period: int = input.int(14, 'Di Period', minval=1), adx_smoothing: int = input.int(14, 'Adx Smoothing', minval=1)):
    plus, minus, strength = ta.dmi(di_period, adx_smoothing)
    plot(plus, "plus_di", color=color.rgb(41, 98, 255))
    plot(minus, "minus_di", color=color.rgb(255, 109, 0))
    plot(strength, "adx", color=color.rgb(245, 0, 87))
