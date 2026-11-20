"""Exact native event selection and bounded Parquet pages retain their meaning."""
from decimal import Decimal

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend.result_tables import read_result_table


def test_native_small_sensitivities_keep_original_float_values_and_nulls(tmp_path):
    path = tmp_path / "native-greeks.parquet"
    values = [3.084946365055284e-13, -1.2889254512140228e-08, 0.12345678901234566, float("nan")]
    pd.DataFrame({"gamma": values}).to_parquet(path, index=False)
    page = read_result_table(path, {"offset": 0, "limit": 100})
    assert [row["gamma"] for row in page["rows"]] == [*values[:3], None]
    assert page["total"] == 4 and page["columns"] == ["gamma"]


def test_native_order_filter_pages_without_projecting_its_id_column(tmp_path):
    path = tmp_path / "events.parquet"
    pd.DataFrame({"nativeOrderId": ["A", "B", "A", "A"], "ts_event": ["1700000000000000123", "1700000000000000124", "1700000000000000125", "1700000000000000126"], "event": ["accepted", "filled", "updated", "canceled"]}).to_parquet(path)
    request = {"filter": {"column": "nativeOrderId", "equals": "A"}, "columns": ["ts_event", "event"], "limit": 2}
    first = read_result_table(path, request)
    assert first["total"] == 3 and first["nextOffset"] == 2
    assert first["rows"] == [{"ts_event": "1700000000000000123", "event": "accepted"}, {"ts_event": "1700000000000000125", "event": "updated"}]
    assert read_result_table(path, {**request, "offset": 2})["rows"] == [{"ts_event": "1700000000000000126", "event": "canceled"}]
    assert read_result_table(path, {**request, "offset": 10})["rows"] == []


def test_null_filter_and_exact_timestamp_combine(tmp_path):
    path = tmp_path / "observations.parquet"
    pd.DataFrame({"date": pd.to_datetime(["2026-01-01T08:00:00Z", "2026-01-01T08:00:00Z", "2026-01-02T08:00:00Z"]), "target": [None, "A", None], "value": [1, 2, 3]}).to_parquet(path)
    selected = read_result_table(path, {"filter": {"column": "target", "equals": None}, "observation": "2026-01-01T16:00:00+08:00", "columns": ["value"]})
    assert selected["total"] == 1 and selected["rows"] == [{"value": 1}]
    with pytest.raises(ContractError, match="UTC offset"):
        read_result_table(path, {"observation": "2026-01-01T08:00:00"})
    with pytest.raises(ContractError, match="real column"):
        read_result_table(path, {"filter": {"column": "missing", "equals": "A"}})


def test_page_crosses_multiple_native_row_groups_without_loading_whole_table(tmp_path, monkeypatch):
    path = tmp_path / "large.parquet"
    pd.DataFrame({"sequence": range(12000)}).to_parquet(path, row_group_size=3000)
    monkeypatch.setattr(pd, "read_parquet", lambda *_args, **_kwargs: pytest.fail("The fixed table must be scanned in bounded batches"))
    selected = read_result_table(path, {"offset": 2997, "limit": 8})
    assert selected["total"] == 12000 and selected["nextOffset"] == 3005
    assert selected["rows"] == [{"sequence": i} for i in range(2997, 3005)]


def test_large_native_numbers_decimal_amounts_and_nanosecond_dates_keep_safe_exact_text(tmp_path):
    path = tmp_path / "native-values.parquet"
    instant = "2023-11-14T22:13:20.000000123Z"
    pd.DataFrame({"date": pd.to_datetime([instant]), "native_integer": [1700000000000000123],
                  "large_double": [3.4028234663852886e38], "decimal_amount": [Decimal("0.000103062000721434")]}).to_parquet(path, index=False)
    row = read_result_table(path, {"offset": 0, "limit": 1})["rows"][0]
    assert {key: row[key] for key in ("date", "native_integer", "large_double", "decimal_amount")} == {"date": instant, "native_integer": "1700000000000000123", "large_double": "3.4028234663852886e+38", "decimal_amount": "0.000103062000721434"}
    assert row["decimal_amountNumeric"] == .000103062000721434
