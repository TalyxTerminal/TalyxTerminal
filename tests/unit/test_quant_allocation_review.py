"""The monthly review of a published allocation: accept as computed, or a one-off adjustment written for the period (every asset, a reason), read as
of the moment it arrived and only once; the home page's reminder; and the risk-drift thresholds the library keeps."""
import asyncio
from types import SimpleNamespace

import pytest
from talyx_sdk import ContractError

from modules.quant.backend import library_summary
from modules.quant.backend.allocation_runs import AllocationRuns
from modules.quant.backend.library_store import LibraryStore

MONTH = 30 * 86_400_000
POOL = ["SPY", "IEF", "BTC"]


def rows(label, weights, available):
    return [(asset, "weight", label, weight, available, available, "scheduled") for asset, weight in zip(POOL, weights, strict=True)]


def store_with_version(tmp_path, clock, **meta):
    store = LibraryStore(tmp_path / "library", clock=clock)
    store.commit_versions([{"name": "Mix", "kind": "allocation", "versionId": "v1", "meta": {"fields": ["weight"], "timeframe": "1d", "net": 1.0, "rebalance": {"every": "month"}, **meta},
                            "rows": rows(MONTH, [.5, .4, .1], MONTH) + rows(2 * MONTH, [.5, .45, .05], 2 * MONTH), "objectId": "o", "runId": "r", "workspaceId": "ws",
                            "state": "updating", "cursor": 2 * MONTH, "checks": {}}])
    return store


def runs_over(store):
    return AllocationRuns.__new__(AllocationRuns), SimpleNamespace(store=store)


def reviewing(store):
    """The review over a library store alone: no run records (the weights are named by their instruments)."""
    runs = AllocationRuns.__new__(AllocationRuns)
    runs.library, runs.records = SimpleNamespace(store=store), SimpleNamespace(find=lambda kind, key: None)
    return runs


def review(store, **args):
    return asyncio.run(reviewing(store).review({"versionId": "v1", **args}))


def test_one_off_needs_reason(tmp_path):
    store = store_with_version(tmp_path, lambda: 5 * MONTH)
    with pytest.raises(ContractError) as error:
        review(store, period=2 * MONTH, how="one_off", weights={"SPY": .5, "IEF": .5, "BTC": 0.0}, reason="  ")
    assert error.value.message == "Write why this period differs." and error.value.details["fields"] == ["reason"]
    with pytest.raises(ContractError, match="add up to 100%"):
        review(store, period=2 * MONTH, how="one_off", weights={"SPY": .5, "IEF": .4, "BTC": 0.0}, reason="Custody limit")
    assert "reviews" not in store.version("v1")["meta"]  # nothing was written by either refusal


def test_a_one_off_adjustment_writes_every_asset_and_is_read_only_after_it_arrives(tmp_path):
    clock = {"now": 3 * MONTH}
    store = store_with_version(tmp_path, lambda: clock["now"])
    done = review(store, period=2 * MONTH, how="one_off", weights={"SPY": .5, "IEF": .5, "BTC": 0.0}, reason="Custody limit at the new exchange")
    assert done["review"]["how"] == "one_off" and done["review"]["reason"].startswith("Custody")
    wrote = [row for row in store.read_values("v1") if row["mode"] == "one_off"]
    assert sorted(row["instrument"] for row in wrote) == sorted(POOL) and {row["instrument"]: row["value"] for row in wrote}["BTC"] == 0.0  # the zero is written too
    # A reader as of before the adjustment arrived gets the computed set; after it, the adjusted one: one row an asset either way.
    before = {row["instrument"]: row["value"] for row in store.latest_available("v1", as_of=2 * MONTH + 1)}
    after = {row["instrument"]: row["value"] for row in store.latest_available("v1", as_of=clock["now"])}
    assert before == {"SPY": .5, "IEF": .45, "BTC": .05} and after == {"SPY": .5, "IEF": .5, "BTC": 0.0}
    assert store.version("v1")["cursor"] == 2 * MONTH  # the cursor and the computed period did not move


def test_a_period_is_reviewed_once(tmp_path):
    store = store_with_version(tmp_path, lambda: 5 * MONTH)
    assert review(store, period=2 * MONTH, how="accept")["review"]["how"] == "accepted"
    with pytest.raises(ContractError) as error:
        review(store, period=2 * MONTH, how="one_off", weights={"SPY": 1.0, "IEF": 0.0, "BTC": 0.0}, reason="again")
    assert error.value.code == "conflict"


