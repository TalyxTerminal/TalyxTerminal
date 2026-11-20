import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from modules.quant.backend import chart_layers as layers
from modules.quant.backend.public_settings import milliseconds


def test_displayed_contributions_add_to_account_pnl_without_assigning_fx_to_a_name():
    # Six positions with fractional-cent marks. The account records net P&L in cents.
    rows = [{"pnl": p} for p in (12865.6975, 9513.1357, 8088.3643, 1297.3929, -3273.10, -3278.4242)]
    assert layers.contribution_amounts(rows, 25213.06) == {"net": 25213.06, "remainder": 0}
    assert sum(round(row["pnl"] * 100) for row in rows) == 2_521_306
    rows = [{"pnl": 40.12}, {"pnl": 30.04}]
    assert layers.contribution_amounts(rows, 72.16)["remainder"] == 2.0
    assert rows == [{"pnl": 40.12}, {"pnl": 30.04}]


def test_nonfinite_values_do_not_affect_percentiles():
    values = np.array([[-np.inf, 2, 4, np.inf], [2, 2, 2, np.nan]])
    np.testing.assert_equal(layers.rank_percentile(values, 1), [0, 50])


def test_window_uses_actual_bars_and_centres_an_earlier_fill():
    opens = np.arange(240_000, dtype="int64") * 60_000
    end = int(opens[-1] + 59_999)
    assert layers.window(opens, end, None) == (2_400_000_000, end)
    assert layers.window(opens, end, "1970-01-02T00:00:00Z") == (0, 11_999_999_999)
    assert layers.window(opens, end, layers.timestamp(120_000 * 60_000)) == (
        1_200_000_000,
        13_199_999_999,
    )


HOUR = 3_600_000


# A US daily bar opens at local midnight (04:00Z) and is known at the close (20:00Z). A crypto daily bar opens at
# 00:00Z and closes at the next bar's open: its value stays on its own day, the last one included.
@pytest.mark.parametrize("kind", ["factor", "model"])
@pytest.mark.parametrize(
    ("opens", "known", "first", "drawn"),
    [
        (4 * HOUR, 20 * HOUR, "2025-06-02T04:00:00Z", "2025-06-02T20:00:00Z"),
        (0, 24 * HOUR, "2025-06-02T00:00:00Z", "2025-06-02T23:59:59.999000Z"),
    ],
)
def test_signal_uses_its_known_time_and_cross_section(kind, opens, known, first, drawn, tmp_path):
    run = tmp_path / ("qmodels" if kind == "model" else "qruns") / "r" / "output"
    (run / "cross").mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    labels = [milliseconds(f"2025-06-0{d}") for d in (2, 3, 4)]
    columns = ["A", "B", "C", "D"]
    instruments = [
        {
            "column": c,
            "instrumentId": "id-" + c,
            "displaySymbol": c,
            "series": {"adjustment": "split"},
        }
        for c in columns
    ]
    (data / "info.json").write_text(json.dumps({"instruments": instruments, "timeframe": "1d"}))
    pd.DataFrame(
        [
            {"column": c, "label": t, "time": t + opens, "knownAt": t + known}
            for c in columns
            for t in labels
        ]
    ).to_parquet(data / "bars.parquet")
    values = np.array(
        [[2.0, 2.0, 6.0, np.nan], [np.nan, 4.0, np.nan, np.nan], [5.0, 3.0, 8.0, 1.0]]
    )
    path = run / ("cross/meta.json" if kind == "factor" else "pred.json")
    path.write_text(json.dumps({"labels": labels, "columns": columns}))
    np.save(run / ("values.npy" if kind == "factor" else "pred.npy"), values)
    if kind == "factor":
        np.save(run / "cross/missing.npy", np.zeros_like(values))
    services = SimpleNamespace(
        runs=SimpleNamespace(
            records=SimpleNamespace(root=tmp_path), directory=lambda rid: tmp_path / "qruns" / rid
        ),
        factors=SimpleNamespace(data=SimpleNamespace(directory=lambda _: data)),
    )
    record = {
        "id": "r",
        "number": 3,
        "name": "Score",
        "modelRun": kind == "model",
        "objectKind": kind,
        "data": {"key": "frozen"},
    }
    assert layers.research(services, record, None, None)["instrumentId"] == "id-C"
    layer = layers.research(services, record, "id-B", None)["layer"]
    assert layer["adjustment"] == "split"
    assert layer["window"]["from"] == first
    raw, ranks = layer["series"]
    assert [p["value"] for p in raw["points"]] == [2.0, 4.0, 3.0]
    assert raw["points"][0]["time"] == drawn
    assert [p["value"] for p in ranks["points"]] == pytest.approx([25.0, 100.0, 100 / 3])


