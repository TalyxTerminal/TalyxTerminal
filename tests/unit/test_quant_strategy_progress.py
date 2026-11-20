"""A strategy run in flight reports its progress and its equity so far, and that equity moves on a run driven only by bars."""
from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import pytest

from modules.quant.backend.object_routes import with_progress


def test_get_object_run_gives_a_strategy_runs_progress_and_nothing_to_other_runs():
    progress = {"simulatedTo": 1_700_000_000_000, "value": 0.4, "tradesSoFar": 3, "equity": [[1, 0.0], [2, -0.01]], "stage": "backtest"}
    strategy = SimpleNamespace(progress=lambda record: progress if record["state"] == "running" else None)
    view = {"runId": "r1", "state": "running"}
    got = with_progress(view, {"objectKind": "strategy", "state": "running"}, strategy)
    assert got["progress"] == progress and got["runId"] == "r1"
    assert {key: got["progress"][key] for key in ("simulatedTo", "value", "tradesSoFar", "equity")} == {key: progress[key] for key in ("simulatedTo", "value", "tradesSoFar", "equity")}
    # Over, or not a strategy: the view is returned as it was, with no progress key.
    assert "progress" not in with_progress(view, {"objectKind": "strategy", "state": "completed"}, strategy)
    assert "progress" not in with_progress(view, {"objectKind": "factor", "state": "running"}, strategy)


@pytest.fixture
def native_free(monkeypatch):
    """The progress actor needs nautilus's DataActor only as a base class: a plain one stands in, with the clock and cache a run's actor has."""
    common = types.ModuleType("nautilus_trader.common")

    class DataActor:
        clock = cache = None

        def __init__(self, config=None):
            pass
    common.DataActor, common.DataActorConfig = DataActor, lambda actor_id=None: None
    model = types.ModuleType("nautilus_trader.model")
    model.ActorId = lambda name: name
    package = types.ModuleType("nautilus_trader")
    for name, module in (("nautilus_trader", package), ("nautilus_trader.common", common), ("nautilus_trader.model", model)):
        monkeypatch.setitem(sys.modules, name, module)


def test_the_equity_so_far_moves_on_a_run_that_only_has_bars(tmp_path, monkeypatch, native_free):
    from pathlib import Path

    import pandas as pd
    # The engine's own modules import each other as `native.*`, with modules/quant on the path (as in a run's process).
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
    for name in [name for name in sys.modules if name == "native" or name.startswith("native.")]:
        monkeypatch.delitem(sys.modules, name)
    import native.strategy_equity as strategy_equity
    import native.strategy_research as strategy_research

    class Capture:
        """Snapshots exist in the account's ring but only reach `rows` when read (a bars-only run gives no quote or trade to trigger it)."""
        def __init__(self):
            self.rows, self.ring = [], [1000.0]

        def read_ring(self):
            self.rows = [{"valuation": "complete", "equityNumeric": value} for value in self.ring]

    capture = Capture()
    engine = SimpleNamespace(add_actor=lambda actor: None)
    monkeypatch.setitem(strategy_equity._CAPTURES, id(engine), capture)
    frame = pd.DataFrame({"ts_event": [0, 100 * 10**9]})
    actor = strategy_research.progress_actor(engine, tmp_path, frame, [None])
    clock = SimpleNamespace(now=0, timestamp_ns=lambda: clock.now)
    actor.clock = clock
    actor.cache = SimpleNamespace(positions_closed=lambda: [])
    equity = []
    for step, value in enumerate([1000.0, 1010.0, 990.0, 1020.0]):
        capture.ring.append(value)
        clock.now = (step + 1) * 10 * 10**9
        actor.on_bar(SimpleNamespace())
        equity = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))["equity"]
    values = [point[1] for point in equity]
    assert len(values) == 4 and any(value != 0 for value in values)
    assert values[-1] == pytest.approx(1020.0 / 1000.0 - 1, abs=1e-6)
