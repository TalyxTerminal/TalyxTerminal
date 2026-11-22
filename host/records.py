"""What the platform keeps about work: long tasks, the call log, run tokens."""

from __future__ import annotations

import asyncio
import itertools
import logging
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
from talyx_sdk.errors import ContractError
from talyx_sdk.i18n import t

log = logging.getLogger("talyx.host")
# Recorded calls are kept this many days.
CALL_DAYS = 30
# An ended task or run is kept this long for whoever still asks about it, then forgotten.
ENDED_SECONDS = 3600


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Tasks:
    """Long tasks: a task action answers with a task id at once; the module's SDK reports progress and the end."""

    def __init__(self, cancel: Callable[[str, str], None]):
        self.cancel_in_module = cancel
        self.tasks: dict[str, dict] = {}
        self.ended: dict[str, float] = {}       # task id -> when it ended, oldest first

    def start(self, caller: dict, action: str) -> dict:
        if caller["origin"] != "module" or not action.startswith(caller["module"] + "."):
            raise ContractError("permission_denied", t("Only a module's SDK records its own tasks"))
        self._forget()
        task_id = "t_" + secrets.token_hex(8)
        self.tasks[task_id] = {"taskId": task_id, "action": action, "module": caller["module"],
                               "workspaceId": caller.get("workspaceId"), "state": "running", "progress": None,
                               "result": None, "error": None}
        return {"taskId": task_id}

    def get(self, task_id: str) -> dict:
        try:
            return self.tasks[task_id]
        except KeyError:
            raise ContractError("not_found", t("No task {task_id}", task_id=task_id)) from None

    def progress(self, module: str, task_id: str, value) -> None:
        task = self.tasks.get(task_id)
        if task is not None and task["module"] == module:
            task["progress"] = value

    def end(self, module: str, task_id: str, result, error: dict | None) -> None:
        task = self.tasks.get(task_id)
        if task is None or task["module"] != module:
            return
        if error is None:
            task.update(state="completed", result=result)
        else:
            task.update(state="cancelled" if error["code"] == "cancelled" else "failed", error=error)
        self.ended[task_id] = time.monotonic()

    def _forget(self) -> None:
        cutoff = time.monotonic() - ENDED_SECONDS
        for task_id, ended in list(self.ended.items()):
            if ended > cutoff:
                break
            del self.ended[task_id]
            self.tasks.pop(task_id, None)

    async def cancel(self, task_id: str) -> dict:
        task = self.get(task_id)
        if task["state"] == "running":
            self.cancel_in_module(task["module"], task_id)
        return task

    def module_gone(self, module: str) -> None:
        for task in self.tasks.values():
            if task["module"] == module and task["state"] == "running":
                task.update(state="failed", error=ContractError(
                    "provider_restarted", t("{module} restarted while the task ran; whether it finished is unknown", module=module),
                    execution="unknown", effects="may_have_occurred").to_dict())
                self.ended[task["taskId"]] = time.monotonic()