def test_strategy_keeps_every_fill_and_moves_plots_to_their_source_bar(tmp_path):
    folder = tmp_path / "output" / "strategy"
    folder.mkdir(parents=True)
    at = milliseconds("2025-06-02T13:30:00Z")
    opens = [at, at + 60_000]
    pd.DataFrame(
        {"instrument": ["SPY.N", "SPY.N"], "time": opens, "knownAt": [t + 60_000 for t in opens],
         "label": [milliseconds("2025-06-02")] * 2}
    ).to_parquet(tmp_path / "prices.parquet")
    (tmp_path / "info.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "nativeInstrument": "SPY.N",
                        "marketInstrument": {"displaySymbol": "SPY", "instrumentId": "spy"},
                        "series": {"timeframe": "1m", "adjustment": "raw"},
                    }
                ]
            }
        )
    )
    (tmp_path / "invocation.json").write_text("{}")
    (folder / "work.json").write_text(
        json.dumps(
            {
                "equity": [],
                "benchmark": [],
                "tradesTotal": 1,
                "plots": [
                    {
                        "pane": "price",
                        "style": "line",
                        "name": "SMA",
                        "t": [t + 60_000 for t in opens],
                        "values": [17, 19],
                    }
                ],
            }
        )
    )
    pd.DataFrame(
        {
            "instrument": ["SPY.N"] * 2,
            "time": [at, at + 1000],
            "side": ["buy", "buy"],
            "price": [18.0, 18.5],
            "quantity": [2.0, 1.0],
        }
    ).to_parquet(folder / "fills.parquet")
    services = SimpleNamespace(
        runs=SimpleNamespace(records=SimpleNamespace(), directory=lambda _: tmp_path)
    )
    result = layers.strategy(services, {"id": "r", "number": 1, "name": "Mean"}, None, None, [])
    assert result["instruments"] == [{"instrumentId": "spy", "symbol": "SPY", "key": "SPY.N"}]
    layer = result["layer"]
    assert layer["adjustment"] == "raw"
    assert layer["series"][1]["points"] == [
        {"time": "2025-06-02T13:30:00Z", "value": 17},
        {"time": "2025-06-02T13:31:00Z", "value": 19},
    ]
    assert layer["markers"] == [
        {"time": "2025-06-02T13:30:00Z", "side": "buy", "price": 18.0, "quantity": 2.0},
        {"time": "2025-06-02T13:30:01Z", "side": "buy", "price": 18.5, "quantity": 1.0},
    ]


def test_price_window_starts_at_the_first_scored_day(tmp_path):
    # A model scores only its test windows; the days before carry no prediction and no price window.
    run = tmp_path / "qmodels" / "r" / "output"
    run.mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()
    labels = [milliseconds(f"2025-06-0{d}") for d in (2, 3, 4)]
    instruments = [{"column": c, "instrumentId": "id-" + c, "displaySymbol": c, "series": {"adjustment": "split"}}
                   for c in ("A", "B")]
    (data / "info.json").write_text(json.dumps({"instruments": instruments, "timeframe": "1d"}))
    pd.DataFrame([{"column": c, "label": t, "time": t + 4 * HOUR, "knownAt": t + 20 * HOUR}
                  for c in ("A", "B") for t in labels]).to_parquet(data / "bars.parquet")
    (run / "pred.json").write_text(json.dumps({"labels": labels, "columns": ["A", "B"]}))
    np.save(run / "pred.npy", np.array([[np.nan, np.nan], [1.0, 2.0], [3.0, 1.0]]))
    services = SimpleNamespace(
        runs=SimpleNamespace(records=SimpleNamespace(root=tmp_path), directory=lambda rid: tmp_path / "qruns" / rid),
        factors=SimpleNamespace(data=SimpleNamespace(directory=lambda _: data)),
    )
    record = {"id": "r", "number": 1, "name": "M", "modelRun": True, "objectKind": "model", "data": {"key": "k"}}
    layer = layers.research(services, record, "id-A", None)["layer"]
    assert layer["window"]["from"] == "2025-06-03T04:00:00Z"
    assert [p["value"] for p in layer["series"][0]["points"]] == [1.0, 3.0]
