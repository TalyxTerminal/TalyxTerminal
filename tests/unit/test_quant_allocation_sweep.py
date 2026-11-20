"""A sweep's weights-move number: the average one-way change in target weights against the neighbouring cells."""
import pytest

from modules.quant.backend.allocation_sweep import distance, weights_move


def at(**weights):
    return {"2020-01-31": weights, "2020-02-29": weights}


def test_distance_is_the_share_of_the_portfolio_that_differs():
    assert distance(at(A=.6, B=.4), at(A=.4, B=.6)) == pytest.approx(20)
    assert distance(at(A=1.0), at(B=1.0)) == pytest.approx(100)
    assert distance(at(A=.5, B=.5), at(A=.5, B=.5)) == 0
    assert distance({"2020-01-31": {"A": 1}}, {"2020-02-29": {"A": 1}}) is None


def test_each_cell_averages_over_its_neighbours_along_rows_and_columns():
    cells = {(0, 0): at(A=1.0), (0, 1): at(A=.8, B=.2), (1, 0): at(A=.5, B=.5), (1, 1): at(A=.5, B=.5)}
    move = weights_move(cells)
    assert move[(0, 0)] == pytest.approx((20 + 50) / 2)       # not the diagonal
    assert move[(1, 1)] == pytest.approx((30 + 0) / 2)
    assert weights_move({(0, 0): at(A=1.0)})[(0, 0)] is None   # no finished neighbour


def test_a_group_cap_is_swept_as_that_groups_at_most():
    """"Stocks at most 30, 40, 50" is a sweep axis: each cell sets the Stocks row's max, adding a row for a group without one."""
    from modules.quant.backend.allocation_runs import group_caps
    params = {"group_limits": [{"group": "Stocks", "min": "", "max": "60", "count": ""}, {"group": "Bonds", "min": "20", "max": "", "count": ""}]}
    found = group_caps(params, {"group_max:Stocks": 40, "lookback": 126})
    assert found["lookback"] == 126 and found["group_limits"][0]["max"] == "40" and found["group_limits"][1] == params["group_limits"][1]
    assert params["group_limits"][0]["max"] == "60"   # the object's own settings are untouched
    added = group_caps(params, {"group_max:Gold": 10})["group_limits"]
    assert added[-1] == {"group": "Gold", "min": "", "max": "10", "count": ""}
    assert group_caps(params, {"max_weight": 25}) == {"max_weight": 25}
