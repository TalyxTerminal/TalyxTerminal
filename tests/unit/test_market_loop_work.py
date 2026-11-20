"""What a batch download must not take from charts (platform build standard 16): its file and parsing work runs on its
own workers, a live bar's adjustment reads no file, and a live row is written as text without Arrow, the same text."""

from __future__ import annotations

import asyncio
import math
import threading
from pathlib import Path

import numpy as np

from modules.market.backend import bars
from modules.market.backend.actions import Actions
from modules.market.backend.routing import INTERACTIVE, JOBS, LIVE, PRIORITY, in_worker
from modules.market.backend.store import DocumentStore, SeriesKey, SeriesStore


def test_fetch_jobs_work_on_their_own_workers():
    async def thread_at(priority: int) -> str:
        token = PRIORITY.set(priority)
        try:
            return await in_worker(lambda: threading.current_thread().name)
        finally:
            PRIORITY.reset(token)

    async def scenario():
        return {priority: await thread_at(priority) for priority in (INTERACTIVE, LIVE, JOBS)}

    names = asyncio.run(scenario())
    assert names[JOBS].startswith("market-jobs")
    assert not names[INTERACTIVE].startswith("market-jobs") and not names[LIVE].startswith("market-jobs")


def test_adjusting_a_live_bar_reads_no_file(tmp_path, monkeypatch):
    store = SeriesStore(tmp_path)
    actions = Actions(DocumentStore(tmp_path, "actions"), store)
    daily = SeriesKey("i1", "ohlcv", "last", "regular", "1d")
    day = 86_400 * 10**9
    store.write(daily, {"time": np.array([0, day], dtype=np.int64), "close_time": np.array([day, 2 * day]),
                        "open": np.array([10.0, 10.0]), "high": np.array([10.0, 10.0]), "low": np.array([10.0, 10.0]),
                        "close": np.array([10.0, 10.0]), "volume": np.array([1.0, 1.0])}, fetched=[(0, 2 * day)])
    actions.write("i1", [{"exDate": str(day), "kind": "cash_dividend", "amount": "1"}], start=0, end=2 * day,
                  source="s", revision=1)
    first = actions.factors("i1", daily)

    def no_file(*_args, **_kwargs):
        raise AssertionError("a file was read")

    monkeypatch.setattr(Path, "read_text", no_file)
    monkeypatch.setattr(store, "read", no_file)
    again = actions.factors("i1", daily)
    assert all(np.array_equal(a, b) for a, b in zip(first, again, strict=True))
    assert again[2].tolist() == [0.9]
    monkeypatch.undo()

    # New actions are seen at once (the store keeps what it wrote) and the factors follow them.
    actions.write("i1", [{"exDate": str(day), "kind": "split", "ratio": "2"}], start=0, end=2 * day, source="s",
                  revision=2)
    assert sorted(row["kind"] for row in actions.read("i1")["rows"]) == ["cash_dividend", "split"]
    assert actions.factors("i1", daily)[1].tolist() == [1.0, 2.0]


def test_one_documents_file_steps_do_not_wait_for_anothers(tmp_path):
    documents = DocumentStore(tmp_path, "actions")
    documents.write("a", {"rows": []})
    documents.write("b", {"rows": [1]})
    with documents._lock("a"):
        done = []
        reader = threading.Thread(target=lambda: done.append(documents.read("b")))
        reader.start()
        reader.join(2)
    assert done == [{"rows": [1]}]


def test_a_live_row_is_written_as_the_same_text_without_arrow():
    rng = np.random.default_rng(7)
    values = np.concatenate([rng.normal(0, 1, 500) * 10.0 ** rng.integers(-12, 20, 500),
                             [0.0, -0.0, 0.1, 60000.0, 1e16, 1e22, 1.5e-5, 3e-10, math.nan, math.inf]])
    times = np.array([0, 1, 2**62, 1_794_856_501_735_000_000], dtype=np.int64)
    for value in values:
        assert bars._short_text(np.array([value])) == bars._column_text(np.array([value]), False)
    assert bars._short_text(times) == bars._column_text(times, False)
    row = {"time": times[:1], "open": values[:1], "close": values[-2:-1]}
    assert bars.to_wire(row) == {"time": ["0"], "open": bars._column_text(values[:1], False), "close": [None]}
