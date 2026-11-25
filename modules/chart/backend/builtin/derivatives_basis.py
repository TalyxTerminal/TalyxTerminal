"""@pyne"""
from pynecore.lib import (
    close,
    color,
    display,
    hline,
    input,
    plot,
    request,
    script,
    syminfo,
    timeframe,
)

DESCRIPTION = ("Derivatives Basis: a futures or perpetual contract's mark price minus its index price, in percent "
               "of the index, from the venue's mark and index prices; on venues where Market has both.")
CATEGORY = 'Volume'

@script.indicator('Derivatives Basis', shorttitle='Basis', overlay=False, precision=4)
def main(positive: color.Color = input.color(color.teal, "Positive color"),
         negative: color.Color = input.color(color.red, "Negative color")):
    mark = request.security(syminfo.tickerid + "_MARK", timeframe.period, close)
    index = request.security(syminfo.tickerid + "_INDEX", timeframe.period, close)
    basis = 100 * (mark - index) / index
    plot(basis, "Basis %", style=plot.style_columns, color=positive if basis >= 0 else negative)
    plot(mark - index, "Basis", display=display.data_window)
    plot(mark, "Mark", display=display.data_window)
    plot(index, "Index", display=display.data_window)
    hline(0.0, "Zero", color=color.rgb(120, 123, 134), linestyle=hline.style_dotted)
