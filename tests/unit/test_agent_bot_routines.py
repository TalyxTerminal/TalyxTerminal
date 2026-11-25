import asyncio
from datetime import UTC, datetime

from langchain_core.messages import AIMessage
from talyx_sdk import ContractError

from modules.agent.backend.bots.records import group_handoff, recipients
from modules.agent.backend.bots.schedules import due_run, next_due
from tests.unit.test_agent_runtime import WORKSPACE, agent, settled


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def test_weekday_wake_tracks_new_york_dst_and_coalesces_missed_occurrences():
    rule = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=0;BYSECOND=0"
    zone = "America/New_York"
    start = utc("2027-03-01T00:00:00")
    friday = next_due(rule, zone, start, utc("2027-03-12T00:00:00"))
    assert friday == utc("2027-03-12T14:00:00")
    monday = next_due(rule, zone, start, friday)
    assert monday == utc("2027-03-15T13:00:00")
    routine = {
        "id": "weekday",
        "name": "Morning",
        "rrule": rule,
        "zone": zone,
        "start": start,
        "nextDue": friday,
    }
    for now in [utc("2027-03-12T15:15:00"), utc("2027-03-17T15:15:00")]:
        wake, following = due_run(routine, now, True, False)
        assert wake["kind"] == "catchup" and wake["due"] == friday and following > now
        assert due_run({**routine, "nextDue": following}, now, True, False)[0] is None
        assert due_run(routine, now, True, True)[0]["kind"] == "skipped"


def test_group_round_limit_notifies_once_and_only_named_members_wake():
    group = {"rounds": 0, "limitNotified": False}
    assert recipients(["A", "B"], [], None, user=True) == ["A", "B"]
    assert recipients(["A", "B"], [], None, user=False) == []
    assert recipients(["A", "B"], ["A"], "B", user=True) == ["A", "B"]
    assert all(group_handoff(group, 200) == (True, False) for _ in range(199))
    assert group_handoff(group, 200) == (True, True)
    assert group_handoff(group, 200) == (False, False)
    assert group_handoff(group, 200) == (False, False)
    group.update(rounds=0, limitNotified=False)
    assert group_handoff(group, 200) == (True, False)


def test_bot_wake_queue_combines_five_events_and_quiet_run_stays_out_of_chat(tmp_path):
    from modules.agent.backend.bots.api import dispatch

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="NO_REPLY")]) as (service, _):
            bots = service.bots
            key = bots.make_bot(WORKSPACE, {"name": "Review", "model": "model"})["id"]
            conversation = bots.line(WORKSPACE, bots.entry(WORKSPACE, key), key)
            with service.store.transaction():
                for n in range(5):
                    bots.worker.queue(
                        WORKSPACE,
                        key,
                        key,
                        conversation,
                        {
                            "kind": "event",
                            "event": "Changed",
                            "routineId": "r1",
                            "routine": "Review",
                            "events": [{"price": n}],
                        },
                        "Review prices",
                        100 + n,
                    )
            await bots.worker.dispatch(WORKSPACE)
            await settled(service, conversation)
            await bots.worker.finish(WORKSPACE)
            listed = (await dispatch(bots, WORKSPACE, "list", {}))["bots"][0]
            assert len(listed["runs"]) == 1 and listed["runs"][0]["outcome"] == "quiet"
            assert listed["runs"][0]["wake"]["count"] == 5
            blocks = (await dispatch(bots, WORKSPACE, "read", {"id": key}))["blocks"]
            assert len(blocks) == 1 and blocks[0]["items"][0]["text"].startswith("What's the main")

    asyncio.run(scenario())


