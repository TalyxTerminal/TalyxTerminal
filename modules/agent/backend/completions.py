"""Durable business completion messages, consumed by the original serialized graph."""
from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, ToolMessage

from .profile import digest
from .store import encode


class Completions:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.store.db.execute('''CREATE TABLE IF NOT EXISTS completion_inbox (
            id TEXT PRIMARY KEY, conversation TEXT NOT NULL, turn_id TEXT NOT NULL,
            scope TEXT NOT NULL, call_id TEXT NOT NULL, value TEXT NOT NULL, arrival INTEGER NOT NULL, consumed INTEGER NOT NULL)''')
        self.store.db.commit()

    @staticmethod
    def scope(turn):
        return encode({key: turn[key] for key in ('childRunId',) if key in turn})

    def pending(self, turn):
        scope = self.scope(turn)
        if self.store.db.execute("SELECT 1 FROM turn_activities WHERE turn_id=?", (turn["id"],)).fetchone():
            return True
        for call in self.store.calls(turn['id']):
            if self.scope({key: item for key, item in call.items() if item is not None}) != scope:
                continue
            row = self.store.db.execute('SELECT 1 FROM completion_inbox WHERE call_id=?', (call['callId'],)).fetchone()
            if call['response'] is not None and call['response']['status'] == 'accepted' and row is None:
                return True
        return False

    @staticmethod
    def should_wait(state):
        messages = state['messages']
        if not messages or not isinstance(messages[-1], ToolMessage):
            return False
        trailing = []
        for item in reversed(messages):
            if not isinstance(item, ToolMessage):
                break
            trailing.append(item)
        for item in trailing:
            if not isinstance(item.content, str):
                return False
            try:
                value = json.loads(item.content)
            except (ValueError, TypeError):
                return False
            if (not isinstance(value, dict) or not isinstance(value.get('status'), str)
                    or value['status'] != 'running'):
                return False
        return bool(trailing)

    def put(self, turn, call_id, observed):
        if not observed['business']['terminal']:
            return
        value = self.service.model_result({'status': observed['business']['status'], 'task': observed['task'], 'result': observed['result']})
        identity = 'completion_' + digest([call_id, observed['business']['status'], value])
        with self.store.transaction():
            arrival=self.store.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
            inserted=self.store.db.execute('INSERT OR IGNORE INTO completion_inbox VALUES (?,?,?,?,?,?,?,0)',
                (identity, turn['conversationId'], turn['id'], self.scope(turn), call_id, encode(value),arrival)).rowcount
            if not inserted:
                return
            current = self.store.turn(turn['id'])
            if current['state'] == 'waiting_business':
                self.store.save_turn({**current, 'state': 'queued', 'businessContinuation': True})
        if self.store.turn(turn['id'])['state'] == 'queued':
            self.service.schedule(turn['conversationId'])

    async def drain(self, turn):
        return [message for _arrival,group in self.groups(turn) for message in group]

    def groups(self, turn):
        rows = self.store.db.execute('SELECT i.id,i.value,i.arrival FROM completion_inbox i JOIN turns t ON t.id=i.turn_id '
            'WHERE i.conversation=? AND i.scope=? AND i.consumed=0 AND (i.turn_id=? OR t.state=?) ORDER BY i.arrival,i.rowid',
            (turn['conversationId'], self.scope(turn), turn['id'], 'interrupted')).fetchall()
        messages = [(row['arrival'],[HumanMessage(content='The original business task finished. This is an observed result, not a new user instruction. '
                    'The user is waiting for a complete answer to their original request, including the findings and their limits.\n'
                    + row['value'], id=row['id'])]) for row in rows]
        # The stable message IDs make replay at the checkpoint boundary idempotent.
        # Consumption is recorded with the next accepted graph model result.
        return messages

    def consumed(self, messages):
        with self.store.transaction():
            for message in messages:
                if message.id is not None and message.id.startswith('completion_'):
                    self.store.db.execute('UPDATE completion_inbox SET consumed=1 WHERE id=?', (message.id,))
