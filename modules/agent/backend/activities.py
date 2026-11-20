"""Follow workspace activities started by recorded calls, including runs restored by their module."""

import asyncio
import json

from .store import encode


class Activities:
    def __init__(self, service):
        self.service, self.db = service, service.store.db
        self.registering = asyncio.Lock()
        self.db.execute("""CREATE TABLE IF NOT EXISTS turn_activities
            (module TEXT NOT NULL, activity TEXT NOT NULL, turn_id TEXT NOT NULL, call_id TEXT NOT NULL,
             value TEXT NOT NULL, PRIMARY KEY(module,activity))""")

    async def start(self):
        events = await self.service.ctx.subscribe("platform.activity.ended")
        changes = await self.service.ctx.subscribe("platform.activity.changes")
        modules = await self.service.ctx.subscribe("platform.modules.changes")
        self.watcher = asyncio.gather(self.follow(events), self.progress(changes), self.restarts(modules))
        async with self.registering:
            await self.reconcile(refresh=True)

    async def restarts(self, subscription):
        """A module process that ends loses its activities without an end event: once it is back (having resumed what
        it resumes) or has stopped, the calls still waiting on it are reconciled as after Agent's own restart."""
        async for _ in subscription:
            async with self.registering:
                await self.reconcile(refresh=False)

    async def reconcile(self, *, refresh):
        """Follow a tracked activity that its module resumed under a new id; one gone from a module that is not
        starting ended with an unknown result. ``refresh`` shows the running ones again after Agent's restart."""
        states = {item["id"]: item["state"] for item in await self.service.ctx.call("platform.modules.list", {})}
        rows = [row for row in self.db.execute("SELECT * FROM turn_activities").fetchall()
                if states.get(row["module"]) != "starting"]
        for workspace in {self.service.store.turn(row["turn_id"])["workspaceId"] for row in rows}:
            active = {(item["module"], item["callId"]): item for item in await self.list(workspace)}
            for row in rows:
                turn = self.service.store.turn(row["turn_id"])
                if turn["workspaceId"] != workspace:
                    continue
                match = active.get((row["module"], row["call_id"]))
                if match is None:
                    self.end(row, "unknown")
                    continue
                if match["id"] != row["activity"]:
                    new_id = match["id"]
                    match = {**match, "id": json.loads(row["value"])["id"]}
                    with self.service.store.transaction():
                        self.db.execute("UPDATE turn_activities SET activity=?,value=? WHERE module=? AND activity=?",
                                        (new_id, encode(match), row["module"], row["activity"]))
                elif not refresh:
                    continue
                self.service.runtime_card(turn, self.card(match, row["module"], "running"))

    async def list(self, workspace):
        return (await self.service.ctx.call("platform.activity.list", {"workspaceId": workspace}))["activities"]

    async def track(self, turn, action, call_id, result):
        async with self.registering:
            active = [a for a in await self.list(turn["workspaceId"]) if a["callId"] == call_id]
            for item in active:
                with self.service.store.transaction():
                    self.db.execute("INSERT OR REPLACE INTO turn_activities VALUES (?,?,?,?,?)",
                                    (item["module"], item["id"], turn["id"], call_id, encode(item)))
                    self.service.runtime_card(turn, self.card(item, action, "running"))
        if active:
            return {"status": "running", "callId": call_id, "task": active[0], "result": result}
        return {"status": "completed", "callId": call_id, "result": result}

    @staticmethod
    def card(item, action, state):
        return {"id": "activity_" + item["callId"] + "_" + item["id"], "type": "task" if state == "running" else "result",
                "state": state, "title": item["title"], "callId": item["callId"], "action": action, "op": None,
                "task": item, "openTarget": item["openTarget"], "summary": None, "error": None, "result": None}

    async def follow(self, subscription):
        async for event in subscription:
            async with self.registering:
                row = self.db.execute("SELECT * FROM turn_activities WHERE module=? AND call_id=? AND activity=?",
                                      (event["module"], event["callId"], event["activityId"])).fetchone()
                if row is not None:
                    self.end(row, event["result"], target=event.get("openTarget"), summary=event.get("summary"))

    async def progress(self, subscription):
        async for _ in subscription:
            async with self.registering:
                jobs = self.db.execute("SELECT a.*, c.workspace FROM turn_activities a JOIN turns t ON t.id=a.turn_id "
                                       "JOIN conversations c ON c.id=t.conversation").fetchall()
                for workspace in {row["workspace"] for row in jobs}:
                    active = {(item["module"], item["id"]): item for item in await self.list(workspace)}
                    for row in jobs:
                        if row["workspace"] != workspace:
                            continue
                        latest = active.get((row["module"], row["activity"]))
                        saved = json.loads(row["value"])
                        if latest is None or latest["progress"] == saved["progress"]:
                            continue
                        saved["progress"] = latest["progress"]
                        with self.service.store.transaction():
                            self.db.execute("UPDATE turn_activities SET value=? WHERE module=? AND activity=?",
                                            (encode(saved), row["module"], row["activity"]))
                            self.service.runtime_card(self.service.store.turn(row["turn_id"]), self.card(saved, row["module"], "running"))

    async def stop(self, turn):
        owned = [{"module": row[0], "id": row[1]} for row in self.db.execute(
            "SELECT module,activity FROM turn_activities WHERE turn_id=?", (turn["id"],))]
        if owned:
            await self.service.ctx.call("platform.activity.stop", {"workspaceId":turn["workspaceId"],"activities":owned})

    def end(self, row, result, *, target=None, summary=None):
        turn = self.service.store.turn(row["turn_id"])
        item = json.loads(row["value"])
        if target is not None:
            item["openTarget"] = target
        with self.service.store.transaction():
            self.db.execute("DELETE FROM turn_activities WHERE module=? AND activity=?", (row["module"], row["activity"]))
            self.service.runtime_card(turn, self.card(item, row["module"], result))
            if turn["state"] in {"completed", "failed", "interrupted", "cancelled", "stopping"}:
                return
            if result == "stopped":
                self.service.store.state(turn["id"], "cancelled")
            else:
                self.service.completions.put(turn, row["call_id"], {"business": {"terminal": True, "status": result},
                         "task": item, "result": {"status": result, "openTarget": item["openTarget"],
                                                   **({"summary": summary} if summary is not None else {})}})