def test_unattended_approval_expires_once_across_restart_and_human_card_survives(tmp_path):
    from tests.unit.test_agent_runtime import call, cards

    async def scenario():
        async with agent(
            tmp_path, [call("chart.delete_watchlist", {"watchlistId": "sample"}, "tool-a")]
        ) as (service, _):
            bot = service.bots.make_bot(WORKSPACE, {"name": "Review", "model": "model"})["id"]
            conversation = service.bots.line(WORKSPACE, service.bots.entry(WORKSPACE, bot), bot)
            await service.wake(
                conversation,
                WORKSPACE,
                "Remove the sample list",
                cause={"kind": "schedule"},
                request_id="wake-1",
            )
            [card] = cards(await settled(service, conversation), "approval")
            assert abs(card["expiresAt"] - card["createdAt"] - 1800) < 0.1
        async with agent(tmp_path, [AIMessage(content="Skipped.")]) as (service, platform):
            human = service.store.create(WORKSPACE, model="model")["id"]
            with service.store.transaction():
                turn = service.store.new_turn(human, WORKSPACE, "Remove", view=None, attachments=[])
            manual = service.store.request_approval(
                turn["id"], "tool-b", "host-b", "chart.delete_watchlist", {}, {}
            )
            assert manual["expiresAt"] is None
            service.bots.worker.tick(WORKSPACE, card["expiresAt"] - 0.1, True, [], {})
            assert service.store.approval(card["id"], WORKSPACE)["state"] == "waiting"
            service.bots.worker.tick(WORKSPACE, card["expiresAt"] + 30, True, [], {})
            snapshot = await settled(service, conversation)
            [expired] = cards(snapshot, "approval")
            assert (
                expired["state"] == "expired"
                and expired["version"] == 2
                and expired["decision"] == "deny"
            )
            assert not platform.calls and snapshot["turns"][0]["state"] == "completed"
            service.bots.worker.tick(WORKSPACE, card["expiresAt"] + 90, True, [], {})
            assert service.store.approval(card["id"], WORKSPACE)["version"] == 2
            assert service.store.approval(manual["id"], WORKSPACE)["state"] == "waiting"

    asyncio.run(scenario())


def test_group_message_runs_only_the_named_bot(tmp_path):
    from modules.agent.backend.bots.api import dispatch

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="Hello!")]) as (service, _):
            bots = service.bots
            alice, bob = [
                bots.make_bot(WORKSPACE, {"name": name, "model": "model"})["id"]
                for name in ["Alice", "Bob"]
            ]
            group = bots.make_group(WORKSPACE, [alice, bob])["id"]
            bots.visible[WORKSPACE] = {"panel": group}
            args = {"id": group, "text": "@Alice hello."}
            await dispatch(bots, WORKSPACE, "send", args)
            conversation = bots.line(WORKSPACE, bots.entry(WORKSPACE, alice), group)
            await settled(service, conversation)
            await bots.worker.finish(WORKSPACE)
            rows = service.store.db.execute(
                "SELECT speaker,text FROM bot_messages WHERE line=? ORDER BY seq", (group,)
            ).fetchall()
            assert [(r[0], r[1]) for r in rows] == [
                (None, f"[[ref:{alice}]] hello."),
                (alice, "Hello!"),
            ]
            runs = service.store.db.execute(
                "SELECT bot FROM bot_wakes WHERE line=?", (group,)
            ).fetchall()
            assert [r[0] for r in runs] == [alice]
            assert service.store.list(WORKSPACE)["items"] == []

    asyncio.run(scenario())


def test_workspace_copy_pauses_bots_and_routines_and_delete_keeps_bound_folder(tmp_path):
    from modules.agent.backend.bots.api import dispatch

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="Finished")]) as (service, platform):
            bots = service.bots
            key = bots.make_bot(WORKSPACE, {"name": "Morning", "model": "model"})["id"]
            bots.save_routine(
                WORKSPACE,
                {
                    "name": "At nine",
                    "when": "09:00",
                    "botId": key,
                    "instruction": "Check data",
                    "rrule": "FREQ=DAILY;BYHOUR=9",
                    "zone": "America/New_York",
                },
            )
            await service.copy_workspace(WORKSPACE, "copy")
            listed = (await dispatch(bots, "copy", "list", {}))["bots"]
            assert len(listed) == 1 and listed[0]["state"] == "paused" and listed[0]["id"] != key
            assert (
                listed[0]["routines"][0]["active"] is False
                and listed[0]["routines"][0]["botId"] == listed[0]["id"]
            )
            assert bots.entry(WORKSPACE, key)["paused"] is False
            from pathlib import Path

            folder = Path(await platform.workspace_folder("copy"))
            saved = folder / "belongs-to-user.txt"
            saved.write_text("keep", encoding="utf8")
            assert any(
                "Morning" in name for name in await service.delete_workspace("copy", preview=True)
            )
            await service.delete_workspace("copy", preview=False)
            assert saved.read_text(encoding="utf8") == "keep"
            assert not bots.entries("copy") and bots.entry(WORKSPACE, key)["name"] == "Morning"
            assert not service.usage.db.execute(
                "SELECT 1 FROM group_budgets WHERE workspace_id='copy'"
            ).fetchone()

    asyncio.run(scenario())


