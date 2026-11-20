"""The one run flow: states, numbers, the queue, stopping, failing, and what changed between two runs."""
from modules.quant.backend import run_flow as F
from modules.quant.backend.work_records import WorkRecords

AAPL = {"instrument": {"instrumentId": "a", "displaySymbol": "AAPL"}}
BTC = {"instrument": {"instrumentId": "b", "displaySymbol": "BTC/USDT"}}


def test_what_changed_compares_the_whole_effective_settings():
    # the dates moved and a name was added, yet the old comparisons said "No changes" / "Same as #2".
    before = {"data": {"symbols": [AAPL], "since": "2020-01-01", "until": "2024-12-31"}, "params": {"lookback": 126},
              "run": {"costs": {"commissionBps": 5}}, "code": "abc"}
    after = {"data": {"symbols": [AAPL, BTC], "since": "2021-01-01", "until": "2024-12-31"}, "params": {"lookback": 126},
             "run": {"costs": {"commissionBps": 7}}, "code": "abc"}
    assert F.changes(before, after) == [
        {"what": "data.since", "from": "2020-01-01", "to": "2021-01-01"},
        {"what": "data.symbols", "from": ["AAPL"], "to": ["AAPL", "BTC/USDT"]},
        {"what": "run.costs.commissionBps", "from": 5, "to": 7}]
    assert F.changes(before, before) == []
    assert F.changes(None, after) == []
    # an index is named by its symbol, and a setting that is new on one side shows from nothing
    index = {"data": {"index": {"instrument": {"instrumentId": "i", "displaySymbol": "^GSPC"}}}}
    assert F.changes(index, {"data": {"index": {"instrument": {"instrumentId": "j", "displaySymbol": "^NDX"}}, "heldOutFrom": "2024-01-01"}}) == [
        {"what": "data.heldOutFrom", "from": None, "to": "2024-01-01"}, {"what": "data.index", "from": "^GSPC", "to": "^NDX"}]


def test_a_publication_takes_no_run_number(tmp_path):
    records = WorkRecords(tmp_path)
    records.put("qrun", {"id": "r1", "objectId": "o", "state": "completed", "number": 3})
    records.put("qrun", {"id": "p1", "objectId": "o", "state": "completed", "runKind": "publish", "number": 9})
    assert F.next_number(records, "o") == 4 and F.next_number(records, "other") == 1
    records.close()


def test_runs_going_when_the_application_stopped_are_marked_interrupted(tmp_path):
    records = WorkRecords(tmp_path)
    records.put("qrun", {"id": "r1", "objectId": "o", "state": F.RUNNING})
    records.put("qrun", {"id": "r2", "objectId": "o", "state": F.COMPLETED})
    records.close()
    again = WorkRecords(tmp_path)
    assert again.find("qrun", "r1")["state"] == F.INTERRUPTED and again.find("qrun", "r2")["state"] == F.COMPLETED
    again.close()
