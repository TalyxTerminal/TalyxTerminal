"""@pyne"""
from pynecore.lib import color, fill, hline, input, plot, script, ta

DESCRIPTION = 'Williams percent range oscillator.'
CATEGORY = 'Oscillators'

@script.indicator('Williams %R', shorttitle='%R', overlay=False)
def main(period: int = input.int(14, 'Period', minval=1)):
    plot(ta.wpr(period), "williams_r")
    upper = hline(-20.0, "Upper Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    hline(-50.0, "Middle Band", color=color.new(color.rgb(120, 123, 134), 50), linestyle=hline.style_dotted)
    lower = hline(-80.0, "Lower Band", color=color.rgb(120, 123, 134), linestyle=hline.style_dashed)
    fill(upper, lower, color=color.new(color.rgb(126, 87, 194), 90))