def test_declared_event_cursor_resumes_without_replaying_and_pause_skips(tmp_path):
    import json

    from modules.chart.backend.store import Store

    class Stream:
        def __init__(self, chart, ws, params, cursor):
            self.feed = chart.follow_firings(ws, params.get("alertId"), None, cursor)
            self.cursor = cursor

        async def __aiter__(self):
            async for event, cursor in self.feed:
                self.cursor = cursor
                yield event

        async def close(self):
            await self.feed.aclose()

    async def scenario():
        chart = Store(tmp_path / "chart.sqlite")
        async with agent(tmp_path, [AIMessage(content="NO_REPLY")]) as (service, platform):
            bots = service.bots
            bots.sources = {
                "chart.alert_fired": {"name": "alert_fired", "params": {"type": "object"}}
            }

            async def subscribe(name, params, *, workspace, cursor):
                assert name == "chart.alert_fired"
                return Stream(chart, workspace, params, cursor)

            service.ctx.subscribe = subscribe
            bot = bots.make_bot(WORKSPACE, {"name": "Alerts", "model": "model"})["id"]
            key = bots.save_routine(
                WORKSPACE,
                {
                    "botId": bot,
                    "name": "Alert",
                    "when": "test price moves",
                    "instruction": "Check event",
                    "source": "chart.alert_fired",
                    "params": {"alertId": "test"},
                },
            )["id"]
            run = asyncio.create_task(bots.worker.events(WORKSPACE, bots.routine(WORKSPACE, key)))
            await asyncio.sleep(0)

            def fire(i):
                chart.add_fired(
                    WORKSPACE,
                    {
                        "id": str(i),
                        "alertId": "test",
                        "at": i,
                        "barTime": i,
                        "instrumentId": "test-symbol",
                    },
                    "price moved",
                )

            for i in range(5):
                fire(i)
            async with asyncio.timeout(3):
                while bots.routine(WORKSPACE, key)["cursor"] != 5:
                    await asyncio.sleep(0.01)
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            bots.toggle_routine(WORKSPACE, key, False)
            fire(5)
            run = asyncio.create_task(bots.worker.events(WORKSPACE, bots.routine(WORKSPACE, key)))
            async with asyncio.timeout(3):
                while bots.routine(WORKSPACE, key)["cursor"] != 6:
                    await asyncio.sleep(0.01)
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            rows = service.store.db.execute(
                "SELECT cause,result FROM bot_wakes ORDER BY queued"
            ).fetchall()
            assert (
                len(rows) == 2
                and len(json.loads(rows[0]["cause"])["events"]) == 5
                and rows[1]["result"] == "skipped"
            )
            assert all(e["alertId"] == "test" for e in json.loads(rows[0]["cause"])["events"])
        chart.close()

    asyncio.run(scenario())


def test_bad_routine_input_is_returned_to_model_and_corrected_once(tmp_path):
    from tests.unit.test_agent_runtime import cards

    def request(value, key):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "bot_manage",
                    "args": {"operation": "save_routine", "value": value},
                    "id": key,
                }
            ],
        )

    async def scenario():
        missing = {
            "name": "Morning check",
            "zone": "America/New_York",
            "instruction": "Check data",
            "rrule": "FREQ=DAILY;BYHOUR=9",
        }
        async with agent(
            tmp_path,
            [
                request(missing, "first"),
                request({**missing, "when": "Daily at nine"}, "corrected"),
                AIMessage(content="Scheduled."),
            ],
        ) as (service, _):
            line = service.store.create(WORKSPACE, model="model")["id"]
            turn = await service.send(
                line, WORKSPACE, "Schedule my morning check"
            )
            view = await settled(service, line)
            assert view["turns"][0]["state"] == "completed" and not cards(view, "error")
            [routine] = service.bots.schedules(WORKSPACE)
            assert routine["name"] == "Morning check"
            await service.changes.revert(WORKSPACE, line, turn["id"], "undo")
            assert not service.bots.schedules(WORKSPACE)
            await service.changes.revert(WORKSPACE, line, turn["id"], "redo")
            assert [r["id"] for r in service.bots.schedules(WORKSPACE)] == [routine["id"]]

    asyncio.run(scenario())


