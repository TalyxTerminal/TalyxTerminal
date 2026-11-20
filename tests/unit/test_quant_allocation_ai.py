"""What an AI reads of an allocation stays inside the platform's response budget at real size (ten years of daily lines, 120 periods of 11 assets, a run list),
keeps the numbers it needs, and the screen's answers are not cut (the Work answer was some 36,000 characters, a Results summary 227,000)."""
from types import SimpleNamespace

from talyx_sdk import wire

LIMIT = 20_000
AI = SimpleNamespace(caller=SimpleNamespace(origin="ai", module=None, workspace_id="ws", ai={"client": "Agent"}, run=None))
SCREEN = SimpleNamespace(caller=SimpleNamespace(origin="user", module=None, workspace_id="ws", ai=None, run=None))
ASSETS = ["SPY", "TLT", "GLD", "IEF", "VNQ", "QQQ", "EFA", "EEM", "LQD", "HYG", "DBC"]
DAYS = [f"2016-{1 + k // 250 % 12:02d}-{1 + k % 28:02d}" for k in range(2600)]


def size(value):
    return len(wire.encode(value).decode("utf-8"))


def figure(key, names, points=2600):
    return {"data": [{"type": "scatter", "meta": {"role": "main", "key": name}, "name": name, "x": DAYS[:points], "y": [1 + k / 1e4 for k in range(points)],
                      "hovertemplate": name + " %{y:.3f}<extra></extra>"} for name in names], "layout": {"meta": {"key": key}, "yaxis": {"side": "right"}}}


def period(k):
    return {"date": DAYS[k * 21], "heldFrom": DAYS[k * 21 + 1], "weights": {a: 1 / 11 for a in ASSETS}, "riskShare": {a: 1 / 11 for a in ASSETS}, "turnover": .05, "cost": 1e-5,
            "status": "optimal", "note": "", "bound": {}, "seconds": .2}


class Runs:
    def work(self, run_id):
        rows = [{"asset": a, "group": "Funds", "weight": 1 / 11, "riskShare": 1 / 11, "published": None, "bound": None, "drifted": 1 / 11} for a in ASSETS]
        return {"run": {"id": run_id, "number": 3, "state": "completed", "settings": {"data": {"symbols": [{"instrument": {"instrumentId": "vi1_" + a, "displaySymbol": a, "name": a * 40}} for a in ASSETS]},
                                                                                       "params": {"objective": "min_risk"}}},
                "work": {"metrics": {"cagr": .05, "sharpe": .6, "vol": .09}, "checks": [{"id": "solver", "state": "pass", "value": "0"}], "thisPeriod": rows,
                         "periodsFigure": figure("allocation.work.periods", ASSETS, 120), "periodsGroupFigure": figure("allocation.work.periods", ["Funds"], 120),
                         "periodDates": DAYS[:120], "notes": []}}

    def results(self, run_id, tab):
        return {"tab": tab, "growth": figure("allocation.results.growth", ["Allocation", "Equal weight", "60/40"]),
                "drawdown": figure("allocation.results.drawdown", ["Allocation", "Equal weight", "60/40"]),
                "years": [{"year": 2016 + k, "Allocation": .05, "Equal weight": .04, "vsEqual": .01} for k in range(10)],
                "periods": [{"date": DAYS[k], "traded": True, "turnover": .05, "cost": 1.0, "limits": ", ".join(f"{a} cap" for a in ASSETS), "status": "optimal", "note": ""} for k in range(120)]}

    def period(self, run_id, index):
        return period(index)

    def list(self, object_id):
        return {"items": [{"id": f"run-{k}", "number": k, "state": "completed", "changes": [{"name": "lookback", "from": 252, "to": 126}] * 6,
                           "settings": {"params": {"pool": [{"ticker": a, "group": "Funds", "name": a * 30} for a in ASSETS]}}} for k in range(200)], "now": 0}

    async def prep(self, object_id):
        return {"objectId": object_id, "name": "Portfolio", "mode": "standard", "errors": [], "data": {}, "running": {"running": 0},
                "version": 1, "pool": [], "capabilities": {}, "limits": [], "engineWarm": False,
                "values": {"objective": "min_risk"}, "groups": [{"name": "Limits", "settings": [{"name": f"s{k}", "help": "x" * 200, "file": "a.py", "line": k}
                                                                                                                 for k in range(40)]}]}


def test_allocation_reports_keep_their_metrics_and_fit_the_public_page():
    from modules.quant.backend.allocation_routes import for_ai
    full = Runs().work("r")
    work = for_ai(full)
    assert size(full)>18000 and size(work)<=18000
    assert work["work"]["metrics"]["sharpe"] == .6 and len(work["work"]["thisPeriod"]) == 11
    assert work["work"]["periodsFigure"]["traces"][0]["points"] == 120
    shown = for_ai(Runs().results("r","summary"))
    assert [trace["name"] for trace in shown["growth"]["traces"]] == ["Allocation","Equal weight","60/40"]
    assert len(shown["years"]) == 10 and size(shown)<=18000
