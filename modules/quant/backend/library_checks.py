"""The frozen bars as the library's figures read them (`library_after`): closes by bar label and instrument id."""
from __future__ import annotations


def close_prices(bars, instruments):
    """The frozen bars as a table of bar labels by instrument ids."""
    frame = bars.pivot(index="label", columns="column", values="close").sort_index()
    return frame.rename(columns=instruments)
