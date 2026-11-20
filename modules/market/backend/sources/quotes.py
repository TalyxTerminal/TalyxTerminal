"""Building the contract's Quote from source values (decimal strings, nanosecond times)."""

from __future__ import annotations

import math

import numpy as np

# Numbers keep this many significant digits: a price of 0.000000001234 keeps all four, and the binary noise of a
# float sum or ratio (0.30000000000000004) falls away. Differences of prices are taken in Decimal where they are
# formed, so cancellation noise never reaches this point.
SIGNIFICANT = 12


def decimal(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return value if math.isfinite(number) else None
    number = float(value)
    if not math.isfinite(number):
        return None
    return np.format_float_positional(number, precision=SIGNIFICANT, fractional=False, trim="-")


def make_quote(*, price=None, last=None, bid=None, ask=None, mark=None, index=None, change=None,
               change_percent=None, basis=None, as_of=None, delayed=False, field_as_of=None) -> dict:
    return {"price": decimal(price if price is not None else last), "last": decimal(last), "bid": decimal(bid),
            "ask": decimal(ask), "mark": decimal(mark), "index": decimal(index), "change": decimal(change),
            "changePercent": decimal(change_percent), "changeBasis": basis,
            "asOf": None if as_of is None else str(int(as_of)), "fieldAsOf": field_as_of or {},
            "delayed": bool(delayed)}
