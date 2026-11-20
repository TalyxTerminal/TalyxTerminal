"""Brick rules with concrete prices (TradingView's definitions), close as the source."""

from modules.chart.backend import projection
from modules.chart.backend.projection import CLOSE, HIGH, LOW, OPEN


def project(kind, closes, **settings):
    rows = [(index * 60_000, (index + 1) * 60_000, close, close, close, close, 1.0) for index, close in enumerate(closes)]
    projector = projection.CausalBarProjector(projection.ProjectionSpec(kind=kind, **settings))
    projector.seed([list(column) for column in zip(*rows, strict=True)])
    return [(bar[OPEN], bar[HIGH], bar[LOW], bar[CLOSE]) for bar in projector.snapshot()]


def test_every_range_bar_spans_the_range_from_low_to_high():
    assert project("range", [100, 96, 108, 95, 80], box_size=10) == [
        (100, 106, 96, 106),  # dipped to 96 first: the bar is 96..106 and closes at its top
        (106, 108, 98, 98),
        (98, 98, 88, 88),
    ]


def test_kagi_reverses_by_box_size_times_reversal_boxes():
    assert project("kagi", [100, 110, 85, 79, 105, 109], box_size=10, reversal_boxes=3) == [
        (100, 110, 100, 110),  # 85 is 25 below the shoulder: not yet a reversal of 30
        (110, 110, 79, 79),
        (79, 109, 79, 109),
    ]