def test_closing_workspace_invalidates_pending_cards_including_ordinary_chats(tmp_path):
    from tests.unit.test_agent_runtime import call, cards

    async def scenario():
        async with agent(
            tmp_path, [call("chart.delete_watchlist", {"watchlistId": "sample"}, "step")]
        ) as (service, platform):
            line = service.store.create(WORKSPACE, model="model")["id"]
            await service.send(line, WORKSPACE, "Remove list")
            [card] = cards(await settled(service, line), "approval")
            assert card["state"] == "waiting" and card["expiresAt"] is None

            async def closed():
                yield {"kind": "closed", "workspaceId": WORKSPACE}

            await service.bots.worker.workspaces(closed())
            assert service.store.approval(card["id"], WORKSPACE)["state"] == "interrupted"
            assert not service.store.active(line) and not platform.calls

    asyncio.run(scenario())


def test_pausing_bot_follows_handoffs_through_completed_parents(tmp_path):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="OK")]) as (service, _):
            bots = service.bots
            a, b = [
                bots.make_bot(WORKSPACE, {"name": v, "model": "model"})["id"] for v in ["A", "B"]
            ]
            group = bots.make_group(WORKSPACE, [a, b])["id"]
            with service.store.transaction():
                first = bots.worker.queue(
                    WORKSPACE,
                    a,
                    group,
                    bots.line(WORKSPACE, bots.entry(WORKSPACE, a), group),
                    {"kind": "you"},
                    "Start",
                    1,
                )
                second = bots.worker.queue(
                    WORKSPACE,
                    b,
                    group,
                    bots.line(WORKSPACE, bots.entry(WORKSPACE, b), group),
                    {"kind": "mention", "parent": first},
                    "First handoff",
                    2,
                )
                third = bots.worker.queue(
                    WORKSPACE,
                    b,
                    group,
                    bots.line(WORKSPACE, bots.entry(WORKSPACE, b), group),
                    {"kind": "mention", "parent": second},
                    "Second handoff",
                    3,
                )
                bots.db.execute(
                    "UPDATE bot_wakes SET result='done' WHERE id IN (?,?)", (first, second)
                )
                independent = bots.worker.queue(
                    WORKSPACE,
                    b,
                    b,
                    bots.line(WORKSPACE, bots.entry(WORKSPACE, b), b),
                    {"kind": "schedule"},
                    "Independent",
                    4,
                )
            await bots.patch(WORKSPACE, a, {"paused": True})
            assert (
                bots.db.execute("SELECT result FROM bot_wakes WHERE id=?", (third,)).fetchone()[0]
                == "stopped"
            )
            assert (
                bots.db.execute(
                    "SELECT result FROM bot_wakes WHERE id=?", (independent,)
                ).fetchone()[0]
                is None
            )

    asyncio.run(scenario())


def test_duty_can_post_to_a_joined_group_once_and_original_line_stays_quiet(tmp_path):
    from modules.agent.backend.bots.api import dispatch

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="NO_REPLY")]) as (service, _):
            bots = service.bots
            a, b = [
                bots.make_bot(WORKSPACE, {"name": name, "model": "model"})["id"]
                for name in ("Desk", "Calendar")
            ]
            group = bots.make_group(WORKSPACE, [a, b])["id"]
            bots.visible[WORKSPACE] = {"panel": group}
            model = service.model_runtime.chat_model
            model.responses = [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "bot_manage",
                            "id": "post",
                            "args": {
                                "operation": "post_to_group",
                                "value": {"groupId": group, "text": "Calendar checked."},
                            },
                        }
                    ],
                ),
                AIMessage(content="NO_REPLY"),
            ]
            with service.store.transaction():
                line = bots.line(WORKSPACE, bots.entry(WORKSPACE, a), a)
                bots.worker.queue(
                    WORKSPACE,
                    a,
                    a,
                    line,
                    {"kind": "schedule", "routine": "Report"},
                    "Post in the group",
                    1,
                )
            await bots.worker.dispatch(WORKSPACE)
            await settled(service, line)
            await bots.worker.finish(WORKSPACE)
            group_view = await dispatch(bots, WORKSPACE, "read", {"id": group})
            assert len(group_view["blocks"]) == 1 and group_view["blocks"][0]["bot"] == a
            assert group_view["blocks"][0]["items"][0]["text"] == "Calendar checked."
            assert (await dispatch(bots, WORKSPACE, "list", {}))["bots"][1]["runs"][0][
                "outcome"
            ] == "quiet"
            assert len((await dispatch(bots, WORKSPACE, "read", {"id": a}))["blocks"]) == 1

    asyncio.run(scenario())


