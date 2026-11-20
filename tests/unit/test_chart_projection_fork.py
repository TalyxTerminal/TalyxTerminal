"""The forming bar's branch preserves causal and atomic behavior for all six projections."""

import pytest

from modules.chart.backend import projection

KINDS = ("heikin_ashi", "renko", "kagi", "point_figure", "line_break", "range")


def bar(index, close, volume=5.0):
    time = index * 60_000
    return (time, time + 60_000, close - 1, close + 2, close - 3, close, volume)


def columns(rows):
    return [list(column) for column in zip(*rows, strict=True)]


def spec_of(kind):
    return projection.ProjectionSpec(kind=kind, box_size=None if kind in ("heikin_ashi", "line_break", "range") else 1,
                                     range_ticks=2, minimum_tick=1)


@pytest.mark.parametrize("kind", KINDS)
def test_seed_matches_bar_by_bar_closes_and_subsequent_live_updates(kind):
    rows = [bar(index, 100 + index % 4 * 3) for index in range(240)]
    rows[100] = bar(100, 100 + 100 % 4 * 3, None)
    seeded, stepped = projection.CausalBarProjector(spec_of(kind)), projection.CausalBarProjector(spec_of(kind))
    seeded.seed(columns(rows))
    for row in rows:
        stepped.update(row, True)
    assert seeded.snapshot() == stepped.snapshot()
    for row, closed in [
        (bar(240, 109), False),
        (bar(240, 112), False),
        (bar(240, 112), True),
        (bar(240, 108), True),
    ]:
        assert seeded.update(row, closed) == stepped.update(row, closed)


@pytest.mark.parametrize(
    "invalid",
    [
        [bar(1, 100), bar(0, 101)],
        [bar(0, 100), bar(0, 101)],
        [(0, 60_000, 99, 90, 97, 100, 5.0)],
        [bar(0, 100, -1.0)],
        [bar(0, float("nan"))],
    ],
)
def test_seed_rejects_an_invalid_window_without_replacing_accepted_state(invalid):
    projector = projection.CausalBarProjector(spec_of("heikin_ashi"))
    projector.seed(columns([bar(0, 100)]))
    before = projector.snapshot()
    with pytest.raises(projection.BarProjectionError):
        projector.seed(columns(invalid))
    assert projector.snapshot() == before


@pytest.mark.parametrize("kind", KINDS)
def test_the_forming_branch_matches_a_fresh_projection_across_forming_closure_and_revision(kind):
    rows = [bar(index, 100 + index % 4 * 3) for index in range(12)]
    actual = projection.CausalBarProjector(spec_of(kind))
    actual.seed(columns(rows))
    confirmed_rows = list(rows)
    shown = list(actual.snapshot())
    observations = [
        (bar(12, 105), False),
        (bar(12, 109), False),
        (bar(12, 102), False),
        (bar(12, 109), True),
        (bar(12, 107), True),  # Market revised the bar it closed
        (bar(13, 100), False),
        (bar(13, 100), True),
    ]
    for row, closed in observations:
        initial = actual.snapshot()[:actual.confirmed]
        start, bars = actual.update(row, closed)
        shown[start:] = bars  # the splice gives what a fresh projection shows
        if closed:
            confirmed_rows = [*confirmed_rows[:-1], row] if row[0] == confirmed_rows[-1][0] else [*confirmed_rows, row]
        reference = projection.CausalBarProjector(spec_of(kind))
        reference.seed(columns(confirmed_rows))
        if not closed:
            reference.update(row, False)
        assert tuple(shown) == actual.snapshot() == reference.snapshot()
        assert actual.confirmed == reference.confirmed
        if not closed:  # a forming bar never rewrites what was confirmed before it
            assert actual.snapshot()[:actual.confirmed] == initial[:actual.confirmed]


def test_a_failed_update_leaves_confirmed_and_forming_state_unchanged(monkeypatch):
    projector = projection.CausalBarProjector(spec_of("heikin_ashi"))
    projector.seed(columns([bar(0, 100), bar(1, 101)]))
    before = projector.snapshot()
    monkeypatch.setattr(projection, "MAX_PROJECTED_BARS", 2)
    for closed in (False, True):
        with pytest.raises(projection.BarProjectionLimitError):
            projector.update(bar(2, 102), closed)
        assert projector.snapshot() == before
    monkeypatch.setattr(projection, "MAX_PROJECTED_BARS", 3)
    projector.update(bar(2, 102), False)
    forming = projector.snapshot()
    monkeypatch.setattr(projection, "MAX_PROJECTED_BARS", 2)
    with pytest.raises(projection.BarProjectionLimitError):
        projector.update(bar(2, 103), False)
    assert projector.snapshot() == forming and projector.confirmed == 2


def test_one_raw_bar_may_not_cross_unbounded_boxes():
    projector = projection.CausalBarProjector(projection.ProjectionSpec(kind="renko", box_size=0.001))
    projector.seed(columns([bar(0, 100)]))
    with pytest.raises(projection.BarProjectionLimitError):
        projector.update(bar(1, 200), True)
