"""@pyne"""
from pynecore.lib import color, format, plot, script, ta

DESCRIPTION = 'Cumulative signed volume from close direction.'
CATEGORY = 'Volume'

@script.indicator('On Balance Volume', shorttitle='OBV', overlay=False, format=format.volume)
def main():
    plot(ta.obv, "obv", color=color.rgb(41, 98, 255))
