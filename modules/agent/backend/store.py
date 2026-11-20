"""Workspace conversations, durable turn events and explicit user decisions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from talyx_sdk import ContractError

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "budget_exceeded"}
PERMISSIONS = {"read_only", "auto", "full"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def encoded(value):
    """The same JSON with sorted keys, for values compared or digested."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def stamp_card(value, previous, sequence):
    now = time.time()
    created = previous['createdAt'] if previous is not None else now
    order = previous['orderKey'] if previous is not None else f'{created:.9f}:{sequence:020d}:{value["id"]}'
    result = {**value, 'createdAt':created, 'orderKey':order, 'updatedAt':now}
    if value['type'] == 'process_group':
        # A turn's process card keeps growing; each step orders by when it appeared, so
        # thinking and replies that arrived between two steps stay between them.
        before = {item['id']:item for item in previous['steps']} if previous is not None else {}
        result['steps'] = [{**item, 'createdAt':before[item['id']]['createdAt'] if item['id'] in before else now,
            'orderKey':before[item['id']]['orderKey'] if item['id'] in before else f'{now:.9f}:{sequence:020d}:{value["id"]}:{index:06d}',
            'updatedAt':now} for index,item in enumerate(value['steps'])]
    return result


class ConversationStore:
    def __init__(self, root, *, on_event=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "conversations.sqlite")
        # Recovery below runs while the owner that listens is still being built; its events stay in the database for readers.
        self.on_event, self.pending_events = None, []
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, workspace TEXT NOT NULL,
            updated REAL NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS turns (id TEXT PRIMARY KEY, conversation TEXT NOT NULL,
            created REAL NOT NULL, state TEXT NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, conversation TEXT NOT NULL,
            turn_id TEXT NOT NULL, created REAL NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation TEXT NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS conversation_seen (conversation TEXT PRIMARY KEY, seq INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, conversation TEXT NOT NULL,
            turn_id TEXT NOT NULL, tool_call TEXT NOT NULL, state TEXT NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, conversation TEXT NOT NULL,
            turn_id TEXT NOT NULL, tool_call TEXT NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS send_requests (conversation TEXT NOT NULL,request_id TEXT NOT NULL,
            identity TEXT NOT NULL,turn_id TEXT NOT NULL,PRIMARY KEY(conversation,request_id));
          CREATE TABLE IF NOT EXISTS attachments (id TEXT PRIMARY KEY, conversation TEXT NOT NULL,
            value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS references_data (id TEXT PRIMARY KEY, conversation TEXT NOT NULL,
            turn_id TEXT NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS runtime_defaults (id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS scoped_defaults (workspace TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY(workspace,kind));
          CREATE TABLE IF NOT EXISTS conversation_runtime (conversation TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS child_runs (
            id TEXT PRIMARY KEY, conversation TEXT NOT NULL, turn_id TEXT NOT NULL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS child_details (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, child TEXT NOT NULL, card_id TEXT NOT NULL, value TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS cards_active_tasks ON cards(conversation,turn_id)
            WHERE json_extract(value,'$.type')='task';
          CREATE INDEX IF NOT EXISTS child_detail_card ON child_details(child,card_id,seq);
          CREATE INDEX IF NOT EXISTS child_scope_calls ON calls(json_extract(value,'$.childRunId'));
          CREATE INDEX IF NOT EXISTS conversation_events ON events(conversation,seq);
        """)
        self.db.execute("INSERT OR IGNORE INTO runtime_defaults VALUES (1,?)", (encode({
            "modelId": None, "summaryModelId": None, "childModelId": None, "childThinking": None,
            "childrenEnabled": True, "memoryEnabled": True, "modelThinking": {}}),))
        self.db.commit()
        self.recover()
        self.on_event = on_event

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
        else:
            try:
                with self.db:
                    self.db.execute("BEGIN")
                    yield
            except BaseException:
                self.pending_events.clear()
                raise
            pending, self.pending_events = self.pending_events, []
            if self.on_event is not None:
                for event in pending:
                    self.on_event(event)

    def close(self):
        self.db.close()

    def overrides(self, workspace, kind):
        row = self.db.execute("SELECT value FROM scoped_defaults WHERE workspace=? AND kind=?", (workspace or "", kind)).fetchone()
        return json.loads(row[0]) if row else {}

    def defaults(self, workspace=None, *, kind="chat"):
        base = {"defaultPermission": "auto", "autoCompact": True, "compactAt": 80, "botRoundLimit": 200, "botFailureLimit": 3,
                "botApprovalMinutes": 30, "botRunMinutes": 180, "notifyNeedsYou": True, "notifyResults": True,
                "notifyChannel": "", "chatNotification": "background", "memoryTokens": 2000,
                **json.loads(self.db.execute("SELECT value FROM runtime_defaults WHERE id=1").fetchone()[0])}
        if kind in {"group", "bot"}:
            base.update(self.overrides(None, "bot"))
        if workspace:
            base.update(self.overrides(workspace, "general"))
            base.update(self.overrides(workspace, "chat" if kind == "chat" else "bot"))
        return base

    def configure_defaults(self, changes, workspace=None, *, kind="chat", reset=None):
        if kind not in {"general", "chat", "bot"}:
            raise ContractError("invalid_input", "Choose general, chat or bot defaults")
        if "defaultPermission" in changes:
            self.permission(changes["defaultPermission"])
        for name in ("botRoundLimit", "botApprovalMinutes", "memoryTokens"):
            if name in changes and (type(changes[name]) is not int or changes[name] < 1):
                raise ContractError("invalid_input", "The bot limit must be a positive integer")
        if any(type(changes[x]) is not int or changes[x] < 0 for x in {"botRunMinutes", "botFailureLimit"} & changes.keys()):
            raise ContractError("invalid_input", "Choose a nonnegative duration")
        if any(type(changes[key]) is not bool for key in {"childrenEnabled", "memoryEnabled", "notifyNeedsYou", "notifyResults"} & changes.keys()):
            raise ContractError("invalid_input", "This setting requires a boolean")
        if "chatNotification" in changes and changes["chatNotification"] not in {"background","never","always"}:
            raise ContractError("invalid_input", "Choose a chat notification preference")
        if "compactAt" in changes and (type(changes["compactAt"]) is not int or not 0 < changes["compactAt"] <= 100):
            raise ContractError("invalid_input", "Choose a compaction percentage between 1 and 100")
        if "autoCompact" in changes and type(changes["autoCompact"]) is not bool:
            raise ContractError("invalid_input", "Automatic compaction takes a boolean")
        value = self.defaults()
        if set(changes) - set(value):
            raise ContractError("invalid_input", "Unknown Agent default")
        if workspace or kind == "bot":
            selected = self.overrides(workspace, kind)
            if reset:
                selected.pop(reset, None)
            selected.update(changes)
            with self.transaction():
                self.db.execute("INSERT OR REPLACE INTO scoped_defaults VALUES (?,?,?)", (workspace or "", kind, encode(selected)))
            return self.defaults(workspace, kind=kind)
        value.update(changes)
        with self.transaction():
            self.db.execute("UPDATE runtime_defaults SET value=? WHERE id=1", (encode(value),))
        return self.defaults()

    def forget_default_model(self, model_id):
        """Remove deleted selections at their own layer, restoring inherited defaults."""
        def clear(value, scoped):
            for key in ("modelId", "summaryModelId", "childModelId"):
                if value.get(key) == model_id:
                    if scoped:
                        del value[key]
                    else:
                        value[key] = None
            if "modelThinking" in value:
                value["modelThinking"].pop(model_id, None)
            return encode(value)

        with self.transaction():
            raw = self.db.execute("SELECT value FROM runtime_defaults WHERE id=1").fetchone()[0]
            self.db.execute("UPDATE runtime_defaults SET value=? WHERE id=1", (clear(json.loads(raw), False),))
            for workspace, kind, raw in self.db.execute("SELECT workspace,kind,value FROM scoped_defaults").fetchall():
                self.db.execute("UPDATE scoped_defaults SET value=? WHERE workspace=? AND kind=?",
                                (clear(json.loads(raw), True), workspace, kind))

    def runtime_settings(self, conversation):
        owner = self.conversation(conversation)
        row = self.db.execute("SELECT value FROM conversation_runtime WHERE conversation=?", (conversation,)).fetchone()
        if row is None:
            defaults = self.defaults(owner["workspaceId"], kind=owner.get("kind", "chat"))
            value = {key: defaults[key] for key in ("childrenEnabled", "memoryEnabled")}
            with self.transaction():
                self.db.execute("INSERT INTO conversation_runtime VALUES (?,?)", (conversation, encode(value)))
            return value
        return json.loads(row[0])

    @staticmethod
    def permission(value):
        if value not in PERMISSIONS:
            raise ContractError("invalid_input", "Choose read_only, auto or full permission")

    def conversation(self, conversation_id, workspace=None):
        row = self.db.execute("SELECT workspace,value FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if row is None or workspace is not None and row["workspace"] != workspace:
            raise ContractError("target_gone", "This conversation is unavailable in this workspace")
        return json.loads(row["value"])

    def save_conversation(self, value):
        value = {**value, "updatedAt": time.time()}
        self.db.execute("INSERT OR REPLACE INTO conversations VALUES (?,?,?,?)",
                        (value["id"], value["workspaceId"], value["updatedAt"], encode(value)))
        return value

    def create(self, workspace, *, title="New conversation", model=None, kind="chat", bot_id=None, line_id=None):
        now = time.time()
        defaults = self.defaults(workspace, kind=kind)
        selected = model if model is not None else defaults["modelId"]
        effort = defaults["modelThinking"].get(selected)
        value = {"id": uuid4().hex, "workspaceId": workspace, "title": title,
                 "modelId": selected, "thinking": {"effort": effort} if effort else None, "permission": defaults["defaultPermission"],
                 "createdAt": now, "updatedAt": now, "context": None, "kind": kind, "botId": bot_id,
                   "lineId": line_id, "pinned": False, "archived": False, "planMode": False}
        with self.transaction():
            value = self.save_conversation(value)
            self.event(value["id"], None, "conversation", value)
        return self.describe(value)

    def update(self, conversation_id, workspace, changes):
        allowed = {"title", "modelId", "thinking", "permission", "childrenEnabled", "memoryEnabled",
                   "planMode", "pinned", "archived"}
        if any(type(changes[key]) is not bool for key in ("planMode", "pinned", "archived") if key in changes):
            raise ContractError("invalid_input", "Plan, pinned and archived take boolean values")
        if set(changes) - allowed:
            raise ContractError("invalid_input", "Unknown conversation setting")
        if "permission" in changes:
            self.permission(changes["permission"])
        if "title" in changes and (not isinstance(changes["title"], str) or not 1 <= len(changes["title"]) <= 160):
            raise ContractError("invalid_input", "Conversation title must contain 1–160 characters")
        with self.transaction():
            previous = self.conversation(conversation_id, workspace)
            runtime = self.runtime_settings(conversation_id)
            runtime_changes = {key: changes[key] for key in ("childrenEnabled", "memoryEnabled") if key in changes}
            if any(type(value) is not bool for value in runtime_changes.values()):
                raise ContractError("invalid_input", "Runtime switches must be booleans")
            runtime.update(runtime_changes)
            self.db.execute("UPDATE conversation_runtime SET value=? WHERE conversation=?", (encode(runtime), conversation_id))
            changes = {key: value for key, value in changes.items() if key not in runtime_changes}
            value = self.save_conversation({**previous, **changes})
            self.event(conversation_id, None, "conversation", value)
        return self.describe(value)

    def list(self, workspace, offset=0):
        rows = self.db.execute("SELECT value FROM conversations WHERE workspace=? AND COALESCE(json_extract(value,'$.kind'),'chat')='chat' "
                               "ORDER BY json_extract(value,'$.pinned') DESC,updated DESC LIMIT 26 OFFSET ?",
                               (workspace, offset)).fetchall()
        items = []
        for row in rows[:25]:
            item = json.loads(row[0])
            watermark = self.db.execute("SELECT MAX(seq) FROM events WHERE conversation=?", (item["id"],)).fetchone()[0]
            items.append({**self.describe(item), "activeTurn": self.active(item["id"]), "eventWatermark": watermark or 0, "unread": self.unread(item["id"])})
        return {"items": items,
                "nextOffset": offset + 25 if len(rows) > 25 else None}

    def seen(self, conversation_id):
        """The user has the chat on screen: everything in it so far counts as read."""
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO conversation_seen SELECT ?, COALESCE(MAX(seq), 0) FROM events WHERE conversation=?",
                            (conversation_id, conversation_id))

    def unread(self, conversation_id):
        """A card arrived since the user last had the chat on screen."""
        row = self.db.execute("SELECT seq FROM conversation_seen WHERE conversation=?", (conversation_id,)).fetchone()
        return self.db.execute("SELECT 1 FROM events WHERE conversation=? AND seq>? AND json_extract(value,'$.type')='card' LIMIT 1",
                               (conversation_id, row[0] if row is not None else 0)).fetchone() is not None

    def activity(self, conversation_id):
        pending = self.pending_business_turns(conversation_id)
        waiting = self.db.execute("SELECT 1 FROM approvals WHERE conversation=? AND state='waiting' LIMIT 1", (conversation_id,)).fetchone()
        return {"running": self.active(conversation_id) is not None or bool(pending), "waitingApproval": waiting is not None}

    def pending_business_turns(self, conversation_id, *, exclude_call_id=None):
        return [row[0] for row in self.db.execute("SELECT turn_id FROM cards WHERE conversation=? AND id!=? AND json_extract(value,'$.type')='task' "
            "UNION SELECT turn_id FROM child_runs WHERE conversation=? AND json_extract(value,'$.businessRunning')=1",
            (conversation_id, "task_" + (exclude_call_id or ""), conversation_id))]

    def panel_conversation(self, workspace, selected):
        row = self.db.execute("SELECT id FROM conversations WHERE workspace=? AND "
                              "(id=? OR EXISTS (SELECT 1 FROM json_each(conversations.value,'$.copyAncestors') WHERE value=?)) "
                              "ORDER BY id=? DESC LIMIT 1", (workspace, selected, selected, selected)).fetchone()
        return row[0] if row else None

    def describe(self, conversation):
        return {**conversation, **self.runtime_settings(conversation["id"]), "activity": self.activity(conversation["id"])}

    def event(self, conversation_id, turn_id, kind, payload):
        if kind == "conversation":
            payload = self.describe(payload)
        value = {"conversationId": conversation_id, "workspaceId": self.conversation(conversation_id)["workspaceId"], "turnId": turn_id, "type": kind, "activity": self.activity(conversation_id),
                 "payload": payload, "at": time.time()}
        cursor = self.db.execute("INSERT INTO events(conversation,value) VALUES (?,?)", (conversation_id, encode(value)))
        event = {**value, "seq": cursor.lastrowid}
        self.pending_events.append(event)
        return event

    def events(self, conversation_id, workspace, after=0):
        self.conversation(conversation_id, workspace)
        rows = self.db.execute("SELECT seq,value FROM events WHERE conversation=? AND seq>? ORDER BY seq LIMIT 101",
                               (conversation_id, after)).fetchall()
        return {"items": [{**json.loads(row["value"]), "seq": row["seq"]} for row in rows[:100]],
                "nextAfter": rows[99]["seq"] if len(rows) > 100 else None}

    def with_materials(self, card):
        if card.get("role") == "user" and card.get("attachments") and "materials" not in card:
            ids = card["attachments"]
            rows = self.db.execute("SELECT id,json_extract(value,'$.name') FROM attachments WHERE id IN ("
                                   + ",".join("?" for _ in ids) + ")", ids)
            names = dict(rows)
            return {**card, "materials": [{"id": key, "name": names[key]} for key in ids if key in names]}
        return card

    def card(self, conversation_id, turn_id, value):
        with self.transaction():
            value = self.with_materials(value)
            old = self.db.execute("SELECT created,value FROM cards WHERE id=?", (value["id"],)).fetchone()
            sequence = self.db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM events').fetchone()[0]
            value = stamp_card(value,json.loads(old['value']) if old is not None else None,sequence)
            self.db.execute("INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?)",
                (value["id"], conversation_id, turn_id if turn_id is not None else "", value['createdAt'], encode(value)))
            self.event(conversation_id, turn_id, "card", value)
        return value

    def read(self, conversation_id, workspace, *, before=None):
        conversation = self.conversation(conversation_id, workspace)
        rows = self.db.execute("SELECT created,turn_id,value FROM cards WHERE conversation=? AND created<? ORDER BY created DESC LIMIT 51",
            (conversation_id, time.time() + 1 if before is None else before)).fetchall()
        # A page boundary must not split the steps under one turn's disclosure.
        page = rows[:50]
        if len(rows) > 50:
            floor = page[-1]["created"]
            while True:
                first = self.db.execute("SELECT MIN(created) FROM cards WHERE conversation=? AND turn_id != '' "
                                        "AND turn_id IN (SELECT turn_id FROM cards WHERE conversation=? AND created>=? AND created<?)",
                                        (conversation_id, conversation_id, floor, before if before is not None else time.time()+1)).fetchone()[0]
                if first is None or first >= floor:
                    break
                floor = first
            page = self.db.execute("SELECT created,turn_id,value FROM cards WHERE conversation=? AND created>=? AND created<? "
                                   "ORDER BY created DESC,rowid DESC", (conversation_id, floor, before if before is not None else time.time()+1)).fetchall()
        more = len(rows) > 50 and self.db.execute("SELECT 1 FROM cards WHERE conversation=? AND created<? LIMIT 1",
                                                  (conversation_id, page[-1]["created"])).fetchone()
        turns = self.db.execute("SELECT value FROM turns WHERE conversation=? AND (state NOT IN "
                                "('completed','failed','cancelled','interrupted','budget_exceeded') OR id IN "
                                "(SELECT turn_id FROM cards WHERE conversation=? AND created>=? AND created<=?)) "
                                "ORDER BY created DESC", (conversation_id, conversation_id,
                                page[-1]["created"] if page else 0, page[0]["created"] if page else 0)).fetchall()
        water = self.db.execute("SELECT MAX(seq) FROM events WHERE conversation=?", (conversation_id,)).fetchone()[0]
        return {"conversation": self.describe(conversation), "cards": [{**self.with_materials(json.loads(row["value"])), "turnId": row["turn_id"]} for row in reversed(page)],
                "turns": [json.loads(row[0]) for row in reversed(turns)], "watermark": water or 0,
                "nextBefore": page[-1]["created"] if more else None}

    def turn(self, turn_id):
        row = self.db.execute("SELECT value FROM turns WHERE id=?", (turn_id,)).fetchone()
        if row is None:
            raise ContractError("target_gone", "This turn is unavailable")
        return json.loads(row[0])

    def save_turn(self, value):
        self.db.execute("INSERT OR REPLACE INTO turns VALUES (?,?,?,?,?)",
            (value["id"], value["conversationId"], value["createdAt"], value["state"], encode(value)))
        self.event(value["conversationId"], value["id"], "turn", value)
        return value

    def new_turn(self, conversation_id, workspace, text, *, view=None, attachments=(), wake=None):
        conversation = self.conversation(conversation_id, workspace)
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise ContractError("invalid_input", "Enter a message of at most 100000 characters")
        value = {"id": uuid4().hex, "conversationId": conversation_id, "workspaceId": workspace,
                 "state": "queued", "createdAt": time.time(), "startedAt": None, "finishedAt": None,
                 "text": text, "view": view, "attachments": list(attachments), "inbox": [], "interrupts": {}, "error": None,
                   "businessContinuation": False, "unattended": wake is not None,
                   "wake": wake or {"kind": "user"}}
        value["messageId"] = "user_" + value["id"]
        with self.transaction():
            self.save_turn(value)
            if wake is None:
                self.card(conversation_id, value["id"], {"id": value["messageId"], "type": "answer",
                      "role": "user", "text": text, "references": [], "sources": [], "view": view,
                      "attachments": list(attachments)})
            if conversation["title"] == "New conversation":
                conversation["title"] = text.strip()[:70]
            self.save_conversation(conversation)
        return value

    def state(self, turn_id, state, *, error=None):
        with self.transaction():
            value = self.turn(turn_id)
            if value["state"] in TERMINAL:
                return value
            value.update(state=state, error=error)
            if state == "running" and value["startedAt"] is None:
                value["startedAt"] = time.time()
            if state in TERMINAL:
                value["finishedAt"] = time.time()
            result = self.save_turn(value)
            if state in TERMINAL and state != "completed":
                for row in self.db.execute("SELECT value FROM cards WHERE turn_id=? AND json_extract(value,'$.type')='process_group'", (turn_id,)).fetchall():
                    card = json.loads(row[0])
                    running = [step for step in card["steps"] if step["state"] == "running"]
                    if running:
                        for step in running:
                            step.update(state="failed" if state == "budget_exceeded" else state, finishedAt=value["finishedAt"])
                        self.card(value["conversationId"], turn_id, card)
            return result

    def active(self, conversation_id):
        row = self.db.execute("SELECT value FROM turns WHERE conversation=? AND state NOT IN ('completed','failed','cancelled','interrupted','budget_exceeded') ORDER BY state NOT IN ('queued','running','waiting_approval'),created LIMIT 1",
                              (conversation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def supplement(self, turn_id, text, *, view=None, attachments=()):
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise ContractError("invalid_input", "Enter a supplemental message of at most 100000 characters")
        with self.transaction():
            turn = self.turn(turn_id)
            message_id = "user_" + uuid4().hex
            self.card(turn["conversationId"], turn_id, {"id": message_id, "type": "answer", "role": "user",
                "text": text, "references": [], "sources": [], "view": view, "attachments": list(attachments)})
            arrival=self.db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
            turn["inbox"].append({"messageId": message_id, "text": text, "view": view, "attachments": list(attachments),'arrivalSequence':arrival})
            self.save_turn(turn)
        return turn

    def drain(self, turn_id):
        with self.transaction():
            turn = self.turn(turn_id)
            inbox, turn["inbox"] = turn["inbox"], []
            if inbox:
                self.save_turn(turn)
        return inbox

    def call(self, turn_id, tool_call, call_id, action, value, *, child_run_id=None):
        turn = self.turn(turn_id)
        record = {"callId": call_id, "toolCallId": tool_call, "action": action, "input": value, "result": None, "response": None, "error": None, "state": "started"}
        record["childRunId"] = child_run_id
        with self.transaction():
            self.db.execute("INSERT INTO calls VALUES (?,?,?,?,?)", (call_id, turn["conversationId"], turn_id, tool_call, encode(record)))
        return record

    def finish_call(self, call_id, *, host_id=None, result=None, error=None, response=None):
        with self.transaction():
            row = self.db.execute("SELECT value FROM calls WHERE id=?", (call_id,)).fetchone()
            value = {**json.loads(row[0]), "result": result, "response": response, "error": error, "state": "completed" if error is None else "failed"}
            value["callId"] = host_id or call_id
            self.db.execute("UPDATE calls SET id=?,value=? WHERE id=?", (value["callId"], encode(value), call_id))

    def calls(self, turn_id):
        return [json.loads(row[0]) for row in self.db.execute("SELECT value FROM calls WHERE turn_id=? ORDER BY rowid", (turn_id,))]

    def sent_request(self, conversation_id, request_id, identity):
        row = self.db.execute("SELECT identity,turn_id FROM send_requests WHERE conversation=? AND request_id=?", (conversation_id, request_id)).fetchone()
        if row is None:
            return None
        if row[0] != identity:
            raise ContractError("conflict", "This send request id was already used for different input")
        return self.turn(row[1])

    def remember_send(self, conversation_id, request_id, identity, turn_id):
        self.db.execute("INSERT INTO send_requests VALUES (?,?,?,?)", (conversation_id, request_id, identity, turn_id))

    @staticmethod
    def tool_identity(action, value):
        return hashlib.sha256(json.dumps({"action": action, "input": value}, ensure_ascii=False, allow_nan=False,
                                         sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def approval_for(self, turn_id, tool_call):
        row = self.db.execute("SELECT value FROM approvals WHERE turn_id=? AND tool_call=? ORDER BY rowid DESC LIMIT 1", (turn_id, tool_call)).fetchone()
        return json.loads(row[0]) if row else None

    def approval(self, approval_id, workspace):
        row = self.db.execute("SELECT conversation,value FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is None:
            raise ContractError("target_gone", "This approval is unavailable")
        self.conversation(row["conversation"], workspace)
        return json.loads(row["value"])

    def request_approval(self, turn_id, tool_call, call_id, action, value, proposal, *, child_run_id=None):
        turn = self.turn(turn_id)
        approval = {"id": uuid4().hex, "version": 1, "turnId": turn_id, "conversationId": turn["conversationId"],
            "toolCallId": tool_call, "callId": call_id, "action": action, "input": value, "proposal": proposal,
            "state": "waiting", "decision": None, "reason": "", "createdAt": time.time()}
        approval["childRunId"] = child_run_id
        duration = self.defaults(turn["workspaceId"], kind="bot")["botApprovalMinutes"]
        approval["expiresAt"] = approval["createdAt"] + duration * 60 if turn["unattended"] else None
        with self.transaction():
            self.db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?)", (approval["id"], turn["conversationId"], turn_id, tool_call, "waiting", encode(approval)))
            self.state(turn_id, "waiting_approval")
            self.card(turn["conversationId"], turn_id, {"id": approval["id"], "type": "approval", **approval})
        return approval

    def decide(self, approval_id, workspace, version, decision, reason="", *, expired=False):
        if decision not in {"allow", "allow_conversation", "deny"}:
            raise ContractError("invalid_input", "Choose allow, allow_conversation or deny")
        with self.transaction():
            value = self.approval(approval_id, workspace)
            if value["state"] != "waiting" or value["version"] != version or self.turn(value["turnId"])["state"] in TERMINAL:
                raise ContractError("conflict", "This approval is no longer waiting")
            state = "expired" if expired else "denied" if decision == "deny" else "allowed"
            value.update(state=state, decision=decision, reason=reason, version=version + 1)
            self.db.execute("UPDATE approvals SET state=?,value=? WHERE id=?", (value["state"], encode(value), approval_id))
            self.card(value["conversationId"], value["turnId"], {"id": value["id"], "type": "approval", **value})
            pending = self.db.execute("SELECT 1 FROM approvals WHERE turn_id=? AND state='waiting'", (value["turnId"],)).fetchone()
            self.state(value["turnId"], "waiting_approval" if pending else "running")
        return value

    def consume_approval(self, approval_id):
        with self.transaction():
            row = self.db.execute("SELECT value FROM approvals WHERE id=?", (approval_id,)).fetchone()
            value = json.loads(row[0])
            if value["state"] != "allowed":
                raise ContractError("permission_denied", "This one-time approval has already been consumed")
            value["state"] = "consumed"
            self.db.execute("UPDATE approvals SET state='consumed',value=? WHERE id=?", (encode(value), approval_id))
        return value

    def invalidate_approvals(self, turn_id, reason, *, child_run_id=None):
        with self.transaction():
            rows = self.db.execute("SELECT value FROM approvals WHERE turn_id=? AND state IN ('waiting','allowed')", (turn_id,)).fetchall()
            for row in rows:
                value = json.loads(row[0])
                if child_run_id is not None and value["childRunId"] != child_run_id:
                    continue
                value.update(state="interrupted", version=value["version"] + 1, reason=reason)
                self.db.execute("UPDATE approvals SET state='interrupted',value=? WHERE id=?", (encode(value), value["id"]))
                self.card(value["conversationId"], turn_id, {"type": "approval", **value})

    def recover(self, conversations=None):
        """End what was under way: after a restart everywhere, or in conversations just copied (it goes on in the
        original only)."""
        scope, values = "", []
        if conversations is not None:
            scope, values = f" AND conversation IN ({','.join('?' * len(conversations))})", list(conversations)
        message = "TALYX restarted; this turn was not resumed" if conversations is None else "This turn goes on in the original workspace"
        with self.transaction():
            rows = self.db.execute("SELECT value FROM turns WHERE state NOT IN ('completed','failed','cancelled','interrupted','budget_exceeded')" + scope, values).fetchall()
            for row in rows:
                value = json.loads(row[0])
                if conversations is None:
                    if value["state"] == "waiting_business":
                        continue
                    if value["interrupts"] and any(
                        self.approval(identity, value["workspaceId"])["state"] in {"waiting","allowed","denied","expired"}
                        for identity in value["interrupts"].values()
                    ):
                        continue
                value.update(state="interrupted", finishedAt=time.time(), error={"code": "interrupted", "message": message})
                self.save_turn(value)
            rows = self.db.execute("SELECT value FROM approvals WHERE state IN ('waiting','allowed')" + scope, values).fetchall()
            for row in rows:
                value = json.loads(row[0])
                if conversations is None and value["id"] in self.turn(value["turnId"])["interrupts"].values():
                    continue
                value.update(state="interrupted", version=value["version"] + 1)
                self.db.execute("UPDATE approvals SET state='interrupted',value=? WHERE id=?", (encode(value), value["id"]))
                self.card(value["conversationId"], value["turnId"], {"type": "approval", **value})
            for row in self.db.execute("SELECT conversation,turn_id,value FROM cards WHERE json_extract(value,'$.type') IN ('task','process_group')" + scope, values).fetchall():
                value = json.loads(row["value"])
                if conversations is None and self.turn(row["turn_id"])["state"] in {"waiting_approval","waiting_business"}:
                    continue
                if value["type"] == "task":
                    value.update(type="result", state="interrupted")
                else:
                    pending = [step for step in value["steps"] if step["state"] in {"running", "waiting"}]
                    if not pending:
                        continue
                    for step in pending:
                        step.update(state="interrupted", finishedAt=time.time())
                self.card(row["conversation"], row["turn_id"], value)

    def delete(self, conversation_id, workspace):
        self.conversation(conversation_id, workspace)
        if self.active(conversation_id) is not None:
            raise ContractError("conflict", "Stop the conversation before deleting it")
        with self.transaction():
            for table in ("turns", "cards", "events", "approvals", "calls", "send_requests", "attachments", "references_data"):
                self.db.execute(f"DELETE FROM {table} WHERE conversation=?", (conversation_id,))
            self.db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            self.db.execute("DELETE FROM conversation_runtime WHERE conversation=?", (conversation_id,))
            self.db.execute("DELETE FROM conversation_seen WHERE conversation=?", (conversation_id,))
