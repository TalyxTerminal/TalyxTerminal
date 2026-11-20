"""Undoing a remembered line, and compaction requests."""


class AgentKnowledge:
    def __init__(self, service):
        self.service, self.db = service, service.store.db
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS compaction_requests (
            message TEXT PRIMARY KEY, turn_id TEXT NOT NULL, consumed INTEGER NOT NULL);
        """)

    def memory_decision(self, turn, card, *, redo):
        """Undo or redo the memory a "Remembered" line shows; a bot run's record of it follows."""
        store = self.service.store
        with store.transaction():
            values = self.service.memory.undo(turn["workspaceId"], turn["conversationId"], card["changeId"], redo=redo)
            memory = next(item for item in values if item["id"] == card["memory"]["id"])
            self.service.runtime_card(turn, {**card, "memory": memory})
            self.db.execute("UPDATE turn_changes SET undone=? WHERE turn_id=? AND call_id=?", (not redo, turn["id"], card["changeId"]))
        return memory

    def queue_compact(self, turn, message):
        if message["text"] == "/compact" or message["text"].startswith("/compact "):
            self.db.execute("INSERT OR IGNORE INTO compaction_requests VALUES (?,?,0)", (message["messageId"], turn["id"]))

    def compact_pending(self, turn):
        return [row[0] for row in self.db.execute("SELECT message FROM compaction_requests WHERE turn_id=? AND consumed=0", (turn["id"],))]

    def compact_consumed(self, messages):
        with self.service.store.transaction():
            self.db.executemany("UPDATE compaction_requests SET consumed=1 WHERE message=?", ((message,) for message in messages))

    def release(self, workspace, conversation):
        self.service.store.conversation(conversation, workspace)
        with self.service.store.transaction():
            self.db.execute("DELETE FROM compaction_requests WHERE turn_id IN (SELECT id FROM turns WHERE conversation=?)", (conversation,))
