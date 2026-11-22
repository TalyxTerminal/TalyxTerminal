"""A library value as Nautilus data: what a published factor, prediction or weight said about a symbol, and when it could first be known.

`ts_event` is the moment the value is about (the bar it was computed on), `ts_init` the moment it became available. A backtest hands the value
to the strategy at `ts_init`, so the strategy never sees a value earlier than it could have been had; a live node is sent it when the library
produces it. Registering the class is what lets the engine or the node carry it; both call `register`.
"""
from __future__ import annotations

from nautilus_trader.model import CustomData, DataType, register_custom_data_class
from nautilus_trader.model.custom import customdataclass


@customdataclass()
class LibraryValue:
    name: str = ""
    instrument_id: str = ""
    field: str = ""
    value: float = 0.0
    version: int = 0
    version_id: str = ''
    batch_end: bool = False


DATA_TYPE = "LibraryValue"


def register():
    register_custom_data_class(LibraryValue)


def data_type():
    """What a strategy subscribes to (`subscribe_data(data_type())`) to be handed library values."""
    return DataType(DATA_TYPE, None, None)


def as_data(value):
    """The wrapper an engine or a node takes: a strategy's `on_data` receives it and reads the value from `.data`."""
    return CustomData(data_type(), value)


def library_value(name, instrument_id, field, value, version, observed_ns, available_ns, *, version_id='', batch_end=False):
    if available_ns < observed_ns:
        raise ValueError("A value cannot be available before the moment it is about.")
    return LibraryValue(ts_event=observed_ns, ts_init=available_ns, name=name, instrument_id=instrument_id, field=field, value=float(value), version=int(version),
                        version_id=version_id, batch_end=batch_end)


def values(invocation):
    """The library values a backtest hands its strategy, as the table the backend wrote (None when it reads none)."""
    import pandas as pd
    reading = invocation["libraryValues"]
    if reading["path"] is None:
        return None
    register()
    return pd.read_parquet(reading["path"])


def events(invocation, frame):
    """Those of `values` in `frame` as engine data: each arrives at the moment it became available."""
    versions = {(item['name'], item['version']): item['versionId'] for item in invocation["libraryValues"]['items']}
    result = []
    for _, section in frame.groupby(['name', 'ts_init', 'field'], sort=False):
        for index, row in enumerate(section.itertuples()):
            result.append(as_data(library_value(row.name, row.instrument, row.field, row.value, row.version, int(row.ts_event), int(row.ts_init),
                version_id=versions[(row.name, row.version)], batch_end=index == len(section) - 1)))
    return sorted(result, key=lambda data: data.ts_init)
