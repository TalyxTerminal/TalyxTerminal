"""The research library's store and item page: versions in production or stopped, who may stop them, archiving, values a day at a time,
the list's states, how publishing finds future data; and the research lists, notices and calendars around it."""
import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from talyx_sdk import ContractError

from modules.quant.backend.object_store import check_data
from modules.quant.backend.research_lists import ResearchLists
from modules.quant.backend.work_records import WorkRecords
from tests.fixtures.quant_index import frozen_index
from tests.unit.quant_library_fakes import DAY, NAMES, build, close_of, factor_spec
from tests.unit.quant_runtime_fakes import adapt


def spec(name="Momentum", kind="factor", version_id="v1", rows=None, state="updating", cursor=3 * DAY, fields=("factor",)):
    rows = rows if rows is not None else [("AAPL", "factor", day * DAY, float(day), (day + 1) * DAY, 10 * DAY, "history") for day in (1, 2, 3)]
    return {"name": name, "kind": kind, "meta": {"fields": list(fields), "timeframe": "1d"}, "rows": rows, "objectId": "o", "runId": "r", "workspaceId": "ws",
            "state": state, "cursor": cursor, "checks": {"value": 0.1}, "versionId": version_id}


def test_same_name_again_is_a_new_version_and_a_failed_commit_leaves_nothing(tmp_path):
    from modules.quant.backend.library_store import LibraryStore
    store = LibraryStore(tmp_path)
    first = store.commit_versions([spec()])[0]
    second = store.commit_versions([spec(version_id="v2")])[0]
    assert (first["version"], second["version"], first["itemId"]) == (1, 2, second["itemId"])
    with pytest.raises(sqlite3.IntegrityError):
        store.commit_versions([spec(name="Other", version_id="v3"), spec(name="Third", version_id="v1")])
    assert [item["name"] for item in store.summaries()] == ["Momentum"] and store.read_values("v3") == []
    assert not (store.values_root / "v3").exists()


def test_a_models_predictions_and_trained_model_are_one_item_kind_and_another_kind_cannot_take_its_name(tmp_path):
    from modules.quant.backend.library_store import LibraryStore
    store = LibraryStore(tmp_path)
    store.commit_versions([spec("M", "prediction", "p", fields=("score",))])
    assert store.find_item("M", "model")[0]["kind"] == "model" and store.find_item("M", "prediction")[0]["kind"] == "model"
    with pytest.raises(ContractError, match="already a model"):
        store.commit_versions([spec("M", "factor", "f")])


def test_values_know_when_they_could_be_known_and_latest_gives_each_instrument_its_newest_known_value(tmp_path):
    from modules.quant.backend.library_store import LibraryStore
    store = LibraryStore(tmp_path)
    rows = [("A", "factor", 1 * DAY, 1.0, 2 * DAY, 9, "history"), ("A", "factor", 2 * DAY, 2.0, 3 * DAY, 9, "history"),
            ("B", "factor", 2 * DAY, 5.0, 3 * DAY + 100, 9, "catch_up")]
    store.commit_versions([spec(rows=rows)])
    assert [row["value"] for row in store.read_values("v1", as_of=2 * DAY)] == [1.0]
    assert {row["instrument"]: row["value"] for row in store.latest_available("v1", 3 * DAY)} == {"A": 2.0}
    assert {row["instrument"]: row["value"] for row in store.latest_available("v1", 4 * DAY)} == {"A": 2.0, "B": 5.0}
    assert store.days("v1") == [(DAY, 1), (2 * DAY, 2)]


def test_a_version_research_uses_can_stop_its_users_are_told_and_a_stopped_one_takes_no_new_user(tmp_path):
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    world.library.acquire("v1", "qobject:s1", "ws", kind="strategy", name="Momentum top 50", how="research, pinned v1")
    preview = world.library.stop("v1", dry_run=True)
    assert [user["name"] for user in preview["users"]] == ["Momentum top 50"] and "Momentum top 50 keep v1" in preview["consequence"]
    assert world.store.version("v1")["state"] == "updating", "a dry run changes nothing"
    stopped = world.library.stop("v1")
    assert stopped["stopped"] and "Momentum top 50 keep v1" in stopped["consequence"]
    assert world.store.version("v1")["state"] == "delisted" and world.store.continuing() == []
    with pytest.raises(ContractError, match="stopped version"):
        world.library.acquire("v1", "qobject:s2", "ws", kind="strategy")
    told = [record for record in world.records.list("qlibevent") if record["type"] == "library_version_stopped"]
    assert [record["owner"] for record in told] == ["qobject:s1"]


def test_archiving_needs_a_stopped_version_nobody_uses_and_drops_its_values_but_keeps_the_record(tmp_path):
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    directory = world.library.version_directory("v1")
    (directory / "source").mkdir(parents=True)
    (directory / "source" / "factor.py").write_text("x = 1\n", encoding="utf-8")
    (directory / "models").mkdir()
    with pytest.raises(ContractError, match="Stop this version first"):
        asyncio.run(world.library.archive("v1"))
    world.library.acquire("v1", "qobject:s1", "ws", kind="strategy", name="Study")
    world.library.stop("v1")
    with pytest.raises(ContractError, match="Study"):
        asyncio.run(world.library.archive("v1"))
    world.library.release("v1", "qobject:s1")
    archived = asyncio.run(world.library.archive("v1"))
    assert archived["rows"] == 50 * len(NAMES) and world.store.read_values("v1") == [] and world.store.days("v1") == []
    assert not (world.store.values_root / "v1").exists() and not (directory / "models").exists() and (directory / "source" / "factor.py").is_file()
    version = world.library.read_version("v1")
    assert version["archivedAt"] is not None and version["published"]["value"] == 0.02
    with pytest.raises(ContractError, match="archived"):
        world.library.values_page("v1")
    with pytest.raises(ContractError, match="archived"):
        world.store.resume("v1")
    row = world.store.summary_of(world.store.find_item("Momentum")[0]["id"])
    assert row["state"] == "stopped" and row["versions"][0]["state"] == "archived"