def test_a_quiet_scheduled_run_remembers_for_the_bot_alone_and_undoing_the_run_forgets_it(tmp_path):
    from modules.agent.backend.bots.api import dispatch
    from tests.unit.test_agent_runtime import native

    async def runs(bots, key):
        return {bot["id"]: bot for bot in (await dispatch(bots, WORKSPACE, "list", {}))["bots"]}[key]["runs"]

    async def scenario():
        responses = [native("remember", {"body": "Alerts fire at the 4h close"}, "note"), AIMessage(content="NO_REPLY")]
        async with agent(tmp_path, responses) as (service, _):
            bots = service.bots
            key = bots.make_bot(WORKSPACE, {"name": "Review", "model": "model"})["id"]
            other = bots.make_bot(WORKSPACE, {"name": "Other", "model": "model"})["id"]
            line = bots.line(WORKSPACE, bots.entry(WORKSPACE, key), key)
            with service.store.transaction():
                bots.worker.queue(WORKSPACE, key, key, line, {"kind": "schedule", "routine": "Report"}, "Review prices", 1)
            await bots.worker.dispatch(WORKSPACE)
            await settled(service, line)
            await bots.worker.finish(WORKSPACE)
            [run] = await runs(bots, key)
            assert run["outcome"] == "quiet"
            assert [(e["kind"], e["label"], e["undone"]) for e in run["effects"]] == [("memory", "Alerts fire at the 4h close", False)]
            [note] = service.memory.list(WORKSPACE, bot=key)["items"]
            assert note["scope"] == "bot" and service.memory.list(WORKSPACE, bot=other)["items"] == []
            assert service.memory.list(WORKSPACE)["items"] == []
            await dispatch(bots, WORKSPACE, "revert_run", {"id": run["id"], "direction": "undo"})
            assert service.memory.list(WORKSPACE, bot=key, status="active")["items"] == []
            [run] = await runs(bots, key)
            assert run["effects"][0]["undone"]

    asyncio.run(scenario())


def test_a_bot_with_a_duty_does_not_ask_for_one_and_never_means_no_failure_pause(tmp_path):
    async def scenario():
        async with agent(tmp_path, [AIMessage(content="unused")]) as (service, _):
            bots = service.bots
            asked = bots.make_bot(WORKSPACE, {"name": "New", "model": "model"})["id"]
            preset = bots.make_bot(WORKSPACE, {"name": "Brief", "model": "model", "duty": "Write the brief"})["id"]
            texts = [row[0] for row in service.store.db.execute("SELECT text FROM bot_messages")]
            assert len(texts) == 1 and bots.entry(WORKSPACE, preset)["last"]["text"] == "" and asked != preset
            service.configure_defaults({"botFailureLimit": 0}, None, kind="bot")
            definition = bots.entry(WORKSPACE, asked)
            for _ in range(5):
                bots.worker.failure(WORKSPACE, definition, "Run failed")
            assert definition["failures"] == 5 and not definition["paused"]
    asyncio.run(scenario())


def test_bots_and_groups_the_chat_creates_are_on_its_change_card_and_deleting_them_does_not_ask(tmp_path):
    from tests.unit.test_agent_runtime import cards, native, start

    async def scenario():
        make = {"operation": "create_bot", "value": {"name": "Desk", "intro": "Watches BTC", "duty": "Watch BTC",
                                                     "model": "model"}}
        responses = [native("bot_manage", make, "make"), AIMessage(content="Created.")]
        async with agent(tmp_path, responses) as (service, _):
            bots = service.bots
            line, view = await start(service, "Make a desk bot")
            [made] = [entry["id"] for entry in (await dispatch(bots, WORKSPACE, "list", {}))["bots"]]
            [card] = cards(view, "changes")
            assert [(item["kind"], item["label"]) for item in card["items"]] == [("created", "Desk")]
            mine = bots.make_bot(WORKSPACE, {"name": "Mine", "model": "model"})["id"]
            model = service.model_runtime.chat_model
            model.responses = [native("bot_manage", {"operation": "create_group", "value": {"members": [made, mine]}},
                                      "group"), AIMessage(content="Grouped.")]
            model.i = 0
            await service.send(line, WORKSPACE, "Put them in a group", include_view=False)
            view = await settled(service, line)
            assert [(item["kind"], item["label"]) for item in cards(view, "changes")[-1]["items"]] == [("created", "Desk、Mine")]
            # Deleting the bot this chat made does not ask in the automatic mode; deleting the user's does.
            model.responses = [native("bot_delete", {"id": made, "kind": "bot"}, "drop"), AIMessage(content="Deleted."),
                               native("bot_delete", {"id": mine, "kind": "bot"}, "drop-mine")]
            model.i = 0
            await service.send(line, WORKSPACE, "Delete the desk bot", include_view=False)
            view = await settled(service, line)
            assert view["turns"][-1]["state"] == "completed" and not cards(view, "approval")
            assert [entry["id"] for entry in (await dispatch(bots, WORKSPACE, "list", {}))["bots"]] == [mine]
            await service.send(line, WORKSPACE, "Delete mine too", include_view=False)
            [approval] = cards(await settled(service, line), "approval")
            assert approval["state"] == "waiting"

    from modules.agent.backend.bots.api import dispatch
    asyncio.run(scenario())