class CallLog:
    """The call record indexed by Host call ID and code run ID. Disk I/O stays off the event loop."""

    def __init__(self, folder: Path, days: int = CALL_DAYS):
        self.folder, self.days = folder, days
        self.path = folder / "calls.sqlite3"
        self.dispatch = itertools.count()
        self.open: dict[str, dict] = {}
        self.recent_calls: list[dict] = []
        self.unwritten: list[dict] = []
        self.writing: asyncio.Task | None = None
        self.swept: str | None = None
        folder.mkdir(parents=True, exist_ok=True)

    def begin(self, caller: dict, capability: str, body: bytes, call_id: str) -> None:
        summary = body[:300].decode("utf-8", "replace")
        self.open[call_id] = {"_dispatch": next(self.dispatch), "callId": call_id, "capability": capability, "caller": caller, "startedAt": _now(),
                              "endedAt": None, "ok": None, "error": None, "summary": summary}

    def end(self, call_id: str, error: dict | None) -> None:
        record = self.open.pop(call_id, None)
        if record is None:
            return
        record.update(endedAt=_now(), ok=error is None, error=error["code"] if error else None)
        dispatch = record.pop("_dispatch")
        self.recent_calls.append(record)
        del self.recent_calls[:-2000]
        self.unwritten.append({**record, "_dispatch": dispatch})
        if self.writing is None:
            self.writing = asyncio.get_running_loop().create_task(self._write())

    async def _write(self) -> None:
        try:
            while self.unwritten:
                records, self.unwritten = self.unwritten, []
                await asyncio.to_thread(self._append, records)
        except Exception:
            log.exception("Writing %d call records failed; they are lost", len(records))
            raise
        finally:
            self.writing = None

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, run TEXT, day TEXT NOT NULL, "
                   "record BLOB NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS by_run ON calls(run) WHERE run IS NOT NULL")
        db.execute("CREATE INDEX IF NOT EXISTS by_day ON calls(day)")
        return db

    def _append(self, records: list[dict]) -> None:
        entries = [(r["callId"], (r["caller"].get("run") or {}).get("id"), r["startedAt"][:10], orjson.dumps(r))
                   for r in records]
        with closing(self._connect()) as db, db:
            db.executemany("INSERT INTO calls VALUES (?, ?, ?, ?)", entries)
        today = datetime.now(UTC).date().isoformat()
        if self.swept != today:
            self.sweep()

    async def written(self) -> None:
        """Every ended call is in the database."""
        while self.writing is not None:
            await asyncio.shield(self.writing)

    def recent(self, workspace_id: str | None, limit: int) -> list[dict]:
        rows = [r for r in self.recent_calls if r["caller"]["origin"] in ("ai", "code") and
                (workspace_id is None or r["caller"].get("workspaceId") == workspace_id)]
        return rows[-limit:]

    async def lookup(self, call_ids: list[str], run_id: str | None = None) -> list[dict]:
        """Read the durable records in the requested order, or in dispatch order for a code run."""
        await self.written()

        def public(row):
            record = orjson.loads(row[0])
            record.pop("_dispatch", None)
            return record

        def read() -> list[dict]:
            with closing(self._connect()) as db:
                if run_id is not None:
                    rows = db.execute("SELECT record FROM calls WHERE run = ? "
                                      "ORDER BY json_extract(CAST(record AS TEXT), '$._dispatch'), rowid", (run_id,))
                    return [public(row) for row in rows]
                records = []
                for call_id in call_ids:
                    row = db.execute("SELECT record FROM calls WHERE id = ?", (call_id,)).fetchone()
                    if row is None:
                        raise ContractError("not_found", t("No recorded call {call_id}", call_id=call_id))
                    records.append(public(row))
                return records
        return await asyncio.to_thread(read)

    def sweep(self, today: datetime | None = None) -> None:
        today = today or datetime.now(UTC)
        cutoff = (today - timedelta(days=self.days)).date().isoformat()
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM calls WHERE day < ?", (cutoff,))
        self.swept = today.date().isoformat()


class Runs:
    """Tokens for code a module starts: calls with the token are ``code`` from that module and workspace."""

    def __init__(self, url: Callable[[], str], calls: CallLog):
        self.url, self.log = url, calls
        self.runs: dict[str, dict] = {}
        self.by_token: dict[str, str] = {}

    def start(self, caller: dict, workspace_id: str | None, context, conversation: str | None = None) -> dict:
        if caller["origin"] != "module":
            raise ContractError("permission_denied", t("Runs are started by modules"))
        cutoff = time.time() - ENDED_SECONDS
        for run_id, run in list(self.runs.items()):
            if run["ended"] and run["ended"] < cutoff:
                del self.runs[run_id]
        run_id = "r_" + secrets.token_hex(8)
        token = secrets.token_urlsafe(32)
        self.runs[run_id] = {"id": run_id, "module": caller["module"], "workspaceId": workspace_id,
                             "context": context, "conversation": conversation, "token": token, "started": time.time(),
                             "ended": False}
        self.by_token[token] = run_id
        return {"runId": run_id, "token": token, "url": self.url()}

    def caller(self, token: str) -> dict:
        run_id = self.by_token.get(token)
        if run_id is None:
            raise ContractError("permission_denied", t("Unknown or ended run token"))
        run = self.runs[run_id]
        return {"origin": "code", "module": run["module"], "workspaceId": run["workspaceId"],
                "run": {"id": run_id, "context": run["context"],
                        **({"conversation": run["conversation"]} if run["conversation"] else {})}}

    def _own(self, caller: dict, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None or caller.get("module") != run["module"]:
            raise ContractError("not_found", t("No run {run_id} of this module", run_id=run_id))
        return run

    async def calls(self, caller: dict, run_id: str) -> list[dict]:
        self._own(caller, run_id)
        return await self.log.lookup([], run_id)

    def end(self, caller: dict, run_id: str) -> None:
        """The token stops working; the run is kept ``ENDED_SECONDS`` for whoever still reads its calls."""
        run = self._own(caller, run_id)
        self.by_token.pop(run["token"], None)
        run["ended"] = run["ended"] or time.time()