def test_a_resumed_version_makes_the_periods_it_missed_known_from_then(tmp_path):
    """produced again, a stopped version fills the periods due while it was stopped, as turning auto-update back on does; each is known from the
    moment it was made (no look-ahead) and its log says it was stopped when due."""
    world = build(tmp_path, start_day=50)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    world.library.stop("v1")
    world.clock["now"] = close_of(60) + 6 * 60_000
    world.library.resume("v1")
    asyncio.run(world.loop.cycle())
    days = [label // DAY for label, _ in world.store.days("v1")]
    periods = world.store.periods("v1")
    assert days[-1] == 60 and 55 in days and [period["label"] // DAY for period in periods] == list(range(51, 61))
    assert all(period["result"] == "late" and period["why"].startswith("Stopped when it was due") for period in periods[:-1])
    filled = [row for row in world.store.read_values("v1") if row["datetime"] // DAY == 55]
    assert filled and all(row["availableAt"] >= world.clock["now"] - 60_000 and row["mode"] == "catch_up" for row in filled)


def test_auto_off_keeps_the_values_and_shows_auto_off_and_only_a_producing_version_has_the_switch(tmp_path):
    world = build(tmp_path)
    created = world.store.commit_versions([factor_spec("Momentum", "v1", 50)])[0]
    world.library.set_auto("v1", False)
    assert world.store.continuing() == [] and world.store.summary_of(created["itemId"])["state"] == "auto_off"
    world.library.stop("v1")
    with pytest.raises(ContractError, match="producing version"):
        world.library.set_auto("v1", True)


def test_one_off_data_is_its_own_state_and_says_what_it_is(tmp_path):
    world = build(tmp_path)
    rows = [{"datetime": day * DAY, "instrument": "i:A", "field": "window", "value": 1.0} for day in range(3)]
    created = asyncio.run(world.library.publish_values({"name": "Earnings windows", "rows": rows}))
    row = world.store.summary_of(created["itemId"])
    assert row["state"] == "one_off" and row["oneOff"] and row["freq"] == "One-off" and not row["collecting"]
    assert world.library.detail(created["itemId"])["versions"][0]["settingsText"] == "Put in from a notebook; it does not update."


def test_the_list_puts_problems_first_counts_them_and_lists_stopped_versions_nobody_uses(tmp_path):
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Quiet", "q1", 50)])
    world.store.commit_versions([factor_spec("Broken", "b1", 50)])
    world.store.commit_versions([factor_spec("Old", "o1", 50)])
    world.store.put_periods("b1", [{"label": 51 * DAY, "due_at": close_of(51), "result": "fail", "why": "KeyError: 'x'"}], state="failed")
    world.library.stop("o1")
    listed = world.library.list({})
    assert [item["name"] for item in listed["items"]][0] == "Broken" and listed["needsAttention"] == 1
    assert [item["versionId"] for item in listed["archivable"]] == ["o1"]
    assert listed["items"][0]["last20"][-1] == {"period": 51 * DAY, "result": "fail", "readyAt": None, "dueAt": close_of(51), "why": "KeyError: 'x'"}
    assert [item["name"] for item in world.library.list({"state": "attention"})["items"]] == ["Broken"]


def test_a_day_of_values_reads_that_day_ranks_it_pages_it_and_names_who_has_none(tmp_path):
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    page = world.library.values_page("v1")
    assert page["date"] == 50 * DAY and page["have"] == page["total"] == len(NAMES) and [row["rank"] for row in page["rows"]] == list(range(1, len(NAMES) + 1))
    assert page["rows"][0]["value"] >= page["rows"][-1]["value"] and page["rows"][0]["previous"] is not None and page["previousDay"] == 49 * DAY
    bottom = world.library.values_page("v1", date=10 * DAY + 5, order="bottom")
    assert bottom["date"] == 10 * DAY and bottom["rows"][0]["rank"] == len(NAMES)
    assert [row["name"] for row in world.library.values_page("v1", query="C")["rows"]] == ["C"]


def test_a_name_the_data_has_only_after_publication_is_shown_by_its_symbol_and_counted(tmp_path):
    # the version's universe is a rule (index members each day); a continuation's fresh data can hold a member the published run's data lacked.
    world = build(tmp_path, start_day=50)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    world.world.joined = [{"column": "Z", "instrumentId": "i:Z", "displaySymbol": "ZZ", "source": {"sourceId": "fake", "name": "Fake"}}]
    world.clock["now"] = close_of(51) + 6 * 60_000
    asyncio.run(world.loop.cycle())
    page = world.library.values_page("v1", date=51 * DAY)
    assert page["date"] == 51 * DAY and page["have"] == page["total"] == len(NAMES) + 1
    assert "ZZ" in [row["name"] for row in page["rows"]] and "i:Z" not in [row["name"] for row in page["rows"]]
    assert world.store.version("v1")["meta"]["instrumentCount"] == len(NAMES) + 1
    before = world.library.values_page("v1", date=50 * DAY)
    assert before["have"] == len(NAMES) and before["missing"] == 1


def test_a_comparison_names_how_alike_two_versions_are_and_the_code_and_settings_that_differ(tmp_path):
    world = build(tmp_path)
    first = factor_spec("Momentum", "v1", 50)
    second = factor_spec("Momentum", "v2", 60, item_seed="other")
    first["meta"]["formula"], second["meta"]["formula"] = "rank($close)", "rank(-$close)"
    second["meta"]["settings"] = {"holding": 5}
    world.store.commit_versions([first])
    world.store.commit_versions([second])
    compared = world.library.compare("v2", "v1")
    assert compared["a"]["version"] == 1 and compared["b"]["version"] == 2
    assert compared["overlap"]["days"] == 50 and -1 <= compared["overlap"]["rankCorrelation"] <= 1 and compared["overlap"]["onlyA"] == 0
    assert compared["code"][0]["file"] == "formula" and {"what": "run.holding", "from": None, "to": 5} in compared["settings"]
    world.store.commit_versions([factor_spec("Other", "x1", 10)])
    with pytest.raises(ContractError, match="same item"):
        world.library.compare("v1", "x1")


def test_the_item_page_says_what_is_wrong_since_when_and_what_it_affects(tmp_path):
    from modules.quant.backend import library_summary
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Earnings revision", "f1", 50)])
    world.store.commit_versions([factor_spec("Cross-sectional 5D", "m1", 50, kind="model", inputs=[{"itemId": None, "versionId": "f1", "name": "Earnings revision", "version": 1}])])
    world.library.acquire("m1", "qobject:s1", "ws", kind="strategy", name="Model top 50", how="research, pinned v1")
    world.world.failing["f1"] = 10 ** 15
    world.clock["now"] = close_of(51) + 6 * 60_000
    asyncio.run(world.loop.cycle())
    page = world.library.detail(world.store.find_item("Earnings revision")[0]["id"])
    issue = page["issue"]
    assert issue["kind"] == "fail" and issue["title"] == "Failed 1 period" and issue["note"].endswith("has no values"), "library.js: Failed N periods in a row, the days with no values"
    reason = next(row["text"] for row in issue["rows"] if row["label"] == "Reason")
    assert "epsTrend" in reason and reason.endswith(" in revision.py line 8.")
    tried = next(row["text"] for row in issue["rows"] if row["label"] == "Tried")
    assert tried.startswith("1 time since ") and " UTC; " in tried
    assert any(row["label"] == "Downstream" and "Cross-sectional 5D v1" in row["text"] for row in issue["rows"])
    assert page["failures"][0]["traceback"].endswith("KeyError: 'epsTrend'") and page["failures"][0]["location"] == {"file": "revision.py", "line": 8}
    assert page["feeds"][0]["name"] == "Cross-sectional 5D" and page["feeds"][0]["users"][0]["kind"] == "strategy"
    model = world.library.detail(world.store.find_item("Cross-sectional 5D")[0]["id"])
    assert model["summary"]["state"] == "up" and "Earnings revision v1 has no" in model["periods"][-1]["why"]
    assert model["inputs"][0]["health"] == "fail"
    # library.js: "Upstream missing 1 period", the day with no prediction, which input has no value and why, who goes without.
    up = model["issue"]
    assert up["title"] == "Upstream missing 1 period" and up["note"].endswith(": no prediction")
    missing = next(row["text"] for row in up["rows"] if row["label"] == "Missing input")
    assert missing.startswith("Earnings revision v1 has no value for ") and ", it failed; an input with no new value for a period counts as missing" in missing
    assert any(row["label"] == "Downstream" and row["text"] == "nobody" for row in up["rows"]), "a research object pinned on it is not downstream"
    attention, _ = library_summary.attention(world.store.summaries())
    assert [entry["subject"]["name"] for entry in attention] == ["Earnings revision"], "an input that failed itself is listed alone"


def test_each_notice_is_a_declared_setting_and_a_switched_off_one_is_not_sent(tmp_path):
    """The switches are Quant's settings (the settings window lists them, a workspace can have its own); a notice whose switch reads off is
    not sent."""
    import json as jsonlib

    from modules.quant.backend.notifications import DECLARED, Notifications
    settings = jsonlib.loads((Path(__file__).resolve().parents[2] / "modules" / "quant" / "public" / "contract.json").read_text(encoding="utf-8"))["settings"]
    assert [key for key in settings] == [item["id"] for item in DECLARED]
    assert all(settings[item["id"]]["default"] is True and settings[item["id"]]["group"] == "Notifications: " + item["group"] for item in DECLARED)
    sent = []
    async def toast(value):
        sent.append(value)
    ctx = adapt(SimpleNamespace(platform=SimpleNamespace(notifications=SimpleNamespace(toast=toast, channels=lambda value: asyncio.sleep(0, []), send=toast))))
    ctx.quant_settings = {"libraryWeakening": False}
    notifications = Notifications(ctx, WorkRecords(tmp_path))
    asyncio.run(notifications.deliver("libraryWeakening", "weak:v1", "Weakening", "v1 fell"))
    asyncio.run(notifications.deliver("libraryFailures", "failed:v1", "Failed", "v1 failed"))
    assert [item["title"] for item in sent] == ["Failed"]


def frame(rows):
    return pd.DataFrame(rows, columns=["datetime", "instrument", "field", "value"])


def test_a_value_that_changes_when_the_data_is_cut_names_the_time_it_changed_at():
    from modules.quant.backend.library_publish import first_difference, stamp
    history = frame([(DAY, "A", "f", 1.0), (2 * DAY, "A", "f", 2.0), (3 * DAY, "A", "f", 3.0)])
    assert first_difference(history, frame([(DAY, "A", "f", 1.0), (2 * DAY, "A", "f", 2.0)]), 2 * DAY) is None
    found = first_difference(history, frame([(DAY, "A", "f", 1.0), (2 * DAY, "A", "f", 2.5)]), 2 * DAY)
    assert found["row"]["datetime"] == 2 * DAY and found["row"]["valueCut"] == 2.5 and found["count"] == 1
    gone = first_difference(history, frame([(DAY, "A", "f", 1.0)]), 2 * DAY)
    assert gone["row"]["datetime"] == 2 * DAY and np.isnan(gone["row"]["valueCut"])
    assert stamp(2 * DAY, "1d") == "1970-01-03" and stamp(DAY + 3_600_000, "1h") == "1970-01-02 01:00 UTC"


def test_rows_name_exact_instruments_and_refuse_symbols_the_run_never_had():
    from modules.quant.backend.library_publish import long_rows, long_table
    values = frame([(DAY, "AAPL", "f", 1.0)])
    assert long_rows(values, {"AAPL": "x:AAPL"}, {DAY: 2 * DAY}, 9, "history") == [("x:AAPL", "f", DAY, 1.0, 2 * DAY, 9, "history")]
    table = long_table(values, {"AAPL": "x:AAPL"}, {DAY: 2 * DAY}, 9, "history")
    assert table.iloc[0].to_dict() == {"instrument": "x:AAPL", "field": "f", "datetime": DAY, "value": 1.0, "availableAt": 2 * DAY, "recordedAt": 9, "mode": "history"}
    with pytest.raises(ContractError, match="MSFT"):
        long_rows(frame([(DAY, "MSFT", "f", 1.0)]), {"AAPL": "x:AAPL"}, {DAY: 2 * DAY}, 9, "history")


def test_calendar_counts_bars_per_year_labels_daily_bars_by_local_date_and_dates_values_by_bar_close():
    from modules.quant.backend import research_calendar
    hour = 3_600_000
    assert research_calendar.annualization([(n * hour, (n + 1) * hour) for n in range(8766)], 0, 8766 * hour) == 8766
    new_york = 1_704_160_800_000  # 2024-01-02 02:00 UTC is still 2024-01-01 evening in New York
    assert research_calendar.label_of(new_york, "1d", "America/New_York") == 1_704_067_200_000
    assert research_calendar.label_of(new_york, "1h", "America/New_York") == new_york
    with pytest.raises(ContractError):
        research_calendar.annualization([], 0, 1)


def test_data_settings_choose_symbols_one_way_and_strategies_have_fixed_price_basis():
    symbol = {"instrument": {"instrumentId": "i"}}
    check_data({"symbols": [symbol], "timeframe": "1d", "adjustment": "adjusted"}, "factor")
    check_data({"list": "l", "universe": None}, "factor")
    check_data({"universe": "code", "timeframe": "1d"}, "model")
    with pytest.raises(ContractError, match="one way"):
        check_data({"symbols": [symbol], "list": "l"}, "factor")
    with pytest.raises(ContractError, match="strategy"):
        check_data({"universe": "code"}, "strategy")
    with pytest.raises(ContractError, match="Strategy prices are fixed"):
        check_data({"adjustment": "adjusted"}, "strategy")


def test_research_lists_keep_exact_members_rename_and_refuse_deletion_while_selected(tmp_path):
    events = []
    async def get(args):
        return {"items":[{"status":"found","symbol":item["displaySymbol"],"instrument":item} for item in (args.get("records") or [{"instrumentId":i,"displaySymbol":"AAPL","venueId":"xnas"} for i in args["symbols"]])],"nextCursor":None}
    ctx = adapt(SimpleNamespace(events=SimpleNamespace(publish=lambda name, payload: events.append(name)),
                          require=lambda name: SimpleNamespace(get=get)))
    lists = ResearchLists(ctx, WorkRecords(tmp_path))
    used = []
    lists.used_by = lambda identity: used

    async def scenario():
        member = {"instrumentId": "i1", "displaySymbol": "AAPL", "venueId": "xnas"}
        made = await lists.create({"name": " Large caps ", "members": [{"instrument": member}, {"instrument": member}]})
        assert made["name"] == "Large caps" and made["count"] == 1 and made["members"][0]["displaySymbol"] == "AAPL"
        renamed = await lists.update(made["id"], {"name": "Tech"})
        assert renamed["name"] == "Tech" and lists.members(made["id"]) == [{"instrument": member}]
        used.append("Momentum")
        with pytest.raises(ContractError, match="Momentum"):
            lists.delete(made["id"])
        used.clear()
        lists.delete(made["id"])
        with pytest.raises(ContractError, match="does not exist"):
            lists.read(made["id"])
        with pytest.raises(ContractError, match="Each member"):
            await lists.create({"name": "Bad", "members": [{"nope": 1}]})
    asyncio.run(scenario())
    assert events == ["research_lists_changed"] * 3


def test_a_library_failure_notice_is_sent_once_retried_when_the_service_fails_and_follows_the_setting(tmp_path):
    from modules.quant.backend.notifications import Notifications
    sent, failing = [], [True]

    async def toast(args):
        if failing[0]:
            raise ContractError("unavailable", "service down")
        sent.append(("toast", args["title"]))

    async def channels(args):
        return [{"id": "c1", "enabled": True, "configured": True}, {"id": "c2", "enabled": False, "configured": True}]

    async def send(args):
        sent.append(("send", args["channelIds"], args["notificationId"]))

    ctx = adapt(SimpleNamespace(platform=SimpleNamespace(notifications=SimpleNamespace(toast=toast, channels=channels, send=send))))
    records = WorkRecords(tmp_path)
    notifications = Notifications(ctx, records)

    async def scenario():
        await notifications.deliver("libraryFailures", "n1", "Quant", "body")
        assert sent == [] and records.find("qnotice", "n1")["status"] == "retrying"
        failing[0] = False
        records.put("qnotice", {**records.find("qnotice", "n1"), "nextAt": 0})
        await notifications.resend()
        await notifications.deliver("libraryFailures", "n1", "Quant", "body")
        ctx.quant_settings = {"libraryFailures": False}     # switched off in the settings window
        await notifications.deliver("libraryFailures", "n2", "Quant", "body")
    asyncio.run(scenario())
    assert sent == [("toast", "Quant"), ("send", ["c1"], "n1")] and records.find("qnotice", "n1")["status"] == "sent" and records.find("qnotice", "n2") is None


def test_a_library_failure_reaches_the_publishing_and_using_workspaces_as_trigger_facts(tmp_path):
    records = WorkRecords(tmp_path)
    facts = []
    records.on_trigger = facts.append
    for workspace in ("ws1", "ws2"):
        records.put("qlibevent", {"id": f"library_update_failed:v1:5:{workspace}", "type": "library_update_failed", "workspaceId": workspace, "itemId": "i", "versionId": "v1",
                                  "name": "Momentum", "version": 1, "message": "Market refused"})
    assert [(fact["workspaceId"], fact["type"], fact["payload"]["name"]) for fact in facts] == [("ws1", "quant.library_update_failed", "Momentum"), ("ws2", "quant.library_update_failed", "Momentum")]
    head = records.triggers.head("library", {"source": "quant:library", "workspaceId": "ws1"})
    assert records.triggers.read("library", {"scope": {"source": "quant:library", "workspaceId": "ws1"}, "after": records.triggers.token("library", "ws1", 0), "through": head["cursor"], "limit": 10})["items"][0]["object"]["kind"] == "library"


def test_every_update_is_logged_for_its_version_and_its_item_newest_first(tmp_path):
    from modules.quant.backend.library_store import LibraryStore
    store = LibraryStore(tmp_path)
    created = store.commit_versions([spec()])[0]
    store.commit_versions([spec(version_id="v2")])
    store.append_values("v1", [("A", "factor", 4 * DAY, 4.0, 5 * DAY, 11 * DAY, "scheduled")], cursor=4 * DAY)
    store.log_update("v1", "failed", "Market refused")
    store.append_values("v1", [("A", "factor", 5 * DAY, 5.0, 12 * DAY, 12 * DAY, "catch_up"), ("A", "factor", 6 * DAY, 6.0, 12 * DAY, 12 * DAY, "catch_up")], cursor=6 * DAY)
    log = store.updates(item_id=created["itemId"])
    assert [(item["outcome"], item["values"], item["mode"]) for item in log] == [("updated", 2, "catch_up"), ("failed", 0, None), ("updated", 1, "scheduled")]
    assert log[0]["firstDatetime"] == 5 * DAY and log[0]["lastDatetime"] == 6 * DAY and log[1]["message"] == "Market refused"
    assert store.updates(version_id="v2") == [] and len(store.updates(version_id="v1", limit=2)) == 2


def test_concurrent_updates_cannot_commit_the_same_library_period_twice(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from modules.quant.backend.library_store import LibraryStore
    store = LibraryStore(tmp_path)
    store.commit_versions([spec()])
    rows = [('AAPL', 'factor', 4 * DAY, 4.0, 5 * DAY, 11 * DAY, 'scheduled')]
    def advance():
        try:
            return store.append_values('v1', rows, cursor=4 * DAY, expected_cursor=3 * DAY)
        except ContractError as error:
            return error.code
    with ThreadPoolExecutor(2) as workers:
        results = list(workers.map(lambda _: advance(), range(2)))
    assert results.count(True) == 1 and results.count('conflict') == 1
    assert len(store.read_values('v1')) == 4 and len(store.updates(version_id='v1')) == 1


def test_report_tables_name_their_assets_for_a_backtest_and_for_research(tmp_path):
    from modules.quant.backend.object_runs import ObjectRuns
    (tmp_path / "info.json").write_text(json.dumps({"instruments": [], "sources": [{"nativeInstrument": "BTCUSDT.OKX", "instrument": {"displaySymbol": "BTC/USDT"}}]}), encoding="utf-8")
    backtest = {"objectKind": "strategy", "data": {"infoPath": str(tmp_path / "info.json")}}
    research = {"objectKind": "factor", "data": {"instruments": [{"column": "BTC/USDT", "displaySymbol": "BTC/USDT"}, {"column": "AAPL@xnas", "displaySymbol": "AAPL"}]}}
    assert ObjectRuns.asset_labels(backtest) == {"BTCUSDT.OKX": "BTC/USDT"}
    assert ObjectRuns.asset_labels(research) == {"BTC/USDT": "BTC/USDT", "AAPL@xnas": "AAPL"}
    assert ObjectRuns.asset_labels({"objectKind": "factor", "data": {"universe": "code"}}) == {}


def test_a_strategys_annualization_is_the_days_of_its_calendar_counted_by_market():
    from modules.quant.backend.object_data import daily_annualization

    async def boundaries(args):
        days = 365 if args["calendarId"] == "24_7" else 252
        start = int(args["start"])
        return {"evidence": "verified", "reason": None, "timezone": "UTC", "next": None,
                "open": [str(start + n * 1000000) for n in range(days)], "close": [str(start + (n + 1) * 1000000) for n in range(days)]}

    ctx = adapt(SimpleNamespace(require=lambda name: SimpleNamespace(bar_times=boundaries, sessions=lambda args: asyncio.sleep(0, {"timezone": "UTC"}))))
    year = 365 * DAY
    selection = lambda calendar: {"instrument": {"calendarId": calendar}, "series": {"sessionScope": "regular"}}
    assert asyncio.run(daily_annualization(ctx, [selection("XNAS")], 0, year)) == 252
    assert asyncio.run(daily_annualization(ctx, [selection("24_7")], 0, year)) == 365
    assert asyncio.run(daily_annualization(ctx, [selection("XNAS"), selection("24_7")], 0, year)) == 365


def test_the_editor_moves_to_the_new_lock_when_packages_are_installed(monkeypatch):
    from modules.quant.backend.development_support import SourceLanguage
    digest = ["one"]
    prepared = []

    async def prepare(self, kind, effective):
        prepared.append(effective["digest"])
        self.ready[kind] = (effective["digest"], {"interpreterPath": "python-" + effective["digest"]})

    monkeypatch.setattr(SourceLanguage, "prepare", prepare)
    ctx = adapt(SimpleNamespace(track=lambda release: None))
    packages = SimpleNamespace(effective=lambda kind: {"digest": digest[0], "lock": "", "origin": {}})
    support = SourceLanguage(ctx, None, None, None, packages)

    async def scenario():
        assert support.environment("research") is None  # preparing in the background: paths only for now
        await asyncio.sleep(0)
        assert support.environment("research") == {"interpreterPath": "python-one"}
        support.environment("research")
        digest[0] = "two"  # packages installed: new work uses another lock
        assert support.environment("research") == {"interpreterPath": "python-one"}  # the old one until the new is ready
        await asyncio.sleep(0)
        assert support.environment("research") == {"interpreterPath": "python-two"}
    asyncio.run(scenario())
    assert prepared == ["one", "two"]


# ---------------------------------------------------------------------------------------------------------------------------------------
# L segment: what the new store and production add (archiving frees more, sixty settled days of the symbol's calendar, missing then late,
# stopping what a model reads, same-value periods, index members, shared freezes, the old catalog left alone, the data checks D2, D3, D7).
# ---------------------------------------------------------------------------------------------------------------------------------------
def test_archive_drops_window_models_and_frozen_environment(tmp_path):
    world = build(tmp_path)
    released = []

    async def release(args):
        released.append(args)
    world.ctx.platform.python_env = SimpleNamespace(release=release)
    spec_ = factor_spec("Momentum", "v1", 50)
    spec_["meta"]["environment"] = {"preparationId": "prep-1", "interpreterPath": "python"}
    world.store.commit_versions([spec_])
    directory = world.library.version_directory("v1")
    for folder in ("windows/0", "updates/1"):
        (directory / folder).mkdir(parents=True)
    (directory / "windows" / "0" / "model.bin").write_bytes(b"x")
    (directory / "source").mkdir()
    (directory / "source" / "factor.py").write_text("x = 1\n", encoding="utf-8")
    world.library.stop("v1")
    asyncio.run(world.library.archive("v1"))
    assert not (directory / "windows").exists() and not (directory / "updates").exists() and (directory / "source" / "factor.py").is_file()
    assert released == [{"preparation_id": "prep-1", "owner_id": "library:v1"}]
    assert world.library.read_version("v1")["published"]["value"] == 0.02


def test_an_archived_version_cannot_be_read(tmp_path):
    """an object still pointing at a version that was archived is told so, and which version to switch to, when it runs."""
    from modules.quant.backend import library_inputs
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Reversal", "v1", 50)])
    world.store.commit_versions([factor_spec("Reversal", "v2", 50, item_seed="Reversal")])
    world.library.stop("v1")
    asyncio.run(world.library.archive("v1"))
    sources = [{"marketInstrument": {"instrumentId": NAMES[0]}, "nativeInstrument": "A.X"}]
    with pytest.raises(ContractError, match=r"Reversal v1 was archived; its values are gone\. Switch to v2\.") as refused:
        asyncio.run(library_inputs.prepare(world.library, [("signal", "Reversal")], sources, DAY, 10 * DAY, "1d", tmp_path / "x.parquet", versions={"signal": "v1"}))
    assert refused.value.code == "not_found"


def test_the_new_store_never_opens_the_old_catalog(tmp_path):
    old = tmp_path / "records" / "library"
    old.mkdir(parents=True)
    connection = sqlite3.connect(old / "library.sqlite3")
    connection.execute("CREATE TABLE items(id TEXT, name TEXT, kind TEXT)")
    connection.execute("INSERT INTO items VALUES ('old', 'Old momentum', 'factor')")
    connection.commit()
    connection.close()
    before = (old / "library.sqlite3").read_bytes()
    world = build(tmp_path)
    assert world.library.root.name == "library2" and world.library.list({})["items"] == []
    assert (old / "library.sqlite3").read_bytes() == before and not (world.library.root / "old").exists()


@pytest.mark.parametrize(("timeframe", "per_day"), [("1d", 1), ("1h", 24)])
def test_after_figures_wait_for_sixty_trading_days_settled(tmp_path, timeframe, per_day):
    """Sixty trading days with a settled period, however many bars a day holds (no table of calendars to fall back from)."""
    world = build(tmp_path)
    made = factor_spec("Momentum", "v1", 50)
    made["meta"]["timeframe"] = timeframe
    made["meta"]["info"] = {**made["meta"]["info"], "timeframe": timeframe, "calendarId": "XYZ"}
    item = world.store.commit_versions([made])[0]["itemId"]
    world.store.mark("v1", after={"kind": "signal", "value": 0.02, "se": 0.001, "settled": 59 * per_day, "settledDays": 59, "pending": 5})
    row = world.store.summary_of(item)
    assert row["collecting"] and row["after"] is None and row["settled"] == 59 * per_day and row["settledDays"] == 59 and row["pending"] == 5
    world.store.mark("v1", after={"kind": "signal", "value": 0.02, "se": 0.001, "settled": 60 * per_day, "settledDays": 60, "pending": 5})
    row = world.store.summary_of(item)
    assert not row["collecting"] and row["after"] == 0.02


@pytest.mark.parametrize(("after", "settled", "weak"), [(0.0240, 60, True), (0.0260, 60, False), (-0.0100, 60, True), (-0.0940, 60, True), (0.0240, 59, False), (-0.0940, 59, False)])
def test_weak_needs_sixty_settled_days_same_sign_under_a_quarter_or_the_opposite_sign(tmp_path, after, settled, weak):
    """D3: published 0.10; 0.24 times it and the same sign after 60 settled days is Weaker, 0.26 times it is not; the opposite sign is Weaker however
    large (a factor published at 0.0032 that ran at −0.094 after publication was "On time"); under 60 settled days neither is."""
    world = build(tmp_path)
    item = world.store.commit_versions([factor_spec("Momentum", "v1", 50, published=0.10)])[0]["itemId"]
    world.store.mark("v1", after={"kind": "signal", "value": after, "se": 0.002, "settled": settled, "settledDays": settled, "pending": 0})
    row = world.store.summary_of(item)
    assert (row["state"] == "weak") is weak and (row["stateLabel"] == "Weaker" or not weak)


def test_after_figures_equal_a_direct_computation_on_synthetic_data():
    """D2: 50 names for 300 days, published after day 200, the factor the next day's return plus noise, held 5; 100 days made since."""
    from modules.quant.backend import library_after
    from tests.unit.quant_library_fakes import synthetic_after
    data = synthetic_after()
    figures, _points = library_after.measure(data.values, data.prices, scheduled=data.prices.index[data.prices.index > data.published_through], holding=5, delay=0, annualization=252)
    forward = data.prices.shift(-5) / data.prices - 1
    table = data.values.pivot(index="datetime", columns="instrument", values="value")
    settled = [label for index, label in enumerate(data.labels) if label > data.published_through and index + 5 <= len(data.labels) - 1]
    direct = table.loc[settled].corrwith(forward.loc[settled], axis=1, method="spearman")
    assert abs(figures["value"] - direct.mean()) < 1e-9
    assert (figures["settled"], figures["pending"]) == (95, 5)


def test_missing_then_late_when_the_bar_comes(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = build(tmp_path, start_day=60)
    world.store.commit_versions([factor_spec("Momentum", "v1", 60)])
    world.world.late[61] = 3  # the source gives day 61's bar three hours after its close
    world.clock["now"] = close_of(61) + DELAY + 60_000
    for _ in range(8):
        asyncio.run(world.loop.cycle())
        world.clock["now"] += 3 * 60_000
    period = world.store.periods("v1")[-1]
    assert (period["label"], period["result"]) == (61 * DAY, "miss") and "source" in period["why"] and world.store.frame("v1", datetime=61 * DAY).empty
    world.clock["now"] = close_of(61) + 4 * 3_600_000
    asyncio.run(world.loop.cycle())
    period = world.store.periods("v1")[-1]
    assert (period["label"], period["result"], period["why"]) == (61 * DAY, "late", "The source gave the bar late")
    values = world.store.frame("v1", datetime=61 * DAY)
    assert set(values["mode"]) == {"catch_up"} and (values["availableAt"] == values["recordedAt"]).all() and (values["availableAt"] >= close_of(61)).all()


def test_five_missing_in_a_row_stop_the_version(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = build(tmp_path, start_day=60)
    created = world.store.commit_versions([factor_spec("Momentum", "v1", 60)])[0]
    world.world.gaps.update(range(61, 66))
    for day in range(61, 66):
        world.clock["now"] = close_of(day) + DELAY + 60_000
        for _ in range(6):
            asyncio.run(world.loop.cycle())
            world.clock["now"] += 3 * 60_000
    assert world.store.version("v1")["state"] == "expired" and world.store.continuing() == []
    row = world.store.summary_of(created["itemId"])
    assert (row["state"], row["stateCount"], row["stateLabel"]) == ("miss", 5, "Missing")
    issue = world.library.detail(created["itemId"])["issue"]
    # library.js: "Missing N periods", the period, the reason; a stopped version has no next settlement.
    assert issue["title"] == "Missing 5 periods" and {"label": "Reason", "text": "No new bars from the source for 5 periods; production stopped"} in issue["rows"]
    assert all(row["label"] != "Next settlement" for row in issue["rows"])
    # Checking the source now cannot restart a stopped version: the home row opens it in the library instead.
    from modules.quant.backend import library_summary
    (attention,), _ = library_summary.attention(world.store.summaries())
    assert attention["action"] == {"id": "open_version", "label": "Open in the library"}


def test_auto_on_again_fills_the_missed_periods_as_late(tmp_path):
    world = build(tmp_path, start_day=60)
    world.store.commit_versions([factor_spec("Momentum", "v1", 60)])
    world.library.set_auto("v1", False)
    world.clock["now"] = close_of(63) + 6 * 3_600_000
    asyncio.run(world.loop.cycle())
    assert world.store.frame("v1", datetime=62 * DAY).empty, "nothing is made while auto is off"
    world.library.set_auto("v1", True)
    asyncio.run(world.loop.cycle())
    periods = {period["label"] // DAY: period for period in world.store.periods("v1")}
    assert {day: periods[day]["result"] for day in (61, 62, 63)} == {61: "late", 62: "late", 63: "late"}
    assert set(world.store.frame("v1", datetime=62 * DAY)["mode"]) == {"catch_up"}


def test_stopping_a_factor_a_published_model_reads_warns_and_its_periods_become_upstream_missing(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = build(tmp_path, start_day=60)
    world.store.commit_versions([factor_spec("Breadth", "f1", 60)])
    world.store.commit_versions([{**factor_spec("Blend", "m1", 60, kind="model", inputs=[{"versionId": "f1", "name": "Breadth", "version": 1}]),
                                  "holds": [{"versionId": "f1"}]}])
    preview = world.library.stop("f1", dry_run=True)
    model = next(user for user in preview["users"] if user["name"] == "Blend v1")
    assert model["kind"] == "model" and model["readsDaily"] and model["unattended"]
    world.library.stop("f1")
    world.clock["now"] = close_of(61) + DELAY + 60_000
    asyncio.run(world.loop.cycle())
    period = world.store.periods("m1")[-1]
    assert period["result"] == "up" and "Breadth v1 has no" in period["why"] and world.store.frame("m1", datetime=61 * DAY).empty


def test_a_period_where_every_name_has_the_same_value_fails(tmp_path):
    from modules.quant.backend.library_loop import DELAY
    world = build(tmp_path, start_day=60)
    created = world.store.commit_versions([factor_spec("Momentum", "v1", 60)])[0]
    world.world.constant.add(61)
    world.clock["now"] = close_of(61) + DELAY + 60_000
    asyncio.run(world.loop.cycle())
    period = world.store.periods("v1")[-1]
    assert (period["label"], period["result"]) == (61 * DAY, "fail") and period["why"].startswith("Every name has the same value")
    assert world.store.frame("v1", datetime=61 * DAY).empty and world.store.summary_of(created["itemId"])["state"] == "fail"


def test_the_item_page_for_a_one_off_has_no_production_log(tmp_path):
    world = build(tmp_path)
    rows = [{"datetime": day * DAY, "instrument": "i:A", "field": "window", "value": 1.0} for day in range(3)]
    created = asyncio.run(world.library.publish_values({"name": "Earnings windows", "rows": rows}))
    page = world.library.detail(created["itemId"])
    assert page["summary"]["state"] == "one_off" and page["periods"] == [] and page["versions"][0]["settingsText"] == "Put in from a notebook; it does not update."


def test_two_versions_share_the_unchanged_frozen_revision_after_serialized_verification(tmp_path, monkeypatch):
    """Every request verifies current data; concurrent publications keep one immutable version when the revisions agree."""
    from modules.quant.backend.factor_data import FactorData
    data = FactorData(None, tmp_path / "data", None, None, clock=lambda: 100.)
    state = {"revision": "r1", "active": 0, "most": 0}
    async def resolve(settings):
        return settings
    async def prepare(key, settings, report, directory, calendars="one", facts=True):
        state["active"] += 1
        state["most"] = max(state["most"], state["active"])
        await asyncio.sleep(.01)
        directory.mkdir()
        (directory / "bars.parquet").write_text(state["revision"])
        state["active"] -= 1
        return {"key": key, "fingerprint": state["revision"], "frozenAt": 100000, "how": "full"}
    monkeypatch.setattr(data, "resolve", resolve)
    monkeypatch.setattr(data, "key", lambda settings, calendars, facts=True: "same-selection")
    monkeypatch.setattr(data, "_prepare", prepare)
    async def unchanged(ready):
        state["checked"] = state.get("checked", 0) + 1
        return ready["fingerprint"] == state["revision"]
    monkeypatch.setattr(data, "unchanged", unchanged)
    async def both():
        return await asyncio.gather(data.prepare({}), data.prepare({}))
    first, second = asyncio.run(both())
    assert state["most"] == 1 and first["key"] == second["key"] and {first["how"], second["how"]} == {"full", "unchanged"}
    state["revision"] = "r2"
    current = asyncio.run(data.prepare({}))
    assert current["fingerprint"] == "r2" and current["frozenAt"] > first["frozenAt"]
    retired = next(data.root.glob("same-selection~*"))
    assert (retired / "bars.parquet").read_text() == "r1"


def test_a_freeze_whose_revisions_market_still_has_is_used_without_fetching(tmp_path, monkeypatch):
    """the same settings again (only the formula changed) went through Market name by name, exported and arranged everything,
    then found nothing had changed (89-105 s); now one batch of revision reads decides."""
    from modules.quant.backend import research_data
    from modules.quant.backend.factor_data import FactorData
    data = FactorData(None, tmp_path / "data", None, None, clock=lambda: 100.)
    instrument = {"instrumentId": "i:A", "adjustmentEligible": True}
    adjusted = {"seriesId": "A-adj"}
    info = {"timeframe": "1d", "until": 5, "instruments": [{"instrument": instrument, "series": adjusted}], "revisions": {"A-adj": 3, "A-raw": 7}}
    monkeypatch.setattr(research_data.M, "price_series", lambda instrument, timeframe, adjusted: {"seriesId": "A-raw"})
    now = {"A-adj": 3, "A-raw": 7}
    async def revisions(ctx, selections, until):
        assert until == 5 and {item["series"]["seriesId"] for item in selections} == {"A-adj", "A-raw"}
        return {key: {"revision": value, "source": "s"} for key, value in now.items()}
    monkeypatch.setattr(research_data.M, "revisions", revisions)
    assert asyncio.run(data.unchanged({"info": info}))
    now["A-raw"] = 8  # a corporate action or a corrected bar
    assert not asyncio.run(data.unchanged({"info": info}))


def test_an_update_follows_the_index_members_of_that_day(tmp_path, monkeypatch):
    """and D7: on synthetic bars, three of ten members are swapped on day 250; an update's values on a day count exactly the members that have a price."""
    from modules.quant.native import factor_resident as engine

    def one_day_return(text, panel):  # the formula engine needs Qlib, which lives in the research environment; here a stand-in gives the same shape
        close = panel.fields["close"]
        out = np.full_like(close, np.nan)
        out[1:] = close[1:] / close[:-1] - 1
        return out, {}
    monkeypatch.setattr(engine, "formula_values", one_day_return)
    days, names = 300, 13
    columns = [f"S{index:02d}" for index in range(names)]
    rng = np.random.default_rng(4)
    rows = []
    for day in range(days):
        members = set(range(0, 10)) if day < 250 else set(range(3, 13))
        for index, column in enumerate(columns):
            if (day == 299 and index == 5) or (index >= 10 and day < 200):
                continue  # no price: name 5 on the last day; the entering names before they listed
            close = 100 + rng.normal() + day * .1
            rows.append((day * DAY, column, close, close, close, close, 1000.0, index in members))
    bars = pd.DataFrame(rows, columns=["label", "column", "open", "high", "low", "close", "volume", "isMember"])
    path = tmp_path / "bars.parquet"
    bars["rawClose"], bars["rawVolume"] = bars["close"], bars["volume"]
    bars.to_parquet(path, index=False)
    info = {**{key: value for key, value in __import__("tests.unit.quant_library_fakes", fromlist=["INFO"]).INFO.items() if key != "instruments"},
            "instruments": [{"column": column, "instrumentId": f"x:{column}", "displaySymbol": column, "sourceId": "fake"} for column in columns]}
    output = tmp_path / "out"
    output.mkdir()
    job = {"data": {"key": "d7", "barsPath": str(path), "info": info, "extrasPath": None, "libraryPath": None}, "formula": "$close / Ref($close, 1) - 1", "after": 248 * DAY, "output": str(output)}
    answer = engine.handle({"op": "formula_values", "job": job})
    assert answer["ok"]
    made = pd.read_parquet(answer["path"])
    for day in (249, 250, 299):
        members = set(range(0, 10)) if day < 250 else set(range(3, 13))
        priced = {index for index, column in enumerate(columns) if ((bars["label"] == day * DAY) & (bars["column"] == column)).any()}
        got = {columns.index(column) for column in made.loc[made["label"] == day * DAY, "column"]}
        assert got == members & priced, f"day {day}: values only for that day's members that have a price"


def test_two_index_freezes_read_independent_market_queries_concurrently(monkeypatch):
    from modules.quant.backend import index_universe, materials
    seen = {"now": 0, "most": 0}
    async def task(*args, **kwargs):
        return {"failures": []}
    monkeypatch.setattr(materials, "run_task", task)
    class Facts:
        async def read(self, args):
            seen["now"] += 1
            seen["most"] = max(seen["most"], seen["now"])
            await asyncio.sleep(.01)
            seen["now"] -= 1
            return {"columns":[{"name":n} for n in ("instrumentId","symbol","start","end","knownAt")],"rows":[["i1","A","1970-01-01",None,"1970-01-01"]],
                    "meta":[], "note": None, "nextCursor": None, "unmapped": 0}
        async def get(self, args):
            return {"items":[{"instrument":{"instrumentId":"i1","displaySymbol":"A"},"status":"found","symbol":"A"}],"nextCursor":None}
    ctx = adapt(SimpleNamespace(require=lambda name: Facts()))
    selection = {"instrument": {"instrumentId": "index"}}
    async def both():
        return await asyncio.gather(index_universe.freeze(ctx, selection, 0, 10 * DAY), index_universe.freeze(ctx, selection, 0, 20 * DAY))
    first, second = asyncio.run(both())
    assert seen["most"] == 2 and first["instruments"] and second["instruments"]


def test_a_weaker_version_says_published_against_after_same_sign_under_a_quarter(tmp_path):
    """library.js: "Weaker since publication", how many settled days after the last bar the publication held, the measure published and after."""
    import json
    world = build(tmp_path)
    world.store.commit_versions([factor_spec("Reversal 1M", "w1", 50)])
    with world.store.connect() as db:
        db.execute("UPDATE versions SET after=? WHERE id='w1'", (json.dumps({"kind": "signal", "value": 0.003, "se": 0.0011, "t": 2.7, "settled": 70, "settledDays": 70, "pending": 5}),))
    world.store.resummarize()
    issue = world.library.detail(world.store.find_item("Reversal 1M")[0]["id"])["issue"]
    assert issue["kind"] == "weak" and issue["title"] == "Weaker since publication" and issue["note"].startswith("70 settled days after ")
    assert issue["rows"][0]["text"] == "published +0.0200, after +0.0030 ± 0.0011: same sign, under a quarter"
    assert issue["rows"][-1] == {"label": "Downstream", "text": "nobody"}
    with world.store.connect() as db:
        db.execute("UPDATE versions SET after=? WHERE id='w1'", (json.dumps({"kind": "signal", "value": -0.094, "se": 0.0011, "t": -2.7, "settled": 70, "settledDays": 70, "pending": 5}),))
    world.store.resummarize()
    issue = world.library.detail(world.store.find_item("Reversal 1M")[0]["id"])["issue"]
    assert issue["kind"] == "weak" and issue["rows"][0]["text"] == "published +0.0200, after −0.0940 ± 0.0011: opposite sign"
    summary = world.store.summary_of(world.store.find_item("Reversal 1M")[0]["id"])
    assert world.loop.weak_words(summary) == "it has the opposite sign of what was published", "the notification says why too"


def test_periods_due_before_the_version_existed_are_made_at_publication_not_late(tmp_path):
    """an allocation published from a run whose data end long before is filled up to now at once; each such period read "Late, TALYX was not open"
    and a real late one could not be told apart. They are known only from publication on."""
    world = build(tmp_path, start_day=60)
    world.store.commit_versions([factor_spec("Momentum", "v1", 50)])
    asyncio.run(world.loop.cycle())
    periods = {period["label"] // DAY: period for period in world.store.periods("v1")}
    assert all(periods[day]["result"] == "ok" and periods[day]["why"].startswith("Made at publication") for day in range(51, 61))
    assert set(world.store.frame("v1", datetime=55 * DAY)["availableAt"]) == {world.clock["now"]}
    # Due after publication and made a day late because TALYX was closed: late, as before.
    world.clock["now"] = close_of(62) + 6 * 60_000
    asyncio.run(world.loop.cycle())
    late = {period["label"] // DAY: period for period in world.store.periods("v1")}[61]
    assert late["result"] == "late" and late["why"] == "TALYX was not open when it was due"


def test_a_version_keeps_the_index_summary_not_its_membership(tmp_path):
    """a version published from index data keeps the index's summary (counts, selection) in its record; the whole membership,
    tens of MB for a large index, was parsed on every read of the version and nothing in the library reads it. The exclusions (up to 22 MB)
    are kept beside its values."""
    world = build(tmp_path)
    spec = factor_spec("Momentum", "v1", 50)
    member = {"assetId": "a1", "instrumentId": "i:A", "effectiveTime": 0, "effectiveUntil": 50 * DAY}
    gone = {"assetId": "a2", "instrumentId": "i:B", "symbol": "B", "since": 0, "until": DAY, "kind": "no_prices", "reason": "none"}
    index = {"selection": {"instrument": {"instrumentId": "idx"}}, "since": 0, "until": 50 * DAY, "exclusions": [gone], "instruments": [{"instrumentId": "i:A"}],
             "unmappedCount": 0, "intervals": [{"since": 0, "until": 50 * DAY, "members": [member]}]}
    index = frozen_index(index)
    spec["meta"]["info"] = {**spec["meta"]["info"], "index": index}
    world.store.commit_versions([spec])
    kept = world.store.version("v1")["meta"]["info"]["index"]
    assert "intervals" not in kept and kept["memberCount"] == 1 and kept["mappedCount"] == 1 and kept["selection"] == index["selection"]
    assert kept["spans"] == {"i:A": [[0, 50 * DAY]]}
    assert "exclusions" not in kept and kept["excludedCount"] == 1 and world.store.exclusions("v1") == [gone]


def test_a_name_without_a_value_says_which_reason_it_is(tmp_path):
    """a day's names without a value were each "not a member, no price, or left out by the settings"; the version knows which.
    One who was not in the index that day is not among them at all; one with no price says the source's reason; the rest say the factor gave none."""
    world = build(tmp_path)
    spec = factor_spec("Momentum", "v1", 50)
    spec["rows"] = [row for row in spec["rows"] if not (row[2] == 50 * DAY and row[0] in {"i:A", "i:B", "i:C"})]
    def member(name, until):
        return {"assetId": name, "instrumentId": name, "effectiveTime": 0, "effectiveUntil": until}
    members = [member(name, 40 * DAY if name == "i:A" else 50 * DAY) for name in NAMES]
    index = {"selection": {"instrument": {"instrumentId": "idx"}}, "since": 0, "until": 50 * DAY, "instruments": [{"instrumentId": name} for name in NAMES],
             "exclusions": [{"assetId": "i:B", "instrumentId": "i:B", "symbol": "B", "since": 45 * DAY, "until": 50 * DAY, "kind": "no_data", "reason": "The free source has no prices for this past member"}],
             "unmappedCount": 0, "intervals": [{"since": 0, "until": 40 * DAY, "members": members}, {"since": 40 * DAY + 1, "until": 50 * DAY, "members": members[1:]}]}
    index = frozen_index(index)
    spec["meta"]["info"] = {**spec["meta"]["info"], "index": index}
    world.store.commit_versions([spec])
    page = world.library.values_page("v1", date=50 * DAY, order="missing")
    assert (page["missing"], page["total"]) == (2, len(NAMES) - 1)
    assert {row["instrumentId"]: row["why"] for row in page["rows"]} == {"i:B": "No price that day: The free source has no prices for this past member",
                                                                          "i:C": "A member with prices that day; the factor gave no value for it"}
