"""Copying a workspace's conversations and memories into a copied workspace.

Everything kept for a conversation is copied: its history and cards, the context the model is given (original
messages and summaries), permission rules, child runs and attachments. The caller copies the corresponding graph
threads through the checkpoint saver. Work in progress continues only in the original.

Copies have independent row identities and internal references. User-authored content is preserved verbatim."""

from __future__ import annotations

import json
import re
import shutil
from uuid import uuid4

HEX = re.compile(r"[0-9a-f]{32}")

# (table, column, the table whose ids select its rows, that table's id column), in an order where each selecting
# table comes first. "" selects by the source workspace.
TABLES = [
    ("bot_entries", "workspace", "", ""),
    ("bot_sections", "workspace", "", ""),
    ("bot_messages", "workspace", "", ""),
    ("bot_routines", "workspace", "", ""),
    ("bot_wakes", "workspace", "", ""),
    ("bot_edits", "workspace", "", ""),
    ("bot_lines", "bot", "bot_entries", "id"),
    ("conversations", "workspace", "", ""),
    ("scoped_defaults", "workspace", "", ""),
    ("knowledge_folders", "workspace", "", ""),
    *[(table, "conversation", "conversations", "id") for table in (
        "turns", "cards", "events", "approvals", "calls", "send_requests", "attachments",
        "references_data", "conversation_runtime", "child_runs",
        "completion_inbox", "conversation_allowances", "agent_memory_changes")],
    ("child_details", "child", "child_runs", "id"),
    ("compaction_requests", "turn_id", "turns", "id"),
    ("agent_memories", "workspace", "", ""),
    ("agent_memory_search", "id", "agent_memories", "id"),
]


def _columns(db, table):
    """Each column's name and whether it is the table's own id (a single-column key); numbered rows lose their number."""
    info = db.execute(f"PRAGMA table_info({table})").fetchall()
    keys = [row for row in info if row[5]]
    return [(row[1], len(keys) == 1 and row[5] == 1) for row in info
            if not (len(keys) == 1 and row[5] == 1 and row[2].upper() == "INTEGER")]


def _chunks(values, size=500):
    return [values[start:start + size] for start in range(0, len(values), size)]


def _select(db, table, column, values):
    return [row for chunk in _chunks(values) for row in db.execute(
        f"SELECT * FROM {table} WHERE {column} IN ({','.join('?' * len(chunk))}) ORDER BY rowid", chunk)]


# Remap only identities owned by the copied workspace. User text, tool inputs and outputs,
# market/object IDs and filesystem paths are historical evidence and stay byte-for-byte intact.
ID_COLUMNS = {"id", "workspace", "conversation", "bot", "line", "speaker", "turn_id", "child",
              "card_id", "call_id", "message", "object_id"}
ID_FIELDS = {"id", "workspaceId", "conversationId", "turnId", "botId", "lineId", "childRunId",
             "messageId", "routineId", "modelStepId", "approvalId", "callId", "changeId", "referenceId", "cardId", "bot"}
ID_LISTS = {"attachments", "members", "mentions"}
RECORD_FIELDS = {"payload", "items", "inbox", "references", "steps", "materials", "memory", "wake",
                 "activity", "reply", "sources", "card", "queued"}


class Rewrite:
    def __init__(self, source, copy):
        self.ids = {source: copy}
        self.tokens = {source: copy}

    def learn(self, identity):
        if identity not in self.ids:
            for token in HEX.findall(identity):
                self.tokens.setdefault(token, uuid4().hex)
            self.ids[identity] = (HEX.sub(lambda m: self.tokens[m.group()], identity)
                                  if HEX.search(identity) else f"{identity}-{uuid4().hex[:8]}")

    def __call__(self, value):
        return self.ids.get(value, value)

    def record(self, value):
        if isinstance(value, list):
            return [self.record(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = dict(value)
        if value.get("type") == "answer" and value["role"] == "assistant":
            for reference in value["references"]:
                old = reference["id"]
                result["text"] = result["text"].replace(f"[[ref:{old}]]", f"[[ref:{self(old)}]]")
        for key, item in value.items():
            if key in ID_FIELDS and isinstance(item, str):
                result[key] = self(item)
            elif key in ID_LISTS and isinstance(item, list):
                result[key] = [self(v) if isinstance(v, str) else self.record(v) for v in item]
            elif key in RECORD_FIELDS:
                result[key] = self.record(item)
            elif key == "interrupts":
                result[key] = {k: self(v) for k, v in item.items()}
            elif key == "knowledge":
                result[key] = [re.sub(r"^/knowledge/([^/]+)", lambda m: "/knowledge/" + self(m[1]), v) for v in item]
        return result

    def column(self, name, value):
        if name in ID_COLUMNS:
            return self(value)
        if value is not None and name in {"value", "scope", "cause", "reply", "before", "after", "before_image", "after_image"}:
            return json.dumps(self.record(json.loads(value)), ensure_ascii=False, separators=(",", ":"))
        if name == "mentions":
            return json.dumps([self(v) for v in json.loads(value)])
        return value


def copy_workspace(service, source: str, copy: str) -> dict[str, str]:
    store, db = service.store, service.store.db
    rewrite = Rewrite(source, copy)
    rows: dict[str, list] = {}
    for table, column, parent, parent_column in TABLES:
        values = [source] if not parent else [row[parent_column] for row in rows[parent]]
        rows[table] = _select(db, table, column, values)
        for name, own in _columns(db, table):
            if own:
                for row in rows[table]:
                    rewrite.learn(row[name])

    copied = []
    with store.transaction():
        for table, *_ in TABLES:
            columns = _columns(db, table)
            names = [name for name, _own in columns]
            for row in rows[table]:
                values = [rewrite.column(name, row[name]) for name in names]
                if table == "conversations":
                    value = json.loads(values[names.index("value")])
                    value["copiedFrom"] = row["id"]
                    parent = json.loads(row["value"])
                    value["copyAncestors"] = [*parent.get("copyAncestors", []), parent["id"]]
                    values[names.index("value")] = json.dumps(value, ensure_ascii=False, allow_nan=False,
                                                              separators=(",", ":"))
                    copied.append(values[names.index("id")])
                elif table == "bot_entries" and row["kind"] == "bot":
                    bot = json.loads(values[names.index("value")])
                    bot.update(paused=True, unread=False)
                    values[names.index("value")] = json.dumps(bot, ensure_ascii=False)
                    status = service.usage.group_status(row["id"])
                    service.usage.configure_group(bot["id"], copy, monthly_limit=status["monthlyLimit"],
                                                  currency=status["currency"])
                elif table == "bot_routines":
                    routine = json.loads(values[names.index("value")])
                    routine.update(active=False, cursor=None)
                    values[names.index("value")] = json.dumps(routine, ensure_ascii=False)
                elif table == "bot_wakes" and row["result"] is None:
                    values[names.index("result")] = "stopped"
                elif table == "cards":
                    card = json.loads(values[names.index("value")])
                    if card["type"] == "changes":
                        for item in card["items"]:
                            item["reversible"] = False
                        values[names.index("value")] = json.dumps(card,ensure_ascii=False)
                db.execute(f"INSERT INTO {table} ({','.join(names)}) VALUES ({','.join('?' * len(names))})", values)
        # Work that was under way in the original goes on there only.
        store.recover(copied)
    for conversation in copied:
        store.seen(conversation)

    folder = service.root / "attachments"
    for row in rows["attachments"]:
        shutil.copyfile(folder / row["id"], folder / rewrite(row["id"]))
    return {row["id"]: rewrite(row["id"]) for row in rows["conversations"]}
