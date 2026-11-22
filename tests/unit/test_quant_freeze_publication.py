import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend.factor_data import FactorData
from tests.unit.test_quant_library_inputs import BTC, library


@pytest.fixture
def freeze(tmp_path, monkeypatch):
    data = FactorData(None, tmp_path, None, None)
    settings = {"symbols": [{"instrument": {"instrumentId": "A"}}], "timeframe": "1d",
                "since": "2024-01-01", "until": "2024-01-03"}

    async def prepare(key, resolved, report, directory, calendars, facts=True):
        if calendars == "one":
            raise ContractError("invalid_input", "2 different calendars")
        directory.mkdir()
        (directory / "bars.parquet").write_bytes(b"two markets")
        return {"frozenAt": 1, "fingerprint": "two"}

    monkeypatch.setattr(data, "_prepare", prepare)
    monkeypatch.setattr(data, "unchanged", lambda ready: asyncio.sleep(0, result=True))
    return data, settings


def test_one_calendar_rejects_the_same_two_that_many_accepted(freeze):
    data, settings = freeze
    asyncio.run(data.prepare(settings, calendars="many"))
    with pytest.raises(ContractError, match="2 different calendars"):
        asyncio.run(data.prepare(settings, calendars="one"))


def test_ready_manifest_and_prices_appear_together_after_interruption(freeze, monkeypatch):
    data, settings = freeze
    rename = Path.rename
    def publish(source, destination):
        if source.name.startswith(".preparing-"):
            assert json.loads((source / "ready.json").read_text())["directory"] == str(destination)
        return rename(source, destination)
    monkeypatch.setattr(Path, "rename", publish)
    # A previous interrupted publication left prices in its final directory, with no ready manifest.
    key = data.key(settings, calendars="many")
    data.directory(key).mkdir()
    (data.directory(key) / "bars.parquet").write_bytes(b"partial")
    ready = asyncio.run(data.prepare(settings, calendars="many"))
    assert Path(ready["barsPath"]).read_bytes() == b"two markets"


def test_appending_to_same_factor_version_changes_next_snapshot(tmp_path):
    store = library(tmp_path, [(BTC, "factor", 5, .5, 5, 5, "history")])
    data = FactorData(None, tmp_path / "prices", None, store)
    data.directory("key").mkdir()
    info = {"since": 0, "until": 9, "instruments": [{"instrumentId": BTC, "column": "BTC"}]}
    first = asyncio.run(data.library_values("key", info))
    store.store.append_values("v1", [(BTC, "factor", 8, .8, 8, 8, "scheduled")], cursor=4 * 86_400_000)
    second = asyncio.run(data.library_values("key", info))
    assert pd.read_parquet(second)["value"].tolist() == [.5, .8]
    assert pd.read_parquet(first)["value"].tolist() == [.5]
