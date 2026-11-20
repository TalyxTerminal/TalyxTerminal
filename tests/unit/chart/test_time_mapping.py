"""Done criterion 3 (backend half): the shared fixture the frontend's test reads too."""
import json
from pathlib import Path

from modules.chart.backend.timemap import TimeMap, line_value

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "fixtures/chart/time_mapping.json").read_text("utf-8"))


def timemap(case):
    return TimeMap(case["bars"]["time"], case["bars"]["closeTime"], case["future"]["time"],
                   case["future"]["closeTime"], case["coveredUntil"])


def test_trendline_values_skip_weekends_and_holidays():
    case = FIXTURE["daily"]
    mapping = timemap(case)
    for expected in case["values"]:
        assert line_value(case["trendline"], mapping, expected["time"]) == expected["value"]


def test_a_daily_anchor_on_the_hourly_chart():
    case = FIXTURE["hourly"]
    mapping = timemap(case)
    for anchor in case["anchors"]:
        assert mapping.index(anchor["time"]) == anchor["index"], anchor["about"]
