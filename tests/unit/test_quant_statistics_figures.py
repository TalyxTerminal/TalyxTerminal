import numpy as np
import pandas as pd
import pytest

pytest.importorskip("statsmodels.api")
import statsmodels.api as sm
from native.publish import Publisher
from native.statistics_figures import regression_figures


def test_regression_native_influence_partial_and_ccpr_are_all_published(tmp_path):
    rng = np.random.default_rng(23)
    x = pd.DataFrame(rng.normal(size=(80, 2)), columns=["market", "size"])
    design = sm.add_constant(x)
    model = sm.OLS(.5 + .8 * x.market + .2 * x["size"] + rng.normal(size=80), design).fit()
    publisher = Publisher({"runId": "regression", "output": str(tmp_path)})
    artifacts = regression_figures(publisher, model)
    charts = [item for item in artifacts if item["kind"] == "svg"]
    assert len(charts) == 6
    assert {item["name"] for item in charts} == {"diagnostics/" + name + ".svg" for name in ["influence-plot", "leverage-resid2", "partial-regression-1", "partial-regression-2", "ccpr-1", "ccpr-2"]}
    assert all(item["chapter"] == "influence" for item in charts)
    assert not publisher.index.get("nativeWarnings")
    assert len([item for item in artifacts if item["kind"] == "table"]) == 6

