"""Exports the files the view tests read (`work.json`, `results.json`, `trades.parquet`, `orders.parquet`, `log.parquet`, and a portfolio's
`rebalances.parquet` and `decisions.parquet`) from synthetic runs, so
no test fixture holds a hand-written number. Run it with TALYX_NATIVE_SMOKE=1 whenever the report's shape changes:

    python -m tests.fixtures.strategy.make_fixtures
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from tests.fixtures.strategy import bars as B
from tests.fixtures.strategy import engine as E

HERE = Path(__file__).resolve().parent / "runs"


def single(root):
    from tests.native.test_strategy_report_native import CODE
    E.prepare(root, CODE)


def multi(root):
    from tests.native.test_strategy_report_native import REBALANCE, stock_run
    frame, ids = B.daily_universe(8, 120, halted=((3, 40, 44),))
    stock_run(root, REBALANCE.replace("SCHEDULE", "{2: {i: .1 for i in range(8)}, 30: {i: .12 for i in range(8)}, 41: {i: .08 for i in range(8)}}"), ids, frame)


def failed(root):
    from tests.native.test_strategy_report_native import CODE
    code = CODE.replace("        held = self.position_size(self.instrument_id)\n", "        held = self.position_size(self.instrument_id)\n        level = float(bar.close) / 2\n        if self.count == 100:\n            raise RuntimeError('boom')\n")
    E.prepare(root, code)


MAKERS = {"single": single, "multi": multi, "failed": failed}


def export(names=None):
    for name in names or MAKERS:
        root = Path(tempfile.mkdtemp(prefix="strategy-fixture-"))
        try:
            MAKERS[name](root)
            index = E.run(root)
            target = HERE / name
            shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True)
            shutil.copy(root / "output" / "result-index.json", target / "result-index.json")
            for item in (root / "output" / "strategy").iterdir():
                shutil.copy(item, target / item.name)
            print(name, index["status"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    export(sys.argv[1:] or None)
