"""Long columns of text handed over already encoded.

A page of 10,000 rows is tens of thousands of strings. Made one by one in Python, checked one by one and written one by
one, they cost more than everything else in the call. A ``TextColumn`` is the whole column as one JSON array, built in
one step (from an Arrow string array, say); a field declared ``text_column(item)`` accepts it, checks it against
``item``'s pattern in one pass over the text, and the wire writes it as it is. The declared type, and so the contract,
is the plain ``list[item]``; a plain list is accepted there too and checked item by item. The saving is for outputs
handed over as plain dicts: a model holding a column dumps it as a list.

    class Bars(Model):
        time: text_column(TimestampNs)
        volume: text_column(Decimal, nullable=True)

    Bars(time=TextColumn.from_arrow(times), volume=TextColumn.from_arrow(volumes))
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any

import orjson
from pydantic import Field, TypeAdapter, ValidationError, WrapSerializer, WrapValidator

_NULL = "\x00"


class TextColumn:
    """One column of strings (or nulls) as the text of a JSON array."""

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

    @classmethod
    def from_arrow(cls, array: Any) -> TextColumn:
        """From an Arrow string array; nulls become JSON null."""
        import pyarrow as pa
        import pyarrow.compute as pc

        count = len(array)
        if count == 0:
            return cls("[]")
        nulls = array.null_count
        if pc.any(pc.match_substring_regex(array, r'["\\\x00-\x1f]')).as_py():  # needs escaping: item by item
            return cls(orjson.dumps(array.to_pylist()).decode())
        if nulls:
            array = pc.fill_null(array, _NULL)
        whole = pa.ListArray.from_arrays(pa.array([0, count], pa.int32()), array)
        text = '["' + pc.binary_join(whole, '","')[0].as_py() + '"]'
        if nulls:
            text = text.replace(f'"{_NULL}"', "null")
        return cls(text)

    def to_list(self) -> list:
        return orjson.loads(self.text)

    def __len__(self) -> int:
        return len(self.to_list())

    def __repr__(self) -> str:
        return f"TextColumn({self.text[:40]}...)"


def _plain_pattern(pattern: str) -> bool:
    """True when the regular expression can only match letters, digits and a few plain punctuation marks."""
    rest = re.sub(r"\\[.\-+]", "", pattern)  # escaped literals
    if "\\" in rest or "." in rest:
        return False

    def plain_class(match: re.Match) -> str:
        inside = re.sub(r"0-9|[0-9]-[0-9]|[a-z]-[a-z]|[A-Z]-[A-Z]", "", match.group(1))
        return "" if not inside.startswith("^") and "-" not in inside.strip("-") else "\x00"

    rest = re.sub(r"\[([^\]]*)\]", plain_class, rest)
    return re.fullmatch(r"[A-Za-z0-9^$()|?*+{},:_\- ]*", rest) is not None


def text_column(item: Any, *, nullable: bool = False) -> Any:
    """``list[item]`` (``list[item | None]`` when ``nullable``) that also takes a ``TextColumn``. ``item`` must be a
    string type with an anchored pattern (``^...$``)."""
    schema = TypeAdapter(item).json_schema()
    if "$ref" in schema:
        schema = schema["$defs"][schema["$ref"].rsplit("/", 1)[1]]
    pattern = schema.get("pattern", "")
    if schema.get("type") != "string" or not (pattern.startswith("^") and pattern.endswith("$")):
        raise ValueError(f"text_column needs a string type with an anchored pattern, not {schema}")
    # A pattern that can only match plain characters (no quote, backslash or control character): then the one
    # expression over the whole text already proves it is a JSON array of such strings. Any other pattern could match
    # across the quotes between items, so the items are read and checked one by one.
    plain = _plain_pattern(pattern)
    one = f'"(?:{pattern[1:-1]})"' + ("|null" if nullable else "")
    whole = TypeAdapter(Annotated[str, Field(pattern=rf"^\[(?:(?:{one})(?:,(?:{one}))*)?\]$")])
    listed = list[item | None] if nullable else list[item]

    def validate(value: Any, handler: Callable) -> Any:
        if not isinstance(value, TextColumn):
            return handler(value)
        text = value.text
        if plain:
            try:
                whole.validate_python(text)
                return value
            except ValidationError:
                pass  # the exact error below
        try:
            items = orjson.loads(text)
        except orjson.JSONDecodeError as exc:
            raise ValueError(f"The column is not a JSON array: {exc}") from exc
        handler(items)  # the exact error, if any
        return value

    def serialize(value: Any, handler: Callable) -> Any:
        # A model dumped gives the plain list; only a column in a plain output goes to the wire as it is.
        return value.to_list() if isinstance(value, TextColumn) else handler(value)

    return Annotated[listed, WrapValidator(validate), WrapSerializer(serialize, return_type=listed)]
