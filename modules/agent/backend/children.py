"""UI state and accounting for tasks dispatched by the Deep Agents subagent middleware."""
import asyncio
import json
import time
from contextvars import ContextVar

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .store import digest, encoded, stamp_card

CHILD = ContextVar("agent_child", default=None)


class ChildDisplay(AgentMiddleware):
    def __init__(self, service, turn):
        self.service, self.turn = service, turn

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        if call["name"] != "task":
            return await handler(request)
        runs = self.service.children
        identity = runs.identity(self.turn, call["id"])
        conversation = self.service.execution_conversation(self.turn)
        defaults = self.service.store.defaults(self.turn["workspaceId"], kind=conversation.get("kind", "chat"))
        description = call["args"]["description"]
        row = runs.store.db.execute("SELECT value FROM child_runs WHERE id=?", (identity,)).fetchone()
        record = json.loads(row[0]) if row else {
            "id":identity, "turnId":self.turn["id"], "conversationId":conversation["id"], "workspaceId":self.turn["workspaceId"],
            "title":" ".join(description.split())[:96], "description":description,
            "modelId":defaults["childModelId"] or conversation["modelId"], "thinking":defaults["childThinking"] or conversation["thinking"],
            "detailCursor":0, "steps":0, "lastAction":None, "businessRunning":False, "result":None,
            "createdAt":time.time(), "finishedAt":None}
        if record.get("state") == "cancelled":
            return ToolMessage(content="This subagent was stopped.",tool_call_id=call["id"])
        runs.save({**record, "state":"running"})
        token = CHILD.set({**self.turn, "childRunId":identity, "modelId":record["modelId"], "thinking":record["thinking"]})
        try:
            task = asyncio.create_task(handler(request))
            runs.active[identity] = task
            result = await task
            messages = result.update["messages"] if isinstance(result, Command) else [result]
            text = next((m.text for m in messages if isinstance(m, ToolMessage)), "")
            runs.save({**runs.read(identity), "state":"completed","result":text,"finishedAt":time.time()})
            return result
        except GraphInterrupt:
            runs.save({**runs.read(identity),"state":"waiting_approval"})
            raise
        except asyncio.CancelledError:
            runs.save({**runs.read(identity),"state":"cancelled","finishedAt":time.time()})
            if identity in runs.stopping:
                return ToolMessage(content="The user stopped this subagent.",tool_call_id=call["id"])
            raise
        finally:
            CHILD.reset(token)
            runs.active.pop(identity,None)
            runs.stopping.discard(identity)


class ChildRuns:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.active, self.stopping = {}, set()

    def read(self, identity, workspace=None, conversation=None):
        row = self.store.db.execute("SELECT value FROM child_runs WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ContractError("target_gone", t("The child run is unavailable"))
        value = json.loads(row[0])
        self.store.conversation(value["conversationId"], workspace)
        if conversation is not None and value["conversationId"] != conversation:
            raise ContractError("permission_denied", t("Child run belongs to another conversation"))
        return value

    @staticmethod
    def identity(parent, tool_call):
        """Subagents run only under the main run, so the turn and the task call name one."""
        return "child_" + digest([parent["id"], tool_call])[:32]

    def save(self, value):
        value = {**value, "usage": self.service.usage.summary(group_by="model", child_run_id=value["id"])["groups"]}
        with self.store.transaction():
            self.store.db.execute("INSERT OR REPLACE INTO child_runs VALUES (?,?,?,?)",
                (value["id"], value["conversationId"], value["turnId"], encoded(value)))
            parent = self.store.turn(value["turnId"])
            self.service.runtime_card(parent, {"type": "child_run", **self.project(value)})
        return value

    @staticmethod
    def project(value):
        return {**{key: item for key, item in value.items() if key not in {"result", "interrupts"}},
                "resultPreview": value["result"][:600] if value["result"] is not None else None}

    def detail(self, turn, card):
        identity = turn["childRunId"]
        with self.store.transaction():
            previous = self.store.db.execute('SELECT value FROM child_details WHERE child=? AND card_id=? ORDER BY seq DESC LIMIT 1', (identity,card['id'])).fetchone()
            sequence = self.store.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM child_details').fetchone()[0]
            value = stamp_card({**card, "childRunId": identity},json.loads(previous[0]) if previous is not None else None,sequence)
            cursor = self.store.db.execute("INSERT INTO child_details(child,card_id,value) VALUES (?,?,?)", (identity, card["id"], encoded(value)))
            child = self.read(identity)
            self.store.event(turn["conversationId"], turn["id"], "child_detail", {"childRunId": identity, "seq": cursor.lastrowid, "card": value})
            child["detailCursor"] = cursor.lastrowid
            if card["type"] == "process_group" and (previous is None or not card["id"].startswith("model_")):
                models = self.store.db.execute("SELECT COUNT(DISTINCT card_id) FROM child_details WHERE child=? AND card_id LIKE 'model_%'", (identity,)).fetchone()[0]
                calls = self.store.db.execute("SELECT COUNT(*) FROM calls WHERE json_extract(value,'$.childRunId')=?", (identity,)).fetchone()[0]
                child["steps"] = models + calls
            if card["id"].startswith("process_") and card["steps"]:
                # Its newest tool call, for the one line that says what it is doing now.
                child["lastAction"] = card["steps"][-1]["title"]
            if card["type"] in {"task", "result"}:
                child["businessRunning"] = self.store.db.execute("SELECT 1 FROM child_details d WHERE child=? AND seq=(SELECT MAX(seq) FROM child_details c WHERE c.child=d.child AND c.card_id=d.card_id) AND json_extract(value,'$.type')='task' LIMIT 1", (identity,)).fetchone() is not None
            self.save(child)

    def details(self, identity, workspace, conversation, after=0):
        child = self.read(identity, workspace, conversation)
        rows = self.store.db.execute("SELECT seq,value FROM child_details WHERE child=? AND seq>? ORDER BY seq LIMIT 51", (identity, after)).fetchall()
        return {"child": self.project(child), "items": [{"seq": row["seq"], "card": json.loads(row["value"])} for row in rows[:50]],
                "nextAfter": rows[49]["seq"] if len(rows) > 50 else None}

    async def stop(self, identity, workspace, conversation, *, resume_parent=True):
        child = self.read(identity, workspace, conversation)
        if child["state"] in {"completed","cancelled","failed","interrupted"}:
            return child
        self.stopping.add(identity)
        task = self.active.get(identity)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.store.invalidate_approvals(child["turnId"], "This subagent was stopped", child_run_id=identity)
        result = self.save({**self.read(identity),"state":"cancelled","finishedAt":time.time()})
        if resume_parent and self.store.turn(child["turnId"])["state"] == "waiting_approval":
            self.service.schedule(conversation, resume=True)
        return result

    async def delete(self, conversation):
        with self.store.transaction():
            self.store.db.execute("DELETE FROM child_details WHERE child IN (SELECT id FROM child_runs WHERE conversation=?)", (conversation,))
            self.store.db.execute("DELETE FROM child_runs WHERE conversation=?", (conversation,))
