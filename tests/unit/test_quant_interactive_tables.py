from decimal import Decimal

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend.result_tables import read_result_table


def test_linked_date_group_and_projected_sort(tmp_path):
    path = tmp_path / "observations.parquet"
    pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02"], utc=True),
                  "asset": ["A", "B", "C"], "group": [1, 2, 1], "return": [.2, .1, .3]}).to_parquet(path)
    result = read_result_table(path, {"filters": [{"column":"date", "operator":"eq", "value":"2026-01-01T00:00:00Z"},
                                                 {"column":"group", "operator":"in", "value":[1,2]}],
                                      "sort":{"column":"return"}, "columns":["asset"], "limit":1})
    assert result["rows"] == [{"asset":"B"}]
    assert result["total"] == 2 and result["nextOffset"] == 1
    result = read_result_table(path, {"filters":[{"column":"date", "operator":"gte", "value":"2026-01-02T00:00:00Z"}]})
    assert result["rows"][0]["asset"] == "C"


def test_exact_identifiers_decimal_and_missing(tmp_path):
    path = tmp_path / "precise.parquet"
    pd.DataFrame({"clock":[1700000000000000123], "price":[Decimal("100.000000000000123")], "sensitivity":[1e-15], "missing":[None]}).to_parquet(path)
    row = read_result_table(path,{})["rows"][0]
    assert {key: row[key] for key in ("clock", "price", "sensitivity", "missing")} == {"clock":"1700000000000000123", "price":"100.000000000000123", "sensitivity":1e-15, "missing":None}
    assert row["priceNumeric"] == float(Decimal("100.000000000000123"))


def test_filter_rejects_non_column_and_nan(tmp_path):
    path = tmp_path / "data.parquet"
    pd.DataFrame({"value":[1]}).to_parquet(path)
    for column,value in [("missing",1),("value",float("nan"))]:
        with pytest.raises(ContractError):
            read_result_table(path,{"filters":[{"column":column,"operator":"eq","value":value}]})


def test_sorted_pages_keep_equal_values_in_stable_source_order(tmp_path):
    path=tmp_path / "large.parquet"
    pd.DataFrame({"group":[i % 2 for i in range(20000)],"row":range(20000)}).to_parquet(path)
    result=read_result_table(path,{"sort":{"column":"group"},"columns":["row"],"offset":3,"limit":3})
    assert result["rows"] == [{"row":6},{"row":8},{"row":10}]