def test_a_retired_version_has_nothing_to_review(tmp_path):
    store = store_with_version(tmp_path, lambda: 5 * MONTH)
    store.stop("v1")
    with pytest.raises(ContractError, match="retired"):
        review(store, period=2 * MONTH, how="accept")


def test_only_an_allocation_is_reviewed(tmp_path):
    store = LibraryStore(tmp_path / "library", clock=lambda: 5 * MONTH)
    store.commit_versions([{"name": "Momentum", "kind": "factor", "versionId": "v1", "meta": {"fields": ["factor"], "timeframe": "1d"}, "rows": [("A", "factor", MONTH, 1.0, MONTH, MONTH, "history")],
                            "objectId": "o", "runId": "r", "workspaceId": "ws", "state": "updating", "cursor": MONTH, "checks": {}}])
    with pytest.raises(ContractError, match="allocation"):
        review(store, period=MONTH, how="accept")


def test_the_newest_period_is_a_reminder_until_it_is_decided(tmp_path):
    store = store_with_version(tmp_path, lambda: 5 * MONTH)
    def reminders():
        summary = store.summaries()
        items = summary["items"] if isinstance(summary, dict) else summary
        return [entry for entry in library_summary.attention([item for item in items])[0] if entry["label"] == "Review this period"]
    assert len(reminders()) == 1 and reminders()[0]["action"]["id"] == "review_period"
    review(store, period=2 * MONTH, how="accept")
    assert reminders() == []


NOW = {"A": 0.36, "B": 0.64}   # the risk shares the producer wrote with the newest weights


def _after(published, shares=None):
    import numpy as np
    import pandas as pd

    from modules.quant.backend import library_after
    rng = np.random.default_rng(3)
    index = pd.RangeIndex(1, 200)
    prices = pd.DataFrame({"A": 100 * np.cumprod(1 + rng.normal(0, .01, 199)), "B": 100 * np.cumprod(1 + rng.normal(0, .02, 199))}, index=index)
    values = pd.DataFrame({"datetime": [1, 1], "instrument": ["A", "B"], "value": [.6, .4]})
    return library_after.allocation(values, prices, scheduled=prices.index[prices.index > 100], published_through=100, published={"value": published, "riskShares": shares or NOW}, annualization=252, shares=NOW, every="month")


def test_risk_drift_thresholds():
    """Volatility drifts above 1.5 times the published one (exactly 1.5 does not); a risk share drifts at more than 10 points (exactly 10 does not)."""
    realized = _after(1.0)[0]["value"]
    assert _after(realized / 1.51)[0]["drift"] is True and _after(realized / 1.49)[0]["drift"] is False
    import numpy as np
    import pandas as pd

    from modules.quant.backend import library_after
    now = _after(realized)[0]["shares"]["now"]
    for delta, drifting in ((0.10, False), (0.1001, True)):
        shares = {**now, "A": now["A"] + delta, "B": now["B"] - delta}
        assert _after(realized, shares)[0]["drift"] is drifting, delta
    assert library_after.DRIFT_VOL == 1.5 and library_after.DRIFT_SHARE == 0.10 and np and pd


