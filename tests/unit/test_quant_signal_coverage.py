"""a strategy started from a library factor scored on part of an index says, with both numbers, how many names a day the
factor has values for against the names the run's data holds."""
from __future__ import annotations

from modules.quant.backend.library_inputs import project_values
from modules.quant.backend.strategy_views import signal_coverage

DAY = 86_400_000


def test_the_input_records_how_many_names_it_has_a_value_for_on_a_typical_day():
    # Three days: 2, 3 and 2 names have a value; the typical (median) day has 2.
    rows = [(day * DAY, name, "value", 1.0, day * DAY, day * DAY, "backfill")
            for day, names in ((1, "AB"), (2, "ABC"), (3, "BC")) for name in names]
    known = {name: f"{name}.SIM" for name in "ABC"}
    item = {"version": 1, "versionId": "v1", "state": "published"}
    _, detail = project_values(rows, known, [], item, "signal", "Momentum", DAY, 4 * DAY, DAY, None)
    assert detail["perDay"] == 2 and detail["instruments"] == 3


def test_the_run_says_when_the_input_covers_fewer_names_than_its_data_holds():
    record = {"data": {"traded": 500, "library": [{"name": "Momentum", "version": 3, "perDay": 182}]}}
    assert signal_coverage(record) == ["Momentum v3 has values for about 182 names a day; the run's data holds 500 names over the period, "
                                       "and on a day a name has no value it has none of this input to act on"]
    assert signal_coverage({"data": {"traded": 5, "library": [{"name": "X", "perDay": 5}]}}) == []
    assert signal_coverage({"data": {"traded": 5, "library": [{"name": "X"}]}}) == []
