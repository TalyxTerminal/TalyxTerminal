"""Columns of text handed over already encoded (talyx_sdk.columns): checked like the plain list, written as they are."""

from __future__ import annotations

from typing import Annotated

import orjson
import pyarrow as pa
import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from talyx_sdk import wire
from talyx_sdk.columns import TextColumn, text_column

Number = Annotated[str, Field(pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")]
Word = Annotated[str, Field(pattern=r"^.+$")]  # not plain: any character, so items are read one by one


class Row(BaseModel):
    price: text_column(Number)
    volume: text_column(Number, nullable=True)
    note: text_column(Word)


def column(*items):
    return TextColumn.from_arrow(pa.array(items, pa.string()))


def test_a_column_is_checked_in_one_pass_and_written_as_it_is():
    row = Row(price=column("1.5", "-2", "30"), volume=column("1", None, "3"), note=column('say "hi"', "x"))
    assert row.volume.text == '["1",null,"3"]'
    output = {"price": row.price, "volume": row.volume, "note": row.note}
    expected = {"price": ["1.5", "-2", "30"], "volume": ["1", None, "3"], "note": ['say "hi"', "x"]}
    assert orjson.loads(wire.encode(output)) == expected
    assert row.model_dump() == row.model_dump(mode="json") == expected  # a model dumps it as the plain list


@pytest.mark.parametrize("field, value", [
    ("price", column("1.5", "1e5")), ("price", column("1", None)), ("price", TextColumn('["1"')),
    ("note", column("x", "")),
])
def test_a_wrong_item_is_refused_as_in_a_plain_list(field, value):
    good = {"price": column("1"), "volume": column("1"), "note": column("x")}
    with pytest.raises(ValidationError):
        Row(**{**good, field: value})


def test_the_contract_is_the_plain_list():
    assert TypeAdapter(text_column(Number)).json_schema() == TypeAdapter(list[Number]).json_schema()
    assert Row(price=["1"], volume=[None], note=["x"]).price == ["1"]


def test_an_empty_column():
    assert Row(price=column(), volume=column(), note=column()).price.text == "[]"
