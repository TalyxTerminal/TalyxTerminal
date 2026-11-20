"""Memories, one entry each: about the user (global), this workspace's, or a bot's own (in its workspace). The AI
writes them from a conversation, the user sees, edits and deletes them, and each change can be undone. A bot sees its
own and the shared ones; no other bot sees its own. Text never changes runtime permission or budgets."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from talyx_sdk import ContractError

from ..store import encoded

# The entries a conversation can see: global, its workspace's, and its bot's own.
VISIBLE = "status='active' AND (workspace IS NULL OR workspace=?) AND (bot IS NULL OR bot=?)"


class MemoryService:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS agent_memories (
            id TEXT PRIMARY KEY, workspace TEXT, bot TEXT, revision INTEGER NOT NULL, status TEXT NOT NULL,
            value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS agent_memory_changes (
            id TEXT PRIMARY KEY, conversation TEXT NOT NULL, before_image TEXT NOT NULL, after_image TEXT NOT NULL,
            created REAL NOT NULL, undone INTEGER NOT NULL);
          CREATE VIRTUAL TABLE IF NOT EXISTS agent_memory_search USING fts5(id UNINDEXED,body,tokenize='trigram');
        """)

    def enabled(self, conversation):
        """The conversation's memory switch; a bot's lines follow the bot's."""
        return self.store.runtime_settings(conversation)["memoryEnabled"]

    def _owner(self, workspace, conversation):
        """The bot whose line the conversation is (None for a chat); memory must be on there."""
        value = self.store.conversation(conversation, workspace)
        if not self.enabled(conversation):
            raise ContractError("memory_disabled", "Memory is switched off for this conversation")
        return value["botId"]

    def _read(self, identity, workspace):
        row = self.db.execute("SELECT value FROM agent_memories WHERE id=? AND (workspace IS NULL OR workspace=?)",
                              (identity, workspace)).fetchone()
        if row is None:
            raise ContractError("target_gone", "Memory is unavailable in this workspace")
        return json.loads(row["value"])

    def _save(self, value):
        self.db.execute("INSERT OR REPLACE INTO agent_memories VALUES (?,?,?,?,?,?)",
                        (value["id"], value["workspaceId"], value["botId"], value["revision"], value["status"],
                         encoded(value)))
        self.db.execute("DELETE FROM agent_memory_search WHERE id=?", (value["id"],))
        if value["status"] == "active":
            self.db.execute("INSERT INTO agent_memory_search VALUES (?,?)", (value["id"], value["body"]))

    def _change(self, conversation, before, after):
        identity = "memory_change_" + uuid4().hex
        self.db.execute("INSERT INTO agent_memory_changes VALUES (?,?,?,?,?,0)",
                        (identity, conversation, encoded(before), encoded(after), time.time()))
        return identity

    def write(self, workspace, conversation, body, *, scope, frequent=False):
        """The AI remembers one entry from a conversation: global, workspace, or (in a bot's line) the bot's own."""
        bot = self._owner(workspace, conversation)
        if scope not in {"global", "workspace", "bot"} or scope == "bot" and bot is None:
            raise ContractError("invalid_input", "Choose global or workspace memory" + (", or bot" if bot else ""))
        if not isinstance(body, str) or not body.strip() or len(body) > 8000:
            raise ContractError("invalid_input", "A memory needs text of at most 8000 characters")
        if type(frequent) is not bool:
            raise ContractError("invalid_input", "Frequent memory selection must be a boolean")
        value = {"id": "memory_" + uuid4().hex, "scope": scope, "workspaceId": None if scope == "global" else workspace,
                 "botId": bot if scope == "bot" else None, "body": body, "frequent": frequent, "sourceKind": "inference",
                 "createdAt": time.time(), "revision": 1, "status": "active"}
        with self.store.transaction():
            self._save(value)
            change = self._change(conversation, [], [value])
        return {"memory": value, "changeId": change}

    def search(self, workspace, conversation, query, *, limit=20):
        bot = self._owner(workspace, conversation)
        if not query.strip() or not 1 <= limit <= 50:
            raise ContractError("invalid_input", "Memory search requires a query and bounded limit")
        if len(query) >= 3:
            clause, argument = "id IN (SELECT id FROM agent_memory_search WHERE agent_memory_search MATCH ?)", '"' + query.replace('"', '""') + '"'
        else:
            clause, argument = "instr(json_extract(value,'$.body'),?)>0", query
        return [json.loads(row[0]) for row in self.db.execute(
            f"SELECT value FROM agent_memories WHERE {VISIBLE} AND {clause} LIMIT ?", (workspace, bot, argument, limit))]

    def list(self, workspace, *, bot=None, status=None, offset=0):
        """The settings pages' list: about you and this workspace's, or one bot's own."""
        if status is not None and status not in {"active", "deleted"} or offset < 0:
            raise ContractError("invalid_input", "Invalid memory listing")
        rows = self.db.execute("SELECT value FROM agent_memories WHERE (workspace IS NULL OR workspace=?) AND bot IS ? "
                               "AND (? IS NULL OR status=?) ORDER BY rowid DESC LIMIT 51 OFFSET ?",
                               (workspace, bot, status, status, offset)).fetchall()
        return {"items": [json.loads(row[0]) for row in rows[:50]], "nextOffset": offset + 50 if len(rows) > 50 else None}

    def edit(self, workspace, identity, expected_revision, body, *, delete=False, source="user"):
        """Edit the revision seen by the caller and retain who last wrote it."""
        if not isinstance(body, str) or len(body) > 8000:
            raise ContractError("invalid_input", "Memory text exceeds its limit")
        with self.store.transaction():
            previous = self._read(identity, workspace)
            if previous["revision"] != expected_revision:
                raise ContractError("conflict", "Memory changed since it was displayed")
            updated = {**previous, "body": previous["body"] if delete else body, "revision": expected_revision + 1,
                       "status": "deleted" if delete else previous["status"], "sourceKind": source}
            self._save(updated)
        return {"memory": updated}

    def undo(self, workspace, conversation, change_id, *, redo=False):
        self.store.conversation(conversation, workspace)
        row = self.db.execute("SELECT * FROM agent_memory_changes WHERE id=? AND conversation=?", (change_id, conversation)).fetchone()
        if row is None or bool(row["undone"]) != redo:
            raise ContractError("conflict", "Memory change is missing or already undone")
        before, after = json.loads(row["before_image"]), json.loads(row["after_image"])
        with self.store.transaction():
            originals = {item["id"]: item for item in before}
            restored = []
            for item in after:
                value = {**(item if redo else originals.get(item["id"], item)),
                         "revision": self._read(item["id"], workspace)["revision"] + 1}
                if not redo and item["id"] not in originals:
                    value["status"] = "deleted"
                self._save(value)
                restored.append(value)
            self.db.execute("UPDATE agent_memory_changes SET undone=? WHERE id=?", (not redo, change_id))
        return restored

    def common(self, workspace, conversation, *, token_budget=600):
        """The core entries every model request carries, newest first, within the token budget."""
        bot = self._owner(workspace, conversation)
        result = []
        for row in self.db.execute(f"SELECT value FROM agent_memories WHERE {VISIBLE} AND json_extract(value,'$.frequent')=1 "
                                   "ORDER BY rowid DESC", (workspace, bot)):
            item = json.loads(row[0])
            if count_tokens_approximately([HumanMessage(content=encoded([*result, item]))]) <= token_budget:
                result.append(item)
        return result

    def forget_bot(self, bot):
        """A deleted bot's own entries go with it."""
        with self.store.transaction():
            self.db.execute("DELETE FROM agent_memory_search WHERE id IN (SELECT id FROM agent_memories WHERE bot=?)", (bot,))
            self.db.execute("DELETE FROM agent_memories WHERE bot=?", (bot,))