def test_a_spent_bot_budget_is_not_a_failure_and_its_notice_comes_once(tmp_path):
    from talyx_sdk import ContractError

    from tests.unit.test_agent_runtime import ScriptedModel

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="unused")]) as (service, _):
            bots = service.bots
            key = bots.make_bot(WORKSPACE, {"name": "Brief", "model": "model", "duty": "Write the brief"})["id"]
            service.configure_defaults({"botFailureLimit": 1}, None, kind="bot")
            notices = []

            async def notify(ws, bot, line, reason, key, body, *, run):
                notices.append((reason, body))
            bots.notify = notify

            class Spent(ScriptedModel):
                async def _agenerate(self, messages, **kwargs):
                    raise ContractError("budget_exceeded", "Brief reached its monthly limit", {"groupId": key})
            service.model_runtime.chat_model = Spent(responses=[AIMessage(content="unused")])
            line = bots.line(WORKSPACE, bots.entry(WORKSPACE, key), key)
            with service.store.transaction():
                bots.worker.queue(WORKSPACE, key, key, line, {"kind": "schedule", "routine": "Brief"}, "Write it", 1)
            await bots.worker.dispatch(WORKSPACE)
            await settled(service, line)
            await bots.worker.finish(WORKSPACE)
            definition = bots.entry(WORKSPACE, key)
            assert definition["failures"] == 0 and not definition["paused"]
            assert notices == [("needsYou", "Brief reached its monthly limit")]
    asyncio.run(scenario())


def test_a_paused_bot_answers_the_users_own_message_and_its_schedule_still_skips(tmp_path):
    from modules.agent.backend.bots.api import dispatch

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="ETH funding is 0.01%.")]) as (service, _):
            bots = service.bots
            key = bots.make_bot(WORKSPACE, {"name": "Funding", "model": "model", "duty": "Watch funding"})["id"]
            routine = bots.save_routine(WORKSPACE, {"name": "Morning", "when": "08:00", "botId": key, "instruction": "Report",
                                                    "rrule": "FREQ=DAILY;BYHOUR=8;BYMINUTE=0;BYSECOND=0", "zone": "UTC"})["id"]
            await bots.patch(WORKSPACE, key, {"paused": True})
            await dispatch(bots, WORKSPACE, "send", {"id": key, "text": "What is ETH funding now?"})
            line = bots.line(WORKSPACE, bots.entry(WORKSPACE, key), key)
            await settled(service, line)
            await bots.worker.finish(WORKSPACE)
            texts = [row[0] for row in service.store.db.execute("SELECT text FROM bot_messages WHERE line=? ORDER BY seq", (key,))]
            assert texts == ["What is ETH funding now?", "ETH funding is 0.01%."]
            assert bots.entry(WORKSPACE, key)["paused"] is True
            # The routine comes due while the bot is paused: it is skipped, not run.
            due = bots.routine(WORKSPACE, routine)["nextDue"]
            bots.worker.tick(WORKSPACE, due + 1, False, [bots.routine(WORKSPACE, routine)], {key: bots.entry(WORKSPACE, key)})
            [wake] = service.store.db.execute("SELECT result FROM bot_wakes WHERE json_extract(cause,'$.routineId')=?", (routine,)).fetchall()
            assert wake[0] == "skipped"
    asyncio.run(scenario())

