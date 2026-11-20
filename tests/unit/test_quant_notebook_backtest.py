import asyncio
import random
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant import talyx_quant as q
from modules.quant.backend.notebook_routes import NotebookServices
from modules.quant.backend.quant_home import QuantHome


class Channel:
    """The Host side of the channel: one strategy run that advances a step each time it is read."""

    def __init__(self, steps, interrupt_at=None):
        self.calls, self.steps, self.interrupt_at, self.reads = [], steps, interrupt_at, 0

    def __call__(self, name, value, timeout=None):
        self.calls.append((name, value))
        if name == "quant.start_notebook_backtest":
            return {"runId": "r1", "number": 23}
        if name == "quant.read_run":
            self.reads += 1
            if self.interrupt_at == self.reads:
                raise KeyboardInterrupt
            state = "completed" if self.reads > self.steps else "running"
            return {"state": state, "progress": {"simulatedTo": f"step {self.reads}"}}
        return {}


def use(monkeypatch, channel):
    monkeypatch.setattr(q, "_channel", lambda: channel)
    monkeypatch.setattr("time.sleep", lambda seconds: None)


def test_one_line_backtest_runs_to_the_end(monkeypatch):
    channel = Channel(3)
    use(monkeypatch, channel)
    result = q.backtest("Reversal long–short", {"n": 50})
    assert result.run_id == "r1" and result.number == 23 and not result.partial and result.state == "completed"
    assert channel.calls[0] == ("quant.start_notebook_backtest", {"strategy": "Reversal long–short", "params": {"n": 50}})


def test_progress_and_stop(monkeypatch, capsys):
    channel = Channel(100, interrupt_at=3)
    use(monkeypatch, channel)
    result = q.backtest("Reversal long–short")
    assert result.partial and ("quant.cancel_run", {"runId": "r1"}) in channel.calls
    assert "Stopped at step" in capsys.readouterr().out
    # the run was started once however it ended
    assert [name for name, _ in channel.calls].count("quant.start_notebook_backtest") == 1


def test_backtest_lifecycle_random(monkeypatch):
    for seed in range(50):
        generator = random.Random(seed)
        channel = Channel(generator.randint(1, 6), interrupt_at=generator.choice([None, 1, 2, 4]))
        use(monkeypatch, channel)
        result = q.backtest("S")
        names = [name for name, _ in channel.calls]
        assert names.count("quant.start_notebook_backtest") == 1 and result.run_id == "r1" and result.number == 23
        assert result.partial == ("quant.cancel_run" in names) and (not result.partial or channel.interrupt_at is not None)


class Runs:
    def __init__(self):
        self.started = []
        self.records = SimpleNamespace(list=lambda kind: [{"id": "o1", "kind": "strategy", "name": "Reversal long–short"}])

    async def start(self, object_id, args):
        self.started.append((object_id, args))
        return {"id": "r1", "number": 23}


def services():
    item = NotebookServices.__new__(NotebookServices)
    item.runs = Runs()
    item.store = SimpleNamespace(records=item.runs.records)
    return item


def test_run_listed_under_strategy_tagged_notebook():
    item = services()
    assert asyncio.run(item.start_backtest({"strategy": "Reversal long–short", "params": {"n": 50}})) == {"runId": "r1", "number": 23}
    assert item.runs.started == [("o1", {"params": {"n": 50}, "tags": ["notebook"]})]


def test_backtest_unknown_strategy():
    item = services()
    with pytest.raises(ContractError):
        asyncio.run(item.start_backtest({"strategy": "Nope"}))
    assert item.runs.started == []


def test_notebook_run_not_representative():
    runs = [{"id": "n", "state": "completed", "runKind": "run", "starred": False, "tags": '["notebook"]', "number": 2},
            {"id": "a", "state": "completed", "runKind": "run", "starred": False, "tags": None, "number": 1}]
    found, how = QuantHome.representative(runs, {})
    assert found["id"] == "a" and how["how"] == "latest_successful"
    only, how = QuantHome.representative(runs[:1], {})
    assert only is None


def test_the_equity_so_far_is_a_figure_that_reads_out_like_the_draft(monkeypatch):
    class Steps(Channel):
        def __call__(self, name, value, timeout=None):
            result = super().__call__(name, value, timeout)
            if name == "quant.read_run":
                result["progress"]["equity"] = [[1_700_000_000_000, 0.0], [1_700_086_400_000, 0.021]]
            return result

    import sys
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(q.__file__).parent))
    shown = []
    monkeypatch.setitem(sys.modules, "IPython", SimpleNamespace(display=SimpleNamespace(display=lambda bundle, **keywords: shown.append(bundle))))
    monkeypatch.setitem(sys.modules, "IPython.display", sys.modules["IPython"].display)
    use(monkeypatch, Steps(2))
    q.backtest("Reversal long–short")
    figure = shown[0]["application/vnd.talyx.backtest+json"]["equity"]
    assert figure["layout"]["meta"]["key"] == "backtest_equity" and figure["data"][0]["meta"] == {"role": "main", "key": "backtest_equity"}
    assert figure["data"][0]["name"] == "bt" and figure["data"][0]["hovertemplate"] == "bt: %{y:+.1%}<extra></extra>"
    assert q._equity_figure(None) is None and q._equity_figure([[1, 0]]) is None


def test_over_machine_budget_reported_in_cell():
    item = services()

    async def refuse(object_id, args):
        raise ContractError("over_machine_budget", "This run needs about 9.2 GB; this machine gives runs 7.8 GB (half of 15.6 GB).", {"need": 1})

    item.runs.start = refuse
    with pytest.raises(ContractError) as error:
        asyncio.run(item.start_backtest({"strategy": "Reversal long–short"}))
    assert error.value.message == "This backtest needs about 9.2 GB; this machine gives runs 7.8 GB (half of 15.6 GB)." and error.value.code == "over_machine_budget"


def test_a_failed_backtest_block_says_why(monkeypatch):
    shown = []

    class Channel:
        def __call__(self, name, value, timeout=None):
            if name == "quant.start_notebook_backtest":
                return {"runId": "r1", "number": 3}
            return {"state": "failed", "failure":{"message":"okx is unreachable from this machine right now"}, "progress": None}

    import sys
    import types
    display = types.SimpleNamespace(display=lambda value, **kw: shown.append(value))
    monkeypatch.setitem(sys.modules, "IPython", types.SimpleNamespace(display=display))
    monkeypatch.setitem(sys.modules, "IPython.display", display)
    monkeypatch.setattr(q, "_channel", lambda: Channel())
    result = q.backtest("S", every=0)
    assert result.state == "failed" and shown[-1]["application/vnd.talyx.backtest+json"]["failure"] == "okx is unreachable from this machine right now"


def test_the_simulating_step_names_a_date_not_a_timestamp():
    from modules.quant import talyx_quant as q
    assert q._day_text(1_757_476_800_000) == "Sep 10, 2025"
