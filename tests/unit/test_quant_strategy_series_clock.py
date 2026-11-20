"""A strategy's extra series are visible from the moment each value is known, never earlier."""
import asyncio

import pandas as pd

from modules.quant.backend import strategy_inputs as S

HOUR = 3_600_000


def test_mark_and_index_closes_are_stamped_at_their_bar_close(tmp_path, monkeypatch):
    # Two 1h mark-price bars: 00:00-01:00 closes at 100, 01:00-02:00 closes at 200. At 01:00 only the first close is known.
    start = 1_704_067_200_000  # 2024-01-01 00:00 UTC
    bars = [{"time": str(start * 1_000_000), "closeTime": str((start + HOUR) * 1_000_000), "close": "100"},
            {"time": str((start + HOUR) * 1_000_000), "closeTime": str((start + 2 * HOUR) * 1_000_000), "close": "200"}]

    async def observations(ctx, instrument, capability, since, until, *, timeframe=None, price_basis="last"):
        assert capability == "ohlcv" and price_basis in ("mark", "index")
        return bars
    monkeypatch.setattr(S.M, "observations", observations)
    sources = [{"marketInstrument": {"instrumentId": "x"}, "nativeInstrument": "BTCUSDT-PERP.BINANCE", "series": {"timeframe": "1h"}}]
    out = asyncio.run(S.extras(None, {"mark_price", "index_price"}, sources, start, start + 2 * HOUR, tmp_path))
    for name in ("mark_price", "index_price"):
        table = pd.read_parquet(out[name]["BTCUSDT-PERP.BINANCE"])
        assert table["time"].tolist() == [start + HOUR, start + 2 * HOUR]
        known_at_one = table[table["time"] <= start + HOUR]["value"].tolist()
        assert known_at_one == [100.0]