def test_an_internal_failure_in_a_bot_request_is_a_500_raised_to_the_server_not_the_users_input(tmp_path, monkeypatch):
    import httpx
    import pytest

    from modules.agent.backend import entry

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="unused")]) as (service, _):
            key = service.bots.make_bot(WORKSPACE, {"name": "Desk", "model": "model"})["id"]

            async def broken(ws, args):
                raise KeyError("lineId")
            monkeypatch.setattr(service.bots, "send", broken)
            monkeypatch.setattr(entry, "service", service)
            app = entry.routes(service.ctx)
            request = {"operation": "send", "value": {"id": key, "text": "Hi"}}
            headers = {"x-talyx-workspace": WORKSPACE}
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
                assert (await client.post("/bots", json=request, headers=headers)).status_code == 500
            # The exception reaches the server, which logs it with its stack (uvicorn's "Exception in ASGI application").
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
                with pytest.raises(KeyError, match="lineId"):
                    await client.post("/bots", json=request, headers=headers)
            # The user's own wrong input is still a 400.
            async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
                wrong = {"operation": "create", "value": {"name": ""}}
                assert (await client.post("/bots", json=wrong, headers=headers)).status_code == 400
    asyncio.run(scenario())


def test_a_notification_that_did_not_go_out_is_written_on_the_run(tmp_path):
    from talyx_sdk import ContractError

    from modules.agent.backend.bots.api import dispatch

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="BTC is up 2%.")]) as (service, platform):
            bots = service.bots
            key = bots.make_bot(WORKSPACE, {"name": "Brief", "model": "model", "duty": "Write the brief"})["id"]
            bots.entry(WORKSPACE, key)
            await bots.patch(WORKSPACE, key, {"notify": {"needsYou": True, "results": True, "phone": "gone-channel"}})
            calls = service.ctx.call

            async def deliver(name, value, **kwargs):
                if name == "platform.notifications.send":
                    raise ContractError("not_found", "The channel gone-channel no longer exists")
                return await calls(name, value, **kwargs)
            service.ctx.call = deliver
            line = bots.line(WORKSPACE, bots.entry(WORKSPACE, key), key)
            with service.store.transaction():
                bots.worker.queue(WORKSPACE, key, key, line, {"kind": "schedule", "routine": "Brief"}, "Write it", 1)
            await bots.worker.dispatch(WORKSPACE)
            await settled(service, line)
            await bots.worker.finish(WORKSPACE)
            [run] = (await dispatch(bots, WORKSPACE, "list", {}))["bots"][0]["runs"]
            assert run["outcome"] == "done"
            assert run["notice"] == {"action": "platform.notifications.send", "code": "not_found",
                                     "message": "The channel gone-channel no longer exists"}
    asyncio.run(scenario())

def test_the_chat_runs_a_bot_routine_once_in_the_bots_runs_and_reads_bots_in_a_short_list(tmp_path):
    from modules.agent.backend.bots.api import dispatch
    from tests.unit.test_agent_runtime import native, start

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="unused")]) as (service, _):
            bots = service.bots
            # A declared event source carries its module's whole contract in $defs; the list must not.
            bots.sources = {"chart.alert_fired": {"name": "alert_fired", "description": "An alert fired",
                                                  "params": {"type": "object", "$defs": {f"T{i}": {"description": "x" * 400} for i in range(200)}}}}
            made = {}
            for n in range(8):
                key = bots.make_bot(WORKSPACE, {"name": f"Desk {n}", "model": "model", "duty": "Brief " + "d" * 500})["id"]
                for hour in (8, 20):
                    made[f"Desk {n} at {hour}"] = bots.save_routine(WORKSPACE, {
                        "name": f"Desk {n} at {hour}", "when": f"daily {hour}:00", "botId": key, "instruction": "Report " + "i" * 800,
                        "rrule": f"FREQ=DAILY;BYHOUR={hour};BYMINUTE=0;BYSECOND=0", "zone": "Asia/Shanghai"})["id"]
            target = made["Desk 3 at 8"]
            model = service.model_runtime.chat_model
            model.responses = [native("bot_read", {}, "read"),
                               native("bot_manage", {"operation": "run_routine", "value": {"id": target}}, "try"),
                               AIMessage(content="Started the test run."), AIMessage(content="BTC brief: up 2%.")]
            model.i = 0
            await start(service, "Run the 8:00 brief of Desk 3 once now")
            [listing, started] = [m for m in model.requests[-1] if m.type == "tool"]
            assert len(listing.content) < 20000
            assert all(name in listing.content for name in made) and "daily 8:00" in listing.content
            assert "Started" in started.content
            bot = bots.routine(WORKSPACE, target)["botId"]
            await bots.worker.dispatch(WORKSPACE)
            await settled(service, bots.line(WORKSPACE, bots.entry(WORKSPACE, bot), bot))
            await bots.worker.finish(WORKSPACE)
            [run] = {b["id"]: b for b in (await dispatch(bots, WORKSPACE, "list", {}))["bots"]}[bot]["runs"]
            assert run["wake"]["routineId"] == target and run["outcome"] == "done"
    asyncio.run(scenario())

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000")


