"""A reader that took a series' pointer reads the parts it names to the end, while a write replaces them: the old part
is deleted after the last such reader is done, not before."""

from __future__ import annotations

import threading
from unittest.mock import patch

import numpy as np
import pytest

from modules.market.backend.store import SeriesKey, SeriesStore

DAY = 86_400 * 10**9
KEY = SeriesKey("vi2_" + "1" * 32, "ohlcv", "last", "regular", "1d")


def rows(days: list[int], price: float) -> dict:
    times = np.asarray([day * DAY for day in days], dtype=np.int64)
    values = np.full(len(days), price)
    return {"time": times, "close_time": times + DAY, "open": values, "high": values, "low": values, "close": values,
            "volume": values}


def test_interrupted_backfill_keeps_rows_and_coverage_at_the_same_revision(tmp_path):
    store = SeriesStore(tmp_path)
    committed = store.write(KEY, rows([20_001], 11.0), fetched=[(20_001 * DAY, 20_002 * DAY)], source="test")
    older_span = (20_000 * DAY, 20_001 * DAY)
    # All new Parquet parts have been written, but shutdown interrupts publication of their pointer.
    with patch("modules.market.backend.store.os.replace", side_effect=InterruptedError), pytest.raises(InterruptedError):
        store.write(KEY, rows([20_000], 10.0), fetched=[older_span], source="test")

    reopened = SeriesStore(tmp_path)
    assert reopened.pointer(KEY) == committed
    assert reopened.read(KEY)["close"].tolist() == [11.0]
    retried = reopened.write(KEY, rows([20_000], 10.0), fetched=[older_span], source="test")
    assert retried["revision"] == committed["revision"] + 1
    assert retried["spans"] == [[20_000 * DAY, 20_002 * DAY, "have"]]
    assert reopened.read(KEY)["close"].tolist() == [10.0, 11.0]


def test_a_reader_with_the_old_pointer_reads_its_part_while_a_write_replaces_it(tmp_path):
    store = SeriesStore(tmp_path)
    store.write(KEY, rows([20_000, 20_001], 10.0), source="test")
    old_part = store.directory(KEY) / store.pointer(KEY)["parts"]["all"]
    took_pointer, written = threading.Event(), threading.Event()
    table = store._table

    def held(path):                      # the reader has the old pointer and is about to open its part
        took_pointer.set()
        written.wait(10)
        return table(path)

    store._table = held
    result = {}
    reader = threading.Thread(target=lambda: result.update(store.read(KEY)))
    reader.start()
    assert took_pointer.wait(10)
    store.write(KEY, rows([20_002], 11.0), source="test")      # a new part replaces the old one
    assert old_part.exists()
    written.set()
    reader.join(10)
    assert result["close"].tolist() == [10.0, 10.0]
    assert not old_part.exists()                                # the last reader done, it is gone
    store._table = table
    assert store.read(KEY)["close"].tolist() == [10.0, 10.0, 11.0]