def test_publish_plan_compares_with_previous_and_publish_end_to_end(tmp_path, monkeypatch):
    import json

    import pandas as pd

    from tests.unit.test_quant_allocation_runs import METRICS, enable_publishing, services, settled
    from tests.unit.test_quant_object_runs import DATA, INSTRUMENT
    INSTRUMENT_ID = INSTRUMENT["instrumentId"]

    async def behave(job, output):
        days = ["2024-01-31", "2024-02-29", "2024-03-29"]
        frame = pd.DataFrame({"date": days, "heldFrom": ["2024-02-01", "2024-03-01", "2024-04-01"], "w:BTC/USDT:USDT": [1.0, 1.0, 1.0], "r:BTC/USDT:USDT": [1.0, 1.0, 1.0],
                              "turnover": [0.0, 0.0, 0.0], "cost": [0.0, 0.0, 0.0], "status": "optimal", "note": "", "bound": ""})
        frame.to_parquet(output / "periods.parquet", index=False)
        work = {"metrics": METRICS, "equalMetrics": METRICS, "checks": [{"id": "binding", "name": "Limits that bind", "value": "80%", "need": "50% or less", "state": "fail"}], "notes": [],
                "periods": 3, "from": "2024-02-01", "to": "2024-06-28", "thisPeriod": [], "seconds": .1}
        (output / "work.json").write_text(json.dumps(work), encoding="utf-8")
        (output / "answer.json").write_text(json.dumps({"ok": True, "seconds": .1}), encoding="utf-8")

    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, behave)
        from tests.unit.quant_public import public_actions
        act = public_actions(ctx, shell, monkeypatch)
        seen, labels = enable_publishing(ctx, shell, runs, monkeypatch, tmp_path, INSTRUMENT["symbol"], INSTRUMENT_ID)
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        await shell.store.update_settings(created["id"], {"data":{**DATA,"heldOutFrom":"2024-05-01"}})
        first = await runs.start(created["id"])
        await settled(runs, first["id"])
        plan = await act("plan_publication", {"runId":first["id"]})
        assert (plan["version"], plan["previous"], plan["blocked"]) == (1, None, None) and plan["checks"][0]["name"] == "Limits that bind" and plan["firstPeriod"]["from"] == "2024-04-30"
        # a first version lists its own numbers: against 60/40 and equal weight, and the held-out part (no previous column)
        first_rows = {row["measure"]: row for row in plan["compare"]}
        assert first_rows["CAGR"]["current"] == METRICS["cagr"] and first_rows["CAGR"]["previous"] is None and first_rows["Held-out"]["current"] == "not validated"
        # A pool with a strategy run cannot be published, and the sheet shows why for the run as a whole, not under the name
        record = runs.read(first["id"])
        with monkeypatch.context() as patch:
            patch.setattr(runs, "read", lambda identity: {**record, "settings": {**record["settings"], "pool": [{"ticker": "One name on a signal #16", "kind": "run", "runId": "s1"}]}})
            blocked = runs.plan_publication(first["id"])["blocked"]
            assert blocked["field"] == "run" and blocked["reason"].startswith("One name on a signal #16 (a strategy run): a published allocation needs every asset")
        receipt = await act("publish_run", {"runId":first["id"],"name":"Mix"})
        await shell.runs.tasks[receipt["runId"]]
        published = shell.runs.read(receipt["runId"])["publish"]["versions"][0]
        done = {"versionId":published,"version":shell.library.store.version(published)["version"]}
        assert done["version"] == 1
        store = shell.library.store
        version = store.version(done["versionId"])
        assert version["meta"]["published"]["riskShares"] == {INSTRUMENT_ID: 1.0}  # worked out from the last weights and the bars, as the library's drift state does
        assert version["meta"]["published"]["cagr"] == METRICS["cagr"] and version["meta"]["published"]["objective"] is not None
        assert version["meta"]["producer"] == "allocation" and version["auto"] is True and len(store.read_values(done["versionId"], field="weight")) == 3
        assert len(seen) == 1 and len(seen[0]["cutoffs"]) == 3 and seen[0]["entry"] == "allocation_standard.py"  # the frozen standard code was run again at three cut points
        from modules.quant.backend.object_data import day_ms
        held = runs.read(first["id"])["settings"]["data"]["heldOutFrom"]
        assert held and all(cut < day_ms(held, False) for cut in seen[0]["cutoffs"]), "the future-data check cuts only before the held-out start"
        second = await runs.start(created["id"], {"changes": {"lookback": 126}})
        await settled(runs, second["id"])
        plan = runs.plan_publication(second["id"])
        assert plan["version"] == 2 and plan["previous"]["version"] == 1
        assert any(row["measure"] == "Sharpe" and row["change"] == 0 for row in plan["compare"])
        assert any(row["measure"] == "Held-out" and row["current"] == "not validated" for row in plan["compare"])  # the held-out part was never used for these settings
    asyncio.run(scenario())


