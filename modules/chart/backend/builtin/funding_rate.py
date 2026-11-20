"""@pyne"""
from pynecore.lib import close, color, hline, plot, request, script, syminfo, timeframe

DESCRIPTION = "Settled funding rate of the chart's perpetual contract in percent, from each settlement on."
CATEGORY = 'Volume'

@script.indicator('Funding Rate', shorttitle='Funding', overlay=False, precision=4)
def main():
    rate = request.security(syminfo.tickerid + "_FR", timeframe.period, close) * 100
    plot(rate, "Funding rate", style=plot.style_columns, color=color.teal if rate >= 0 else color.red)
    hline(0.0, "Zero", color=color.rgb(120, 123, 134), linestyle=hline.style_dotted)
