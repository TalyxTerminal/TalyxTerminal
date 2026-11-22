"""What the user allowed for a whole conversation ("Allow for this chat"), kept until removed or the chat's
permission gets stricter. A rule is an action with the risks it was allowed for: an action whose branches carry other
risks is asked again."""

import json
import time
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .store import encoded

# Allowing an action for the whole conversation never covers these: each one is asked again.
ASKED_EVERY_TIME = {"delete", "funds"}


def proposal(effects):
    """What the user is asked to allow: the operation's effects beyond reading."""
    return {"risks": [effect for effect in effects if effect != "read"]}


def rememberable(proposal):
    return not ASKED_EVERY_TIME & set(proposal["risks"])


class ConversationPermissions:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.execute("""
          CREATE TABLE IF NOT EXISTS conversation_allowances (
            id TEXT PRIMARY KEY, conversation TEXT NOT NULL, action TEXT NOT NULL, risks TEXT NOT NULL,
            value TEXT NOT NULL)""")

    def remember(self, approval):
        if approval["decision"] != "allow_conversation" or approval["state"] != "allowed":
            raise ContractError("permission_denied", t("Persistent permission requires the user's approval decision"))
        if not rememberable(approval["proposal"]):
            return None
        conversation, action, risks = approval["conversationId"], approval["action"], sorted(approval["proposal"]["risks"])
        self.store.conversation(conversation)
        with self.store.transaction():
            existing = self.db.execute("SELECT value FROM conversation_allowances WHERE conversation=? AND action=? AND risks=?",
                                       (conversation, action, encoded(risks))).fetchone()
            if existing is not None:
                return json.loads(existing[0])
            value = {"id": "rule_" + uuid4().hex, "conversationId": conversation, "action": action, "risks": risks,
                     "approvalId": approval["id"], "createdAt": time.time()}
            self.db.execute("INSERT INTO conversation_allowances VALUES (?,?,?,?,?)",
                            (value["id"], conversation, action, encoded(risks), encoded(value)))
        return value

    def allows(self, conversation, action, proposal):
        return rememberable(proposal) and self.db.execute(
            "SELECT 1 FROM conversation_allowances WHERE conversation=? AND action=? AND risks=?",
            (conversation, action, encoded(sorted(proposal["risks"])))).fetchone() is not None

    def list(self, conversation):
        self.store.conversation(conversation)
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT value FROM conversation_allowances WHERE conversation=? ORDER BY rowid", (conversation,))]

    def revoke(self, conversation, identity):
        self.store.conversation(conversation)
        with self.store.transaction():
            if self.db.execute("DELETE FROM conversation_allowances WHERE id=? AND conversation=?", (identity, conversation)).rowcount == 0:
                raise ContractError("target_gone", t("This allowance is no longer in this conversation"))

    def clear(self, conversation):
        """A stricter permission, or deleting the conversation, ends its "allow for this chat" grants."""
        with self.store.transaction():
            self.db.execute("DELETE FROM conversation_allowances WHERE conversation=?", (conversation,))
