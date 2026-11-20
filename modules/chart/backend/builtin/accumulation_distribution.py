"""@pyne"""
from pynecore.lib import hline, plot, script, ta

DESCRIPTION = 'Cumulative close-location volume (A/D line).'
CATEGORY = 'Volume'

@script.indicator('Accumulation/Distribution', shorttitle='Accum/Dist', overlay=False)
def main():
    plot(ta.accdist, "accumulation_distribution")
    hline(0.0, "origin")
