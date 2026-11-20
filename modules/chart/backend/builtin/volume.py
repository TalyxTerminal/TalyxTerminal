"""@pyne"""
from pynecore.lib import close, color, format, open, plot, scale, script, volume

DESCRIPTION = 'Volume of each bar.'
CATEGORY = 'Volume'

# TradingView's Volume: on the price pane at its bottom, on a scale of its own that the pane does not show.
@script.indicator('Volume', shorttitle='Vol', overlay=True, scale=scale.none, format=format.volume)
def main():
    up = close >= open
    plot(volume, "volume", style=plot.style_columns,
         color=color.new(color.rgb(38, 166, 154), 50) if up else color.new(color.rgb(239, 83, 80), 50))