def test_a_published_allocation_gains_a_period_at_the_next_rebalance_with_a_fake_clock(tmp_path, monkeypatch):
    """Published with new values on (the default): when the next period's first bar has opened, the library fits it and the version holds one more period."""
    import json

    import numpy as np
    import pandas as pd

    from modules.quant.backend import library_loop, research_data
    from modules.quant.backend.allocation_producer import AllocationProducer
    from tests.unit.test_quant_allocation_runs import METRICS, enable_publishing, services, settled
    from tests.unit.test_quant_object_runs import DATA, INSTRUMENT

    DAY = 86_400_000
    stamp = lambda text: int(pd.Timestamp(text, tz="UTC").value // 1_000_000)  # noqa: E731
    column = INSTRUMENT["symbol"]

    async def behave(job, output):
        frame = pd.DataFrame({"date": ["2024-01-31", "2024-02-29"], "heldFrom": ["2024-02-01", "2024-03-01"], f"w:{column}": [1.0, 1.0], f"r:{column}": [1.0, 1.0],
                              "turnover": [0.0, 0.0], "cost": [0.0, 0.0], "status": "optimal", "note": "", "bound": ""})
        frame.to_parquet(output / "periods.parquet", index=False)
        work = {"metrics": METRICS, "equalMetrics": METRICS, "checks": [], "notes": [], "periods": 2, "from": "2024-02-01", "to": "2024-03-28", "thisPeriod": [], "seconds": .1}
        (output / "work.json").write_text(json.dumps(work), encoding="utf-8")
        (output / "answer.json").write_text(json.dumps({"ok": True, "seconds": .1}), encoding="utf-8")

    async def scenario():
        ctx, shell, runs = services(tmp_path, monkeypatch, behave)
        enable_publishing(ctx, shell, runs, monkeypatch, tmp_path, INSTRUMENT["symbol"], INSTRUMENT["instrumentId"])
        created = await shell.store.create({"kind": "allocation", "name": "Mix"})
        await shell.store.update_settings(created["id"], {"data": DATA})
        first = await runs.start(created["id"])
        await settled(runs, first["id"])
        runs.update(first["id"], info={"timeframe": "1d", "since": stamp("2024-01-01"), "instruments": [{"column": column, "instrumentId": INSTRUMENT["instrumentId"]}],
                                        "annualization": 252, "calendarId": "24_7", "sessionScope": "continuous"})
        published = await runs.publish(first["id"])
        store = shell.library.store
        version = store.version(published["versionId"])
        assert version["auto"] is True and version["meta"]["producer"] == "allocation" and version["cursor"] == stamp("2024-03-01")
        assert version["meta"]["measure"] == "Volatility", "the main measure is the volatility it publishes, not the fit's risk measure (the list read variance)"

        bars_path = tmp_path / "bars.parquet"
        labels = [stamp("2024-03-01") + k * DAY for k in range(0, 33)]
        pd.DataFrame({"label": labels, "column": column, "close": np.linspace(100, 110, len(labels))}).to_parquet(bars_path, index=False)

        async def prepare(ctx_, data, owner, directory, strict=True, calendars="one"):
            return {"research": {"barsPath": str(bars_path), "info": version["meta"]["info"]}}
        monkeypatch.setattr(research_data, "prepare", prepare)
        asked = []

        async def call(op, job, seconds=120):
            asked.append((op, job["labels"], job["after"], job["every"]))
            return {"ok": True, "rows": [(label, column, "weight", 1.0) for label in job["labels"]] + [(label, column, "riskShare", 1.0) for label in job["labels"]], "solver": {}}
        monkeypatch.setattr(shell.factors.engine, "call", call)
        april_first = stamp("2024-04-01")
        now = april_first + DAY + DAY // 2

        async def calendar(version_, start, end):
            return [(label, label, label + DAY) for label in labels if start <= label <= end]
        loop = library_loop.LibraryLoop(ctx, shell.records, shell.library, SimpleNamespace(), clock=lambda: now, calendar=calendar,
                                        producer=lambda found: AllocationProducer(ctx, shell.library, shell.factors.engine))
        await loop.advance(published["versionId"])
        rows = store.read_values(published["versionId"])
        assert asked and asked[0][0] == "allocation_values" and asked[0][3] == "month"
        assert april_first in {row["datetime"] for row in rows} and store.version(published["versionId"])["cursor"] >= april_first
        assert len({row["datetime"] for row in rows}) > 2  # the version holds more periods than it was published with
        # The shares after publication are the producer's own (same window and measure as its weights), not worked out again from 63 bars.
        after = store.version(published["versionId"])["after"]
        assert after["shares"]["now"] == {} and after["settled"] == 0  # this version caught up after its due date
    asyncio.run(scenario())


def test_the_reviewed_period_is_where_the_held_weights_began_not_the_newest_daily_row():
    """Continued values repeat the held weights every day until the next rebalance: the period under review is the first day of those weights, so it stays the
    same all month (a review given for it is not lost the next day) and the trial can be fitted where the version fitted them."""
    from modules.quant.backend.allocation_runs import held_from
    day = lambda d: d * 86_400_000
    rows = [{"datetime": day(d), "instrument": name, "value": value} for d, weights in
            [(1, {"A": .5, "B": .5}), (2, {"A": .5, "B": .5}), (30, {"A": .2, "B": .8}), (31, {"A": .2, "B": .8}), (32, {"A": .2, "B": .8})] for name, value in weights.items()]
    assert held_from(rows) == day(30)
    assert held_from(rows[:4]) == day(1)
    assert held_from([]) is None
    # A weight that comes back to an earlier value later is a new period all the same
    back = rows + [{"datetime": day(60), "instrument": "A", "value": .5}, {"datetime": day(60), "instrument": "B", "value": .5}]
    assert held_from(back) == day(60)


def test_the_trial_for_a_reviewed_period_is_fitted_on_returns_before_its_weights_are_held(monkeypatch):
    import sys
    from pathlib import Path

    import pandas as pd
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
    from native import allocation_run
    days = pd.bdate_range("2026-09-01", "2026-11-06")
    returns = pd.DataFrame({"SPY": 0.001, "IEF": 0.0005}, index=days)
    seen = {}
    monkeypatch.setattr(allocation_run, "cached_returns", lambda job: (returns, {"cutShort": [], "proxies": {}, "spliced": [], "benchmark": None}))
    monkeypatch.setattr(allocation_run, "too_few_periods", lambda *args: None)
    monkeypatch.setattr(allocation_run.W, "check_feasible", lambda *args: None)
    monkeypatch.setattr(allocation_run.W, "check_settings", lambda *args: None)
    monkeypatch.setattr(allocation_run.W, "trial", lambda frame, settings, **kw: seen.setdefault("last", frame.index[-1]) and {"period": str(frame.index[-1].date()), "rows": []})
    monkeypatch.setattr(allocation_run, "spliced_text", lambda info: [])
    job = {"settings": {"pool": [{"ticker": "SPY"}, {"ticker": "IEF"}], "lookback": 21}, "data": {"barsPath": "x", "info": {"annualization": 252}}, "riskFree": {"basis": "zero"}}
    allocation_run.trial({**job, "heldFrom": int(pd.Timestamp("2026-11-02").value // 1_000_000)})
    assert seen.pop("last") == pd.Timestamp("2026-10-30")   # the month-end close the weights held from Nov 2 were fitted at
    allocation_run.trial(job)
    assert seen.pop("last") == pd.Timestamp("2026-10-30")   # no period under review: the newest rebalance close, where the backtest's last period is fitted
    allocation_run.trial({**job, "heldOutFrom": "2026-10-01"})
    assert seen.pop("last") == pd.Timestamp("2026-09-30")   # never inside the held-out part


def test_a_period_that_falls_back_because_an_asset_did_not_move_names_that_asset_in_words():
    """A strategy run out of the market (its equity flat over the lookback) has zero variance and the optimizer refuses the period: the note says which asset
    did not move instead of the optimizer's own message with a Python list in it."""
    import sys
    from pathlib import Path

    import numpy as np
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules" / "quant"))
    from native.allocation_work import why_not_fitted
    days = pd.bdate_range("2025-07-01", periods=63)
    window = pd.DataFrame({"SPY": np.linspace(-.01, .01, 63), "One name on a signal #16": 0.0}, index=days)
    error = ValueError("The following assets have a non positive variance: ['One name on a signal #16']")
    assert why_not_fitted(window, error) == "One name on a signal #16 did not move over the lookback"
    moving = window.assign(**{"One name on a signal #16": np.linspace(.01, -.01, 63)})
    assert why_not_fitted(moving, RuntimeError("Solver failed\nmore")) == "Solver failed"
    assert why_not_fitted(moving, RuntimeError("")) == "RuntimeError"


def test_the_published_period_and_its_weights_stay_put_while_the_library_repeats_them_daily():
    from types import SimpleNamespace

    from modules.quant.backend.allocation_runs import AllocationRuns
    day = lambda d: d * 86_400_000
    history = [(1, {"a": .5, "b": .5}), (30, {"a": .19, "b": .81}), (31, {"a": .19, "b": .81}), (34, {"a": .19, "b": .81})]
    rows = [{"datetime": day(d), "instrument": name, "field": "weight", "value": value, "availableAt": day(d)} for d, weights in history for name, value in weights.items()]
    store = SimpleNamespace(
        all_versions=lambda: [{"id": "v1", "object_id": "o1", "archived_at": None, "state": "live", "item_id": "i1", "version": 1, "run_id": "r1", "meta": {"reviews": {str(day(30)): {"how": "accepted"}}}, "created_at": 0}],
        summary_kind=lambda item: "allocation", summary_name=lambda version: "Mix v1", clock=lambda: day(40),
        latest_available=lambda version, as_of, field=None: [row for row in rows if row["datetime"] == day(34)],
        read_values=lambda version, field=None, as_of=None: rows)
    runs = AllocationRuns.__new__(AllocationRuns)
    runs.library = SimpleNamespace(store=store)
    runs.records = SimpleNamespace(find=lambda kind, identity: {"settings": {"pool": [{"ticker": "SPY", "instrumentId": "a"}, {"ticker": "IEF", "instrumentId": "b"}]}})
    found = runs.published("o1")
    assert found["period"] == day(30) and found["weights"] == {"SPY": .19, "IEF": .81}
    assert found["review"] == {"how": "accepted"}   # the review given for the period is still there days later


def test_an_update_after_publication_carries_the_published_risk_shares_not_zeros(tmp_path, monkeypatch):
    """The publication keeps the weights at its cursor but no risk shares: the first updates carried zeros forward, so every asset's risk read as gone and
    the version was flagged off its published shape (−82 pt on the largest share) on each update until the next rebalance."""
    import pandas as pd

    from modules.quant.backend import research_data
    from modules.quant.backend.allocation_producer import AllocationProducer
    bars = tmp_path / "bars.parquet"
    pd.DataFrame({"label": [1_000, 2_000, 3_000], "column": "A", "close": [1.0, 1.1, 1.2]}).to_parquet(bars, index=False)

    async def prepare(ctx_, data, owner, directory, strict=True, calendars="one"):
        return {"research": {"barsPath": str(bars), "info": {}}}
    monkeypatch.setattr(research_data, "prepare", prepare)
    held = {"weights only": pd.DataFrame({"instrument": ["iA", "iB"], "field": ["weight", "weight"], "value": [0.6, 0.4]}),
            "zeros carried": pd.DataFrame({"instrument": ["iA", "iB", "iA", "iB"], "field": ["weight", "weight", "riskShare", "riskShare"], "value": [0.6, 0.4, 0.0, 0.0]})}
    seen = []

    class Engine:
        async def call(self, op, job, seconds=120):
            seen.append(job["last"]["riskShares"])
            return {"ok": True, "rows": [(3_000, "A", "weight", 0.6)], "solver": {}}

    for case, frame in held.items():
        store = SimpleNamespace(frame=lambda version_id, datetime=None, columns=None, field=None, frame=frame: frame if field is None else frame[frame["field"] == field].assign(datetime=2_000))
        producer = AllocationProducer(SimpleNamespace(), SimpleNamespace(version_directory=lambda identity: tmp_path / identity, store=store), Engine())
        version = {"id": "v1", "meta": {"data": {}, "info": {"since": 0, "annualization": 252, "instruments": [{"column": "A", "instrumentId": "iA"}, {"column": "B", "instrumentId": "iB"}]},
                                         "allocation": {"settings": {"objective": "min_risk", "risk_free": "zero"}, "pool": []}, "published": {"riskShares": {"iA": 0.7, "iB": 0.3}}}}
        asyncio.run(producer.make(version, through=3_000, after=2_000, now=5, attempt=0))
        assert seen[-1] == {"A": 0.7, "B": 0.3}, case


def test_after_publication_an_allocation_has_its_turnover_a_year_sharpe_and_return_against_its_benchmark():
    """The draft's table (library.js): turnover a year from the rebalances since publication (the new weights against the last ones drifted with the
    returns since, as the backtest counts it), the Sharpe since, and the return since against the benchmark's (60/40 when its run had one, else the
    pool in equal weights); none of them before there is anything after publication to count."""
    import numpy as np
    import pandas as pd

    from modules.quant.backend import library_after
    days = [int(stamp.value // 1_000_000) for stamp in pd.date_range("2026-01-01", "2026-04-30", freq="D")]
    prices = pd.DataFrame({"A": [100 * 1.01 ** n for n in range(len(days))], "B": [100.0] * len(days)}, index=days)
    published_through = int(pd.Timestamp("2026-02-15").value // 1_000_000)
    firsts = [day for day in days if pd.Timestamp(day, unit="ms").day == 1]
    labels = [day for day in days if day in firsts or day > published_through]  # history at its rebalances, then a row each day as the library keeps
    values = pd.DataFrame([(day, asset, weight) for day in labels for asset, weight in (("A", 0.8), ("B", 0.2))], columns=["datetime", "instrument", "value"])
    published = {"value": 0.1, "riskShares": {"A": 0.8, "B": 0.2}}
    figures, _ = library_after.allocation(values, prices, scheduled=[day for day in days if day > published_through], published_through=published_through, published={**published, "vsEqualWeight": -0.05}, annualization=252,
                                          shares={"A": 0.8, "B": 0.2}, every="month", benchmark={"name": "equal weight", "key": "vsEqualWeight", "weights": None})

    def drifted(closes, target=(0.8, 0.2)):
        """0.8 / 0.2 set on the first of each month and drifting until the next, as the backtest holds it."""
        returns, held, out = closes.pct_change().fillna(0.0), None, []
        for day, row in returns.iterrows():
            if held is not None:
                growth = float(held @ row.to_numpy())
                out.append((day, growth))
                held = held * (1 + row.to_numpy()) / (1 + growth)
            if day in firsts:
                held = np.array(target)
        return pd.Series(dict(out))

    def turnover(previous, label):
        grown = 0.8 * 1.01 ** (days.index(label) - days.index(previous))  # A's part, drifted from the last rebalance to this one
        return abs(grown / (grown + 0.2) - 0.8)
    march, april = firsts[2], firsts[3]
    assert figures["rebalances"] == 2, "March 1 and April 1; February 1 was before publication"
    assert figures["turnover"] == pytest.approx((turnover(firsts[1], march) + turnover(march, april)) / 2 * 12)
    equal = drifted(prices, (0.5, 0.5))
    mine = drifted(prices)
    assert figures["benchmark"] == {"name": "equal weight", "published": -0.05, "since": pytest.approx(float((1 + mine[mine.index > published_through]).prod()) - (1 + equal[equal.index > published_through]).prod())}
    noisy = prices.assign(B=[100 * (1 + 0.01 * np.sin(n)) for n in range(len(days))])
    figures, _ = library_after.allocation(values, noisy, scheduled=[day for day in days if day > published_through], published_through=published_through, published={**published, "vs6040": 0.02}, annualization=252, every="month",
                                          shares={"A": 0.8, "B": 0.2},
                                          benchmark=library_after.benchmark_of({"published": {"vs6040": 0.02}, "allocation": {"pool": [{"ticker": "SPY", "instrumentId": "A"},
                                                                                                                                     {"ticker": "IEF", "instrumentId": "B"}]}}))
    since = drifted(noisy)
    since = since[since.index > published_through]
    assert figures["sharpe"] == pytest.approx(since.mean() / since.std() * np.sqrt(252))
    them = drifted(noisy, (0.6, 0.4))
    them = them[them.index > published_through]
    assert figures["benchmark"]["name"] == "60/40" and figures["benchmark"]["published"] == 0.02
    assert figures["benchmark"]["since"] == pytest.approx(float((1 + since).prod() - (1 + them).prod()))
    assert library_after.benchmark_of({"published": {"vs6040": None}, "allocation": {"pool": [{"ticker": "SPY", "instrumentId": "A"}]}})["name"] == "equal weight"
    early, _ = library_after.allocation(values[values["datetime"] <= published_through + 86_400_000], prices, scheduled=[day for day in days if day > published_through], published_through=published_through, published=published,
                                        annualization=252, every="month", shares={"A": 0.8, "B": 0.2}, benchmark={"name": "equal weight", "key": "vsEqualWeight", "weights": None})
    assert early["turnover"] is None and early["rebalances"] == 0, "no rebalance since publication yet"


def test_an_update_of_a_pool_in_another_currency_reads_the_rate_and_the_proxy_its_run_read(tmp_path, monkeypatch):
    """The run converts a euro asset with EURUSD=X and fills its early history from its proxy; the published version's updates did neither, so the backtest
    was in dollars and the updates in euros. The update now asks Market for the same two and hands the worker the same column maps."""
    import pandas as pd

    from modules.quant.backend import allocation_runs, research_data
    from modules.quant.backend.allocation_producer import AllocationProducer
    bars = tmp_path / "bars.parquet"
    pd.DataFrame({"label": [1_000, 2_000], "column": "A", "close": [1.0, 1.1]}).to_parquet(bars, index=False)
    asked = {}

    async def resolve(ctx, text):
        return {"instrumentId": "i" + text, "symbol": text}

    async def prepare(ctx_, data, owner, directory, strict=True, calendars="one"):
        asked["symbols"] = [item["instrument"]["instrumentId"] for item in data["symbols"]]
        return {"research": {"barsPath": str(bars), "info": {"instruments": [{"column": "A", "instrumentId": "iA"}, {"column": "B", "instrumentId": "iB"},
                                                                             {"column": "FX", "instrumentId": "iEURUSD=X"}, {"column": "P", "instrumentId": "iVGK"}]}}}
    monkeypatch.setattr(research_data, "prepare", prepare)
    monkeypatch.setattr(allocation_runs, "resolve_text", resolve)

    class Engine:
        async def call(self, op, job, seconds=120):
            asked["settings"] = job["settings"]
            return {"ok": True, "rows": [(2_000, "A", "weight", 1.0)], "solver": {}}
    producer = AllocationProducer(SimpleNamespace(), SimpleNamespace(version_directory=lambda identity: tmp_path / identity, store=SimpleNamespace()), Engine())
    pool = [{"ticker": "A", "instrumentId": "iA", "currency": "USD"}, {"ticker": "B", "instrumentId": "iB", "currency": "EUR", "proxy": "VGK"}]
    version = {"id": "v1", "meta": {"data": {"symbols": [{"instrument": {"instrumentId": "iA"}}, {"instrument": {"instrumentId": "iB"}}]},
                                     "info": {"since": 0, "annualization": 252, "instruments": [{"column": "A", "instrumentId": "iA"}, {"column": "B", "instrumentId": "iB"}]},
                                     "allocation": {"settings": {"currency": "USD", "objective": "min_risk", "risk_free": "zero"}, "pool": pool}}}
    asyncio.run(producer.make(version, through=2_000, after=None, now=5, attempt=0))
    assert set(asked["symbols"]) == {"iA", "iB", "iVGK", "iEURUSD=X"}
    assert asked["settings"]["fxColumns"] == {"B": {"column": "FX", "invert": False}} and asked["settings"]["proxyColumns"] == {"B": "P"}


def test_only_a_period_the_library_made_after_publication_waits_for_review_and_its_reminder_opens_the_allocation(tmp_path):
    """at publication the newest period is the run's own, so the review set it beside itself (every change 0) and the home page sent the user to
    the library item, while the review is done in the allocation's Work."""
    def version(through):
        store = LibraryStore(tmp_path / f"library-{through}", clock=lambda: 5 * MONTH)
        store.commit_versions([{"name": "Mix", "kind": "allocation", "versionId": "v1",
                                "meta": {"fields": ["weight"], "timeframe": "1d", "net": 1.0, "rebalance": {"every": "month"}, "publishedThrough": through, "origin": {"objectId": "alloc-1"}},
                                "rows": rows(MONTH, [.5, .4, .1], MONTH) + rows(2 * MONTH, [.5, .45, .05], 2 * MONTH), "objectId": "alloc-1", "runId": "r",
                                "workspaceId": "ws", "state": "updating", "cursor": 2 * MONTH, "checks": {}}])
        summary = store.summaries()
        items = summary["items"] if isinstance(summary, dict) else summary
        runs = reviewing(store)
        return [entry for entry in library_summary.attention(items)[0] if entry["label"] == "Review this period"], runs.published("alloc-1")
    reminders, published = version(2 * MONTH)
    assert reminders == [] and "period" not in published and "review" not in published
    reminders, published = version(MONTH)
    assert published["period"] == 2 * MONTH and reminders[0]["action"] == {"id":"review_period","period":2*MONTH,"label":"Review this period","objectId":"alloc-1"}


def test_symbol_review_replaces_frozen_instrument_ids_as_of_the_review(tmp_path):
    pool = [{"ticker": ticker, "instrumentId": identity} for ticker, identity in zip(["S", "I", "B"], POOL, strict=True)]
    store = store_with_version(tmp_path, lambda: 3 * MONTH, allocation={"pool": pool})
    runs = reviewing(store)
    args = {"versionId": "v1", "period": 2 * MONTH, "how": "one_off", "reason": "Review"}
    with pytest.raises(ContractError, match="every pool asset"):
        asyncio.run(runs.review({**args, "weights": {"S": 1}}))
    asyncio.run(runs.review({**args, "weights": {"S": .45, "I": .55, "B": 0}}))
    for at, expected in [(2 * MONTH, [.5, .45, .05]), (3 * MONTH, [.45, .55, 0])]:
        assert {r["instrument"]: r["value"] for r in store.latest_available("v1", at)} == dict(zip(POOL, expected, strict=True))
        from modules.quant.backend.library import Library
        ranked = Library.values_page(SimpleNamespace(store=store), "v1", date=2 * MONTH, as_of=at, field="weight")
        assert len(ranked["rows"]) == 3
        assert sum(row["value"] for row in ranked["rows"]) == pytest.approx(1)


def test_ambiguous_ticker_and_duplicate_references_need_distinct_frozen_assets():
    meta = {"allocation": {"pool": [{"instrumentId": "asset-a", "ticker": "FUND"}, {"instrumentId": "asset-b", "ticker": "FUND"}]},
            "info": {"instruments": [{"instrumentId": "asset-a", "column": "US:FUND"}, {"instrumentId": "asset-b", "column": "UK:FUND"}]}}
    ids = {"asset-a", "asset-b"}
    with pytest.raises(ContractError, match="frozen pool instrument ID"):
        AllocationRuns.adjusted_weights(meta, ids, {"FUND": 1.0})
    with pytest.raises(ContractError, match="Duplicate weight"):
        AllocationRuns.adjusted_weights(meta, ids, {"asset-a": .5, "US:FUND": .5})
    assert AllocationRuns.adjusted_weights(meta, ids, {"US:FUND": .45, "asset-b": .55}) == {"asset-a": .45, "asset-b": .55}
