"""A preset that skipped every rebalance says why under the key numbers, not only in its table."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2] / "modules/quant"))
from native.strategy_report import Report  # noqa: E402


def report(decisions):
    found = object.__new__(Report)
    found.strategy = SimpleNamespace(reports=lambda engine, frame: {} if decisions is None else {"Signal rebalances": decisions})
    found.engine = found.frame = None
    found.prices, found.invocation = SimpleNamespace(by={}), {}
    return found


def test_every_rebalance_skipped_names_the_reason():
    decisions = pd.DataFrame([{"ts_available": 1, "state": "skipped", "reason": "Only 1 of 2 names have a value; 2 are needed", "version": "v1"}] * 3)
    assert report(decisions).notes({"trades": 0, "rejected": 0}) == ["No trades: all 3 rebalances were skipped. Only 1 of 2 names have a value; 2 are needed (see Signal rebalances)"]


def test_code_without_orders_keeps_the_plain_note():
    assert report(None).notes({"trades": 0, "rejected": 0}) == ["No trades. The code has not placed an order yet."]
    some = pd.DataFrame([{"ts_available": 1, "state": "skipped", "reason": "x", "version": "v1"},
                         {"ts_available": 2, "state": "done", "reason": "", "version": "v1"}])
    assert report(some).notes({"trades": 0, "rejected": 0}) == ["No trades. The code has not placed an order yet."]
