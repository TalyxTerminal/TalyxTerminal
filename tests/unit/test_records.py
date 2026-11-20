"""Checkup item 11 for the platform's records: ended tasks and runs are forgotten after a while (nothing only
grows), and the call log is written and read off the event loop, swept as each day begins."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime

import pytest
from talyx_sdk.errors import ContractError

from host import records
from host.records import CallLog, Runs, Tasks

MODULE = {"origin": "module", "module": "quant", "workspaceId": "W"}


def test_ended_tasks_and_runs_are_forgotten_after_a_while(monkeypatch):
    tasks = Tasks(lambda module, task: None)
    ended = tasks.start(MODULE, "quant.backtest")["taskId"]
    tasks.end("quant", ended, {"ok": 1}, None)
    running = tasks.start(MODULE, "quant.backtest")["taskId"]
    assert tasks.get(ended)["state"] == "completed"
    monkeypatch.setattr(records, "ENDED_SECONDS", -1)
    tasks.start(MODULE, "quant.backtest")
    assert ended not in tasks.tasks and running in tasks.tasks


def test_runs_ended_long_ago_are_forgotten(tmp_path, monkeypatch):
    runs = Runs(lambda: "http://x", CallLog(tmp_path))
    first = runs.start(MODULE, "W", None)["runId"]
    runs.end(MODULE, first)
    assert first in runs.runs
    monkeypatch.setattr(records, "ENDED_SECONDS", -1)
    runs.start(MODULE, "W", None)
    assert first not in runs.runs


def test_the_call_log_is_written_off_the_loop_and_read_by_run_and_call(tmp_path):
    async def scenario():
        log = CallLog(tmp_path)
        runs = Runs(lambda: "http://x", log)
        run = runs.start(MODULE, "W", None)
        caller = runs.caller(run["token"])
        for index in range(3):
            log.begin(caller, "market.read_quotes", b"{}", f"c{index}")
        for index in reversed(range(3)):
            log.end(f"c{index}", None)
        rows = await runs.calls(MODULE, run["runId"])
        assert [row["callId"] for row in rows] == ["c0", "c1", "c2"]
        assert [row["callId"] for row in await log.lookup(["c2", "c0"])] == ["c2", "c0"]
        return log

    log = asyncio.run(scenario())
    assert log.swept == datetime.now(UTC).date().isoformat()


def test_a_failed_write_is_logged_and_the_next_calls_are_recorded_and_found(tmp_path, monkeypatch, caplog):
    async def scenario():
        log = CallLog(tmp_path)
        append, failures = log._append, ["database is locked"]

        def once(entries):
            if failures:
                raise sqlite3.OperationalError(failures.pop())
            append(entries)
        monkeypatch.setattr(log, "_append", once)
        caller = {"origin": "ai", "module": "agent", "workspaceId": "W", "ai": {"conversation": "c"}}
        log.begin(caller, "chart.add_to_watchlist", b"{}", "lost")
        log.end("lost", None)
        with pytest.raises(sqlite3.OperationalError):
            await log.written()
        log.begin(caller, "chart.add_to_watchlist", b"{}", "kept")
        log.end("kept", None)
        # Undo starts by reading the call's record; it reads like any other.
        assert [row["callId"] for row in await log.lookup(["kept"])] == ["kept"]
        with pytest.raises(ContractError):
            await log.lookup(["lost"])

    with caplog.at_level("ERROR", logger="talyx.host"):
        asyncio.run(scenario())
    assert any(record.exc_info and "lost" in record.getMessage() for record in caplog.records)