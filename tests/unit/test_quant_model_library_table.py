"""A library version's values exported for a model run: read as a table, the same file as the row-by-row reading gave (the first field, the names in the data,
availability moved to the bar when what was known by then counts), and quickly."""
from __future__ import annotations

import time

import pandas as pd

from modules.quant.backend import model_prep as P
from tests.unit.quant_library_fakes import (
    DAY,
    INFO,
    NAMES,
    build,
    close_of,
    factor_spec,
    factor_value,
)


def old_table(library, version_id, info, known_by=None):
    """What `library_table` wrote before: the values as rows (dicts), one at a time."""
    columns = {item["instrumentId"]: item["column"] for item in info["instruments"]}
    rows = library.store.read_values(version_id, start=info["since"], end=info["until"])
    table = pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value", "availableAt", "recordedAt", "mode"])
    if not table.empty:
        first = sorted(set(table["field"]))[0]
        table = table[(table["field"] == first) & table["instrument"].isin(columns)]
    available = table["availableAt"].astype("int64")
    if known_by is not None and not table.empty:
        available = available.where(available > known_by, available.clip(upper=table["datetime"].astype("int64")))
    return pd.DataFrame({"label": table["datetime"].astype("int64"), "column": table["instrument"].map(columns), "value": table["value"].astype(float), "availableAt": available})


def test_the_exported_table_is_what_reading_row_by_row_gave(tmp_path):
    world = build(tmp_path)
    spec = factor_spec("Two fields", "v1", 40)
    rows = [(name, "zeta", day * DAY, factor_value("z", day, name), close_of(day) + 5 * DAY, close_of(day), "history") for day in range(1, 41) for name in NAMES]
    rows += [(name, "alpha", day * DAY, factor_value("a", day, name), close_of(day), close_of(day), "history") for day in range(1, 41) for name in NAMES]
    rows += [("i:Z", "alpha", day * DAY, 1.0, close_of(day), close_of(day), "history") for day in range(1, 41)]  # a name that is not in this data
    spec["rows"] = rows
    world.store.commit_versions([spec])
    info = {**INFO, "since": 3 * DAY, "until": 30 * DAY}
    for known_by in (None, close_of(20)):
        path = tmp_path / f"i-{known_by}.parquet"
        P.library_table(world.library, "v1", info, path, known_by)
        got = pd.read_parquet(path)
        expected = old_table(world.library, "v1", info, known_by).reset_index(drop=True)
        pd.testing.assert_frame_equal(got.reset_index(drop=True), expected)
        assert set(got["column"]) == {name[2:] for name in NAMES} and got["label"].min() >= 3 * DAY and got["label"].max() <= 30 * DAY
    assert len(got) == 28 * len(NAMES), "the first field alone, the names of the data, the period asked for"


def test_a_big_version_is_exported_in_a_second_or_two(tmp_path):
    world = build(tmp_path)
    spec = factor_spec("Big", "v2", 60)
    spec["rows"] = [(name, "value", day * DAY, 0.5, close_of(day), close_of(day), "history") for day in range(1, 3001) for name in NAMES for _ in range(1)]
    spec["rows"] = spec["rows"][:200_000]
    world.store.commit_versions([spec])
    info = {**INFO, "since": DAY, "until": 3000 * DAY}
    started = time.perf_counter()
    P.library_table(world.library, "v2", info, tmp_path / "big.parquet")
    assert time.perf_counter() - started < 2.5
