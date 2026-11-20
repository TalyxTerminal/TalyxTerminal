"""Market's bar columns in Chart's wire units, and the price format of a series."""
from __future__ import annotations

FIELDS = ("time", "closeTime", "open", "high", "low", "close", "volume")


def columns(market: dict) -> dict:
    """Market BarColumns (ns and decimal strings) as millisecond integers and floats."""
    return {
        "time": [int(value) // 1_000_000 for value in market["time"]],
        "closeTime": [int(value) // 1_000_000 for value in market["closeTime"]],
        **{name: [float(value) for value in market[name]] for name in ("open", "high", "low", "close")},
        "volume": [None if value is None else float(value) for value in market["volume"]],
    }


def row(market: dict) -> dict:
    """One Market bar row (a session ``update``) in wire units."""
    return {"time": int(market["time"]) // 1_000_000, "closeTime": int(market["closeTime"]) // 1_000_000,
            **{name: float(market[name]) for name in ("open", "high", "low", "close")},
            "volume": None if market.get("volume") is None else float(market["volume"])}


def decimals(text: str) -> int:
    whole, _, fraction = text.partition(".")
    return len(fraction.rstrip("0"))


def price_format(increment: str | None, prices: list[str]) -> dict:
    """Market's price increment when it has one (adjusted prices too); otherwise the most
    decimals the data shows."""
    if increment is not None:
        places = decimals(increment)
        return {"precision": places, "minMove": float(increment)}
    places = max((decimals(value) for value in prices), default=2)
    return {"precision": places, "minMove": 10 ** -places}
