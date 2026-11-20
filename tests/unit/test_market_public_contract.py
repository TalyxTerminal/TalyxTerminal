"""The public read shape at the size of a large equity universe."""

import json

from modules.market.backend import public_inputs as inputs
from modules.market.backend import public_results as outputs


def test_default_rows_budget_for_500_symbols():
    symbols = [f"US:NAME{n}" for n in range(500)]
    query = inputs.ReadData(limit=24, symbols=symbols, start="2025-01-02", end="2025-01-02")
    columns = ["symbol", "instrumentId", "time", "open", "high", "low", "close", "volume"]
    rows = [
        [symbol, f"vi2_{n:032x}", "2025-01-02", 170.31, 170.81, 169.76, 170.11, 1021345]
        for n, symbol in enumerate(symbols[: query.limit])
    ]
    result = outputs.MarketTable(
        columns=[{"name": c, "unit": None} for c in columns],
        total=500,
        rows=rows,
        nextCursor=str(query.limit),
        meta=[
            {
                "symbol": row[0],
                "instrumentId": row[1],
                "source": "exchange",
                "name": "Listed common stock",
                "adjustment": "split",
                "units": {"price": "USD"},
                "first": row[2],
                "last": row[2],
                "gaps": [],
            }
            for row in rows
        ],
    )
    assert len(json.dumps(result.model_dump(), separators=(",", ":"), ensure_ascii=False)) <= 18_000
