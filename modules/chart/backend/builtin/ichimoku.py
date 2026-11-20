"""@pyne"""
from pynecore.lib import close, color, fill, high, input, low, plot, script, ta

DESCRIPTION = 'Unshifted causal Ichimoku values with independent plot offsets.'
CATEGORY = 'Trend'

@script.indicator('Ichimoku Cloud', shorttitle='Ichimoku', overlay=True)
def main(conversion_period: int = input.int(9, 'Conversion Period', minval=1), base_period: int = input.int(26, 'Base Period', minval=1), span_b_period: int = input.int(52, 'Span B Period', minval=1), displacement: int = input.int(26, 'Lagging Span', minval=1)):
    tenkan = (ta.highest(high, conversion_period) + ta.lowest(low, conversion_period)) / 2
    kijun = (ta.highest(high, base_period) + ta.lowest(low, base_period)) / 2
    span_b = (ta.highest(high, span_b_period) + ta.lowest(low, span_b_period)) / 2
    span_a = (tenkan + kijun) / 2
    plot(tenkan, "tenkan", color=color.rgb(41, 98, 255))
    plot(kijun, "kijun", color=color.rgb(183, 28, 28))
    lead_a = plot(span_a, "senkou_a", offset=displacement - 1, color=color.rgb(165, 214, 167))
    lead_b = plot(span_b, "senkou_b", offset=displacement - 1, color=color.rgb(239, 154, 154))
    plot(close, "chikou", offset=-displacement + 1, color=color.rgb(67, 160, 71))
    fill(lead_a, lead_b, color=color.rgb(67, 160, 71, 90) if span_a > span_b else color.rgb(244, 67, 54, 90))
