from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qlib")
from native.prediction_figures import feature_importance, position_figures
from native.publish import Publisher


def test_feature_names_survive_native_dataset_cache_stripping(tmp_path):
    import lightgbm as lgb
    from qlib.contrib.model.gbdt import LGBModel
    rng = np.random.default_rng(42)
    x = rng.normal(size=(80, 2))
    model = LGBModel()
    model.model = lgb.train({"objective":"regression", "verbosity":-1, "num_threads":1, "min_data_in_leaf":5},
                            lgb.Dataset(x, label=x[:, 0] + rng.normal(size=80) * .1), num_boost_round=5)
    def stripped(**kwargs):
        raise AttributeError("_infer")
    dataset = SimpleNamespace(handler=SimpleNamespace(get_cols=stripped, data_loader=SimpleNamespace(fields={"feature": (["$close", "$volume"], ["close_feature", "volume_feature"])})))
    class Recorder:
        def load_object(self, name, **kwargs):
            assert kwargs["trusted"] is True
            return model if name == "params.pkl" else dataset
    publisher = Publisher({"runId":"importance", "output":str(tmp_path)})
    errors = []
    artifacts = feature_importance(publisher, Recorder(), ["params.pkl", "dataset"], "result", errors)
    assert not errors
    assert len([item for item in artifacts if item["kind"] == "plotly"]) == 2
    values = pd.read_parquet(tmp_path / "result/feature-importance-gain.parquet")
    assert values.feature.tolist() == ["close_feature", "volume_feature"]
    np.testing.assert_allclose(values.importance, model.model.feature_importance(importance_type="gain"))


def test_position_charts_use_provider_day_frequency_and_native_position_objects(tmp_path, monkeypatch):
    from qlib.backtest.position import Position
    from qlib.data import D
    dates = pd.date_range("2025-01-01", periods=6)
    positions = {date: Position(cash=1000, position_dict={"A": {"amount":10, "price":100, "weight":.5}, "B":{"amount":20, "price":50, "weight":.5}}) for date in dates}
    report = pd.DataFrame({"bench": [.001] * len(dates)}, index=dates)
    labels = pd.DataFrame({"label":np.linspace(-.02,.02,len(dates)*2)}, index=pd.MultiIndex.from_product([["A","B"],dates], names=["instrument","datetime"]))
    def features(assets, fields, start, end, *, freq):
        assert freq == "day" and assets == ["A", "B"]
        return labels.copy()
    monkeypatch.setattr(D, "features", features, raising=False)
    class Recorder:
        def load_object(self, name, **kwargs):
            return positions if "positions_normal" in name else report
    publisher = Publisher({"runId":"positions", "output":str(tmp_path)})
    errors = []
    artifacts = position_figures(publisher, Recorder(), ["portfolio_analysis/positions_normal_1day.pkl"], "result", errors)
    assert not errors
    charts = [item for item in artifacts if item["kind"] == "plotly"]
    assert len(charts) == 7
    assert len([item for item in charts if item["chapter"] == "quality"]) == 3
