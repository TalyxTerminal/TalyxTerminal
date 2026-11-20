"""Market starting (after its own or the Host's restart) continues the downloads of workspaces already open, as when
one opens; it listens for openings before it lists the open ones, so none falls in between."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from talyx_sdk import ContractError

from modules.market.backend import entry
from modules.market.backend.app import Market


class Jobs:
    def __init__(self, steps):
        self.steps, self.open_workspaces = steps, None

    def workspace_opened(self, workspace_id):
        self.steps.append(("resume", workspace_id))


class Context:
    def __init__(self, steps):
        self.steps = steps
        self.platform = self

    async def subscribe(self, name):
        self.steps.append(("subscribe", name))

        async def changes():
            yield {"kind": "opened", "workspaceId": "later"}
            yield {"kind": "closed", "workspaceId": "desk"}
        return changes()

    async def open_workspaces(self):
        self.steps.append(("list",))
        return ["desk"]


def test_open_workspaces_resume_at_start_like_one_that_opens():
    async def scenario():
        steps = []
        jobs = Jobs(steps)
        changes = await entry.open_workspaces(Context(steps), jobs)
        assert steps == [("subscribe", "platform.workspace.changes"), ("list",), ("resume", "desk")]
        assert jobs.open_workspaces == {"desk"}
        await entry.follow_workspaces(jobs, changes)
        assert steps[-1] == ("resume", "later") and jobs.open_workspaces == {"later"}

    asyncio.run(scenario())


class WorkspaceChanges:
    def __init__(self, error=None):
        self.error = error
        self.listening = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.close = AsyncMock()

    async def __aiter__(self):
        self.listening.set()
        if self.error is not None:
            raise self.error
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
            raise
        yield  # this stream stays open without emitting a workspace change


async def start_market(tmp_path, changes, monkeypatch):
    from talyx_sdk import rest_profiles

    from modules.market.backend import app

    ctx = SimpleNamespace(platform=SimpleNamespace(storage=AsyncMock(return_value={"dataDir": str(tmp_path)}),
                                                   open_workspaces=AsyncMock(return_value=[])),
                          watch_extensions=Mock(), subscribe=AsyncMock(return_value=changes),
                          extensions=AsyncMock(return_value=[]))
    market = Market(ctx, tmp_path, sources=[])
    monkeypatch.setattr(app, "Market", lambda ctx, path: market)
    monkeypatch.setattr(entry, "_prepare", lambda: None)
    monkeypatch.setattr(entry, "market", None)
    monkeypatch.setattr(rest_profiles, "use", lambda profile: None)
    await entry.start(ctx)
    return market


def test_market_close_cancels_and_joins_the_workspace_subscription(tmp_path, monkeypatch):
    async def scenario():
        changes = WorkspaceChanges()
        market = await start_market(tmp_path, changes, monkeypatch)
        await changes.listening.wait()
        closing = asyncio.create_task(market.close())
        try:
            await asyncio.wait_for(changes.cancelled.wait(), 2)
            assert not closing.done()
        finally:
            changes.release.set()
            await closing
        changes.close.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize(("code", "message", "reported"), [
    ("unavailable", "TALYX is closing", False),
    ("unavailable", "workspace service disconnected", True),
    ("permission_denied", "TALYX is closing", True),
])
def test_workspace_stream_end_reports_only_unexpected_failures(tmp_path, monkeypatch, caplog, code, message, reported):
    async def scenario():
        error = ContractError(code, message)
        changes = WorkspaceChanges(error)
        market = await start_market(tmp_path, changes, monkeypatch)
        await changes.listening.wait()
        await asyncio.sleep(0)  # run the task completion callback
        records = [r for r in caplog.records if r.exc_info and r.exc_info[1] is error]
        assert bool(records) is reported
        await market.close()
        changes.close.assert_awaited_once()
    asyncio.run(scenario())
