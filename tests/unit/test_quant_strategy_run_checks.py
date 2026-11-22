"""Everything a strategy run's checks refuse as its first step (nothing fetched), and what the Host does for the run before it starts:
saving every open file, reading the names the code asks Market for, and refusing failed explicit symbols."""
import asyncio

import pytest
from talyx_sdk import ContractError

from tests.fixtures.strategy import fakes
from tests.fixtures.strategy.fakes import services, strategy
from tests.unit.test_quant_object_runs import DATA, INSTRUMENT
from vocabulary.instruments import MarketInstrument

GIB = 1024 ** 3
VERSIONS = {"v2": {"name": "Alpha", "kind": "model", "version": 2, "state": "live", "publishedAt": 1, "trainingCutoff": 1_719_705_600_000, "heldOutFrom": "2025-05-01"}}


def code_with(line):
    return 'import talyx_quant as q\n\nclass S(q.Strategy):\n    def __init__(self):\n        super().__init__()\n        ' + line + '\n'


def count(shell):
    return shell.runs.records.db.execute("SELECT count(*) FROM records WHERE kind='qrun'").fetchone()[0]


def scenario(tmp_path, body, **more):
    async def run():
        ctx, records, shell = services(tmp_path, **more)
        try:
            return await body(ctx, shell)
        finally:
            records.close()
    return asyncio.run(run())


async def refused(shell, obj, code, message=None, **args):
    """The run is made at the click and its checks are its first step: a refusal ends that run failed at the check stage, before anything
    is fetched, with the reason (design.md, run flow)."""
    before = count(shell)
    record = await shell.runs.start(obj, args)
    assert count(shell) == before + 1 and record["state"] == "preparing" and record["stage"] == "check"
    await asyncio.gather(shell.runs.tasks.get(record["id"]) or asyncio.sleep(0), return_exceptions=True)
    ended = shell.runs.read(record["id"])
    assert ended["state"] == "failed" and ended["error"]["stage"] == "check", ended
    error = ContractError(ended["error"]["code"], ended["error"]["message"], ended["error"].get("details"))
    assert error.code == code, (error.code, error.message)
    if message:
        assert message in error.message
    assert ended["data"] is None  # nothing was fetched
    return error


def patch_binding(monkeypatch, cutoff):
    from modules.quant.backend import model_inputs

    def binding(library, name, *, version_id=None):
        return {"name": name, "versionId": "v2", "version": 2, "trainingCutoff": cutoff, "model": {}, "environment": None, "digest": "d"}
    monkeypatch.setattr(model_inputs, "binding", binding)


def test_model_cutoff_rejects_early_start(tmp_path, monkeypatch):
    patch_binding(monkeypatch, 1_719_705_600_000)  # 2024-06-30
    async def body(ctx, shell):
        obj = await strategy(shell, code_with('self.model = q.model("Alpha")'))
        error = await refused(shell, obj, "model_training_overlap")
        assert error.message == ("Model 'Alpha' v2 was trained on data to 2024-06-30. Start after that date, or read its published values, which cover earlier dates.")
        # starting after the cutoff is not refused for this reason
        await shell.store.update_settings(obj, {"data": {**DATA, "since": "2024-07-02", "until": "2024-12-31"}})
        try:
            await shell.runs.start(obj, {})
        except ContractError as other:
            assert other.code != "model_training_overlap"
    scenario(tmp_path, body)


def test_model_values_backtest_before_cutoff(tmp_path, monkeypatch):
    """A model read through its published values (q.pick) has no cutoff to respect: a start before its training cutoff is not refused."""
    patch_binding(monkeypatch, 1_719_705_600_000)
    async def body(ctx, shell):
        obj = await strategy(shell, code_with('self.signal = q.pick("signal", kinds=("factor", "model"))'))
        try:
            await shell.runs.start(obj, {})
        except ContractError as error:
            assert error.code != "model_training_overlap"
    scenario(tmp_path, body)


def test_stopped_input_runs(tmp_path):
    """A strategy pinned to a stopped version runs: the start check does not refuse it (its values end where it stopped)."""
    async def body(ctx, shell):
        shell.runs.library = fakes.library({"v3": {**VERSIONS["v2"], "version": 3, "state": "delisted"}})
        obj = await strategy(shell, code_with('self.model = q.model("Alpha")'))
        record = shell.store.read(obj)
        shell.store.save(record, settings={**record["settings"], "inputs": {"library": {}, "models": {"Alpha": {"name": "Alpha", "versionId": "v3"}}}})
        try:
            await shell.runs.start(obj, {})
        except ContractError as error:
            assert error.code != "input_retired"
    scenario(tmp_path, body)


def test_unavailable_period_refused_before_fetch(tmp_path):
    async def body(ctx, shell):
        okx_eth = {**fakes.OKX_ETH}
        obj = await strategy(shell, data={"symbols": [{"instrument": okx_eth}], "timeframe": "1h", "since": "2024-01-01", "until": "2024-06-30"})
        error = await refused(shell, obj, "unsupported")
        assert error.message == "okx ETH/USDT:USDT perpetual 1h history is not available in Market."
        assert ctx.exported == []  # nothing was fetched
    scenario(tmp_path, body, routes=[{**fakes.ROUTE, "available": {"periods": ["1d"], "earliest": None, "live": "stream"}}])


def test_the_choices_list_only_verified_routes(tmp_path):
    from modules.quant.backend import object_data
    async def body(ctx, shell):
        found = await object_data.verified_routes(ctx, INSTRUMENT)
        assert found == ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
        ctx.routes = [{**fakes.ROUTE, "available": {"periods": [], "earliest": None, "live": "none"}}]
        assert await object_data.verified_routes(ctx, INSTRUMENT) == []
    scenario(tmp_path, body)


def test_missing_series_refused_before_run(tmp_path):
    async def body(ctx, shell):
        obj = await strategy(shell, code_with('self.oi = q.series("open_interest")'))
        error = await refused(shell, obj, "series_missing")
        assert error.message == "BTC/USDT:USDT has no open_interest history in Market"
        ok = await strategy(shell, code_with('self.rate = q.series("funding_rate")'), name="Funding")
        try:
            await shell.runs.start(ok, {})
        except ContractError as other:
            assert other.code != "series_missing"
    scenario(tmp_path, body)


def test_series_names_are_read_from_every_project_file(tmp_path):
    from modules.quant.backend.strategy_inputs import series_names
    files = {"strategy.py": 'import talyx_quant as q\nq.series("funding_rate")\nq.series("mark_price", other)\n', "helpers.py": 'import talyx_quant as q\nx = q.series("open_interest")\n'}
    assert series_names(files) == {"funding_rate", "mark_price", "open_interest"}


def test_unresolvable_account_conversion_preserves_market_refusal(tmp_path):
    async def body(ctx, shell):
        run = {"accounts": [{"venue": "COINBASE", "balances": ["10000 USD"]}, {"venue": "OKX", "balances": ["10000 USDT"]}]}
        spot = MarketInstrument(venueId="coinbase", symbol="ETH/USD", displaySymbol="ETH/USD", marketType="spot",
                                assetClass="crypto", base="ETH", quote="USD").to_dict()
        two = {**DATA, "symbols": [{"instrument": INSTRUMENT}, {"instrument": spot}]}
        obj = await strategy(shell, data=two, run=run)
        error = await refused(shell, obj, "not_found")
        assert error.message == "Market cannot identify USDT/USD."
    scenario(tmp_path, body, routes=[fakes.ROUTE])


def test_ranked_without_signal_refused_before_run(tmp_path):
    async def body(ctx, shell):
        created = await shell.store.create({"kind": "strategy", "preset": "ranked"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        error = await refused(shell, created["id"], "invalid_input")
        assert error.message == "Choose the signal this strategy ranks by (a library item)."
    scenario(tmp_path, body)


def test_unsaved_files_all_in_snapshot(tmp_path):
    """Both open documents of the project are saved before the run fixes the code; a conflict in any one stops the run and names the file."""
    async def body(ctx, shell):
        obj = await strategy(shell, code_with("pass"))
        record = shell.store.read(obj)
        entry, helper = (f"{record['directory']}/{record['entry']}", f"{record['directory']}/helpers.py")
        ctx.documents.rows = [{"documentId": "d1", "uri": "file:///" + entry, "version": 3, "dirty": True}, {"documentId": "d2", "uri": "file:///" + helper, "version": 5, "dirty": True},
                              {"documentId": "d3", "uri": "file:///" + record["directory"] + "/clean.py", "version": 1, "dirty": False}]
        await shell.runs.saved_source(record)
        assert ctx.documents.saved == ["d1", "d2"]
        ctx.documents.saved.clear()
        ctx.documents.conflicts = {"d2"}
        with pytest.raises(ContractError, match="helpers.py"):
            await shell.runs.saved_source(record)
    scenario(tmp_path, body)
