import pandas as pd
import pytest

from modules.quant.native.model_training import validate_return_target_boundaries


def dataset(train_end, target="Ref($close, -3) / Ref($close, -1) - 1"):
    return {"handler":{"kwargs":{"label":[target]}},"segments":{"train":["2024-01-01",train_end],"valid":["2024-01-10","2024-01-15"],"test":["2024-01-19","2024-01-20"]}}


def test_target_gap_uses_observations_and_rejects_heldout_leakage():
    calendar=pd.date_range("2024-01-01",periods=20)
    validate_return_target_boundaries(dataset("2024-01-06"),calendar)
    with pytest.raises(ValueError, match="Move train end to 2024-01-06"):
        validate_return_target_boundaries(dataset("2024-01-07"),calendar)


def test_custom_target_is_not_guessed_as_return_or_reinterpreted():
    validate_return_target_boundaries(dataset("2024-01-09","$close * 2"),pd.date_range("2024-01-01",periods=20))


def test_loader_pair_and_non_trading_dates_use_exact_calendar_positions():
    value=dataset("2024-01-05")
    value["handler"]["kwargs"]={"data_loader":{"kwargs":{"config":{"label":[["Ref($close, -3) / Ref($close, -1) - 1"],["target"]]}}}}
    with pytest.raises(ValueError,match="reach into valid"):
        validate_return_target_boundaries(value,pd.bdate_range("2024-01-01",periods=20))
