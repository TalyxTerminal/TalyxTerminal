"""@pyne"""
from pynecore.lib import color, fill, hlc3, hline, input, plot, script, ta

DESCRIPTION = 'Commodity channel index of typical price.'
CATEGORY = 'Oscillators'

@script.indicator('Commodity Channel Index', shorttitle='CCI', overlay=False)
def main(period: int = input.int(20, 'Period', minval=1)):
    plot(ta.cci(hlc3, period), "cci", color=color.rgb(41, 98, 255))
    upper = hline(100.0, "Upper Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    hline(0.0, "Middle Band", color=color.new(color.rgb(120, 123, 134), 50), linestyle=hline.style_dashed)
    lower = hline(-100.0, "Lower Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    fill(upper, lower, color=color.rgb(33, 150, 243, 90))
