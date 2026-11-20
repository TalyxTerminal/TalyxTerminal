"""Reference series use the public Market series contract and preserve native clock alignment."""
import pandas as pd
import pytest
from talyx_sdk import ContractError
from test_quant_market2_inputs import long_file, selection, stock

from modules.quant.backend import market_inputs as M
from modules.quant.backend import reference_prices as R


def test_reference_series_are_explicit_and_join_by_close_time(tmp_path):
    instrument = stock()
    series = [M.price_series(instrument, "1d", False, price_basis=basis) for basis in ("last", "mark", "index")]
    assert len({item["seriesId"] for item in series}) == 3
    assert all(item["adjustment"] == "raw" for item in series)
    path = tmp_path / "mark.parquet"
    long_file(path, [instrument], [97.])
    selected = {**selection(instrument, path), "series": series[1]}
    source = {"marketInstrument": instrument, "nativeInstrument": "A"}
    frame = pd.DataFrame({"instrument": ["A"], "ts_event": ["1704229200000000000"]})
    result = R.project([source], frame, [selected])
    assert result.mark_close.tolist() == ["97"]
    assert "mark_close" not in frame
    frame["ts_event"] = ["1704229200000000001"]
    with pytest.raises(ContractError, match="missing MARK"):
        R.project([source], frame, [selected])
