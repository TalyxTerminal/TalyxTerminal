"""@pyne"""
from pynecore.lib import color, fill, hlc3, hline, input, plot, script, ta

DESCRIPTION = 'Volume-weighted money flow index.'
CATEGORY = 'Volume'

@script.indicator('Money Flow Index', shorttitle='MFI', overlay=False)
def main(period: int = input.int(14, 'Period', minval=1)):
    plot(ta.mfi(hlc3, period), "mfi", color=color.rgb(126, 87, 194))
    upper = hline(80.0, "Upper Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    hline(50.0, "Middle Band", color=color.new(color.rgb(120, 123, 134), 50), linestyle=hline.style_dashed)
    lower = hline(20.0, "Lower Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    fill(upper, lower, color=color.new(color.rgb(126, 87, 194), 90))
