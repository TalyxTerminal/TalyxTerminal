"""The table explorer a notebook shows for a DataFrame, as the kernel's startup code builds it (run here without a kernel)."""
from pathlib import Path

import pandas as pd

SOURCE = Path(__file__).resolve().parents[2] / "modules" / "quant" / "native" / "kernel_startup.py"


def table_of(frame):
    namespace = {}
    exec(SOURCE.read_text(encoding="utf-8").replace("\n_install()\n", "\n"), namespace)  # noqa: S102 - the kernel's own startup code, without hooking a shell
    return namespace["_table"](frame)["application/vnd.talyx.table+json"]


def test_a_date_index_is_shown_as_the_first_column_and_a_plain_one_is_not():
    closes = pd.DataFrame({"GLD": [355.78, 361.65], "IEF": [91.76, 91.5]}, index=pd.DatetimeIndex(["2026-11-04", "2026-11-05"], name="datetime"))
    table = table_of(closes)
    assert table["shape"] == [2, 2], "the size is the frame's, as pandas says it"
    assert [(column["name"], column["type"]) for column in table["columns"]] == [("datetime", "date"), ("GLD", "float"), ("IEF", "float")]
    assert table["rows"][0][0].startswith("2026-11-04") and table["rows"][0][1:] == [355.78, 91.76]
    plain = table_of(pd.DataFrame({"a": [1, 2]}))
    assert [column["name"] for column in plain["columns"]] == ["a"] and plain["rows"] == [[1], [2]]


def test_an_index_named_like_a_column_and_an_unnamed_text_index_are_both_kept():
    clash = table_of(pd.DataFrame({"a": [1.0, 2.0]}, index=pd.Index([10, 20], name="a")))
    assert [(column["name"], column["type"]) for column in clash["columns"]] == [("a", "int"), ("a", "float")] and clash["rows"] == [[10, 1.0], [20, 2.0]]
    named = table_of(pd.Series([0.5, 0.7], index=["AAPL", "MSFT"]).to_frame("weight"))
    assert [column["name"] for column in named["columns"]] == ["index", "weight"] and named["rows"][1] == ["MSFT", 0.7]