def test_a_group_image_goes_to_the_bots_that_read_images_and_says_which_did_not(tmp_path):
    """Alice's model reads images, Bob's does not. The message is posted once, Alice gets it, and the answer names Bob
    as skipped (nothing to send again). Sent to Bob alone, it is refused and nothing is posted."""
    from modules.agent.backend.bots.api import dispatch

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="Seen.")]) as (service, _):
            service.catalogue.put_model({"id": "vision", "connectionId": "local", "model": "vision-model",
                                         "contextWindow": 32000, "tools": True, "images": True})
            bots = service.bots
            alice = bots.make_bot(WORKSPACE, {"name": "Alice", "model": "vision"})["id"]
            bob = bots.make_bot(WORKSPACE, {"name": "Bob", "model": "model"})["id"]
            group = bots.make_group(WORKSPACE, [alice, bob])["id"]
            lines = {key: bots.line(WORKSPACE, bots.entry(WORKSPACE, key), group) for key in (alice, bob)}
            images = {key: [service.attach(line, WORKSPACE, "chart.png", "image/png", PNG)["id"]]
                      for key, line in lines.items()}
            answer = await dispatch(bots, WORKSPACE, "send", {"id": group, "text": "What do you see?", "attachments": images})
            assert [(item["botId"], item["name"]) for item in answer["skipped"]] == [(bob, "Bob")]
            assert "image" in answer["skipped"][0]["reason"]
            await settled(service, lines[alice])
            posted = service.store.db.execute("SELECT speaker FROM bot_messages WHERE line=? AND speaker IS NULL",
                                              (group,)).fetchall()
            assert len(posted) == 1
            woken = service.store.db.execute("SELECT bot FROM bot_wakes WHERE line=?", (group,)).fetchall()
            assert [row[0] for row in woken] == [alice]
            images = {bob: [service.attach(lines[bob], WORKSPACE, "chart.png", "image/png", PNG)["id"]]}
            try:
                await dispatch(bots, WORKSPACE, "send", {"id": group, "text": "@Bob and you?", "attachments": images})
            except ContractError as error:
                assert error.code == "unsupported"
            else:
                raise AssertionError("a message no recipient can read is refused")
            assert service.store.db.execute("SELECT COUNT(*) FROM bot_messages WHERE line=? AND speaker IS NULL",
                                            (group,)).fetchone()[0] == 1

    asyncio.run(scenario())


def test_deleting_a_routine_takes_its_queued_wakes_with_it(tmp_path):
    """A routine's wake waits in the queue while its bot is busy; deleting the routine stops it, so it never runs."""
    from modules.agent.backend.bots.api import dispatch

    async def scenario():
        async with agent(tmp_path, [AIMessage(content="Done.")]) as (service, _):
            bots = service.bots
            key = bots.make_bot(WORKSPACE, {"name": "Review", "model": "model"})["id"]
            conversation = bots.line(WORKSPACE, bots.entry(WORKSPACE, key), key)
            routine = bots.save_routine(WORKSPACE, {"botId": key, "name": "Morning", "instruction": "Review",
                                                    "when": "09:00", "zone": "UTC", "rrule": "FREQ=DAILY"})
            with service.store.transaction():
                queued = bots.worker.queue(WORKSPACE, key, key, conversation,
                                           {"kind": "schedule", "routineId": routine["id"], "routine": "Morning"},
                                           "Review", 100)
                other = bots.worker.queue(WORKSPACE, key, key, conversation,
                                          {"kind": "schedule", "routineId": "another", "routine": "Evening"},
                                          "Review", 101)
            await dispatch(bots, WORKSPACE, "delete_routine", {"id": routine["id"]})
            result = dict(service.store.db.execute("SELECT id,result FROM bot_wakes").fetchall())
            assert result == {queued: "stopped", other: None}
            await bots.worker.dispatch(WORKSPACE)
            await settled(service, conversation)
            assert service.store.db.execute("SELECT turn_id FROM bot_wakes WHERE id=?", (queued,)).fetchone()[0] is None
            assert service.store.db.execute("SELECT turn_id FROM bot_wakes WHERE id=?", (other,)).fetchone()[0]

    asyncio.run(scenario())
