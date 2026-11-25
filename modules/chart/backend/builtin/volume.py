"""@pyne"""
from pynecore.lib import close, color, format, input, open, plot, scale, script, volume

DESCRIPTION = 'Volume of each bar.'
CATEGORY = 'Volume'

# TradingView's Volume: on the price pane at its bottom, on a scale of its own that the pane does not show.
@script.indicator('Volume', shorttitle='Vol', overlay=True, scale=scale.none, format=format.volume)
def main(up_color: color.Color = input.color(color.new(color.rgb(38, 166, 154), 50), "Up color"),
         down_color: color.Color = input.color(color.new(color.rgb(239, 83, 80), 50), "Down color")):
    up = close >= open
    plot(volume, "volume", style=plot.style_columns, color=up_color if up else down_color)
