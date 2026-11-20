"""Symbols never become paths: names such as BTC/USDT:USDT, A:B, a Windows reserved word and two that differ only in case are prepared as
columns of their own (the whole run and the values of a day are in tests/native/test_quant_factor_names_run.py); and a run that breaks inside
says what broke without folders or internal ids."""
import asyncio
import json
from types import SimpleNamespace

from modules.quant.backend import factor_data as D
from tests.native.test_quant_factor_names_run import DAY, START, SYMBOLS, instrument
from tests.unit.test_quant_object_runs import SERIES


def test_awkward_symbols_are_prepared_each_as_a_column_of_its_own(tmp_path, monkeypatch):
    import pandas as pd
    chosen = [instrument(index, symbol) for index, symbol in enumerate(SYMBOLS)]
    async def prepare(ctx, settings, owner, directory, **kwargs):
        path = directory / "bars.parquet"
        pd.DataFrame({"column": SYMBOLS, "label": [START] * len(SYMBOLS), "close": [1.] * len(SYMBOLS)}).to_parquet(path)
        info = {"since": START, "until": START + DAY, "adjusted": True, "timeframe": "1d", "revisions": {"series": "r1"},
                "instruments": [{"column": item["displaySymbol"], "instrumentId": item["instrumentId"], "instrument": item,
                    "series": SERIES, "revision": "r1", "source": {"sourceId": "test"}} for item in chosen]}
        return {"research": {"barsPath": str(path), "info": info}, "references": [], "summary": {"requested": len(chosen),
                "fetchRows": [{"instrumentId": item["instrumentId"], "ticker": item["displaySymbol"]} for item in chosen]}}
    async def describe(ctx, instruments):
        return {"fields": [], "limits": [], "sources": []}
    monkeypatch.setattr(D.research_data, "prepare", prepare)
    monkeypatch.setattr(D.research_inputs, "describe", describe)
    data = D.FactorData(None, tmp_path / "factor-data", SimpleNamespace(members=lambda name: []), None)
    ready = asyncio.run(data.prepare({"symbols": [{"instrument": item} for item in chosen], "timeframe": "1d", "since": "2024-01-01", "until": "2024-06-08"}))
    assert ready["names"] == len(SYMBOLS)
    assert {item["column"] for item in ready["info"]["instruments"]} == set(SYMBOLS)


def test_a_run_that_breaks_inside_says_what_broke_without_folders(tmp_path, monkeypatch):
    from tests.unit.test_quant_factor_runs import services
    from tests.unit.test_quant_object_runs import DATA
    ctx, shell, factors = services(tmp_path, monkeypatch, [])

    async def broken(settings, report=None):
        raise OSError(f"Cannot save file into a non-existent directory: '{tmp_path}\\factor-data\\c163cf\\names\\BTC'")
    monkeypatch.setattr(factors.data, "prepare", broken)

    async def scenario():
        created = await shell.store.create({"kind": "factor", "name": "Reversal"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        started = await factors.start(created["id"], {"formula": "-($close / Ref($close, 5) - 1)"})
        async with asyncio.timeout(10):
            while started["id"] in factors.tasks:
                await asyncio.sleep(.01)
        return factors.read(started["id"])
    run = asyncio.run(scenario())
    assert run["state"] == "failed" and "OSError" in run["failure"]["message"]
    assert str(tmp_path) not in json.dumps(run["failure"]) and "c163cf" not in run["message"]



def test_a_member_without_early_prices_records_the_missing_interval():
    import pandas as pd

    from modules.quant.backend.index_universe import check_prices, freeze_rows
    item = instrument(0, "A")
    since = START - 30 * DAY
    index = freeze_rows({"instrument": {"instrumentId": "index"}}, since, START + DAY - 1,
        [{"instrumentId": item["instrumentId"], "symbol":"A","start":pd.Timestamp(since,unit="ms",tz="UTC").isoformat(),"end":None,"knownAt":"1970-01-01"}],
        {item["instrumentId"]: item}, unmapped=0)
    calendar = [(day, day + DAY) for day in range(since, START + DAY, DAY)]
    check_prices(index, {"instrument": item, "series": SERIES}, pd.DataFrame({"time": [START]}), START + DAY, calendar)
    gone = index["exclusions"]
    assert len(gone) == 1 and gone[0]["since"] == since and gone[0]["until"] == START + DAY - 1
    assert gone[0]["symbol"] == "A" and gone[0]["kind"] == "missing_price"
