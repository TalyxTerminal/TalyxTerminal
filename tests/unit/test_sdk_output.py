"""What a module sends is what it declared: a value of another type is refused as ``invalid_output`` (neither it nor
a converted copy goes out), and no float the receiver would read as null (NaN, infinity) leaves the sender, whatever
the declared type holds it in."""

from __future__ import annotations

from datetime import date
from typing import TypedDict

import orjson
import pytest
from talyx_sdk.declare import Model
from talyx_sdk.errors import ContractError
from talyx_sdk.process import Codec

NAN, INF = float("nan"), float("inf")


class Counted(Model):
    count: int
    ready: bool


class Point(Model):
    x: float


class Row(TypedDict):
    v: float


def refused(annotation, value) -> None:
    with pytest.raises(ContractError) as error:
        Codec(annotation).check(value)
    assert error.value.code == "invalid_output", (annotation, value)


@pytest.mark.parametrize(("annotation", "value"), [
    (Counted, {"count": "7", "ready": "false"}),
    (Counted, {"count": 7.0, "ready": 1}),
    (int, "7"),
    (bool, "false"),
    (list[int], ["1", 2]),
    (dict[str, int], {"a": "1"}),
    (date, "3 June 2024"),
    (date, 1717372800),
])
def test_a_value_of_another_type_is_refused_not_converted(annotation, value):
    refused(annotation, value)


@pytest.mark.parametrize(("annotation", "value"), [
    (float, NAN),
    (float, INF),
    (float | None, NAN),
    (list[float], [1.0, NAN]),
    (dict[str, float], {"a": -INF}),
    (list[Row], [{"v": NAN}]),
    (Point, {"x": NAN}),
    (list[Point], [{"x": INF}]),
    (dict, {"a": [NAN]}),
])
def test_no_float_that_would_go_out_as_null_leaves(annotation, value):
    refused(annotation, value)


def test_what_matches_goes_out_as_it_is():
    assert orjson.loads(Codec(Counted).check({"count": 7, "ready": False})) == {"count": 7, "ready": False}
    assert orjson.loads(Codec(Counted).check(Counted(count=7, ready=True))) == {"count": 7, "ready": True}
    assert orjson.loads(Codec(list[float]).check([1.0, 2, 3.5])) == [1.0, 2, 3.5]
    assert orjson.loads(Codec(float | None).check(None)) is None
    # A date is a string on the wire: the string the declaration's JSON form is goes out as it is.
    assert Codec(date).check("2024-06-03") == Codec(date).check(date(2024, 6, 3)) == b'"2024-06-03"'
