"""Continue a chat from one of its saved message boundaries."""

import asyncio
import json
import shutil
from uuid import uuid4

from talyx_sdk import ContractError

from . import checkpoints
from .store import encode


class History:
    def __init__(self, service):
        self.service, self.store, self.db = service, service.store, service.store.db
        self.db.execute("CREATE TABLE IF NOT EXISTS graph_branches "
                        "(thread TEXT PRIMARY KEY, conversation TEXT NOT NULL)")

    def thread(self, conversation):
        row = self.db.execute("SELECT thread FROM graph_branches WHERE conversation=? ORDER BY rowid DESC LIMIT 1",
                              (conversation,)).fetchone()
        return row[0] if row else conversation

    async def checkpoint(self, conversation):
        point = await self.service.engine.checkpointer.aget_tuple(checkpoints.config(self.thread(conversation)))
        return point.checkpoint["id"] if point else None

    def boundary(self, cid, workspace, item, role):
        conversation = self.store.conversation(cid, workspace)
        if conversation.get("kind", "chat") != "chat" or self.store.active(cid):
            raise ContractError("conflict", "Choose a finished chat before changing its history")
        row = self.db.execute("SELECT turn_id,value FROM cards WHERE id=? AND conversation=?", (item, cid)).fetchone()
        if row is None:
            raise ContractError("not_found", "This message is unavailable")
        card = json.loads(row["value"])
        if card.get("role") != role or "checkpointId" not in card:
            raise ContractError("invalid_input", "This message has no saved boundary for this operation")
        event = self.db.execute("SELECT MIN(seq) FROM events WHERE conversation=? AND json_extract(value,'$.type')='card' "
                                "AND json_extract(value,'$.payload.id')=?", (cid, item)).fetchone()[0]
        return conversation, row["turn_id"], card, event

    def cards_at(self, cid, seq):
        rows = self.db.execute("SELECT value FROM events WHERE conversation=? AND seq<=? "
                               "AND json_extract(value,'$.type')='card' ORDER BY seq", (cid, seq))
        cards = {}
        for row in rows:
            event = json.loads(row[0])
            card = event["payload"]
            cards[card["id"]] = (event["turnId"] or "", card)
        return cards

    def copy_answer(self, saved, conversation, turn):
        refs = []
        for source in saved.get("references", []):
            ref = {**source, "id": uuid4().hex}
            self.db.execute("INSERT INTO references_data VALUES (?,?,?,?)", (ref["id"], conversation, turn, encode(ref)))
            saved["text"] = saved["text"].replace("[[ref:" + source["id"] + "]]", "[[ref:" + ref["id"] + "]]")
            refs.append(ref)
        saved["references"] = refs

    def copy_child(self, saved, source_id, conversation, turn):
        source = self.service.children.read(source_id)
        child_id = saved["id"]
        cursor = 0
        for row in self.db.execute("SELECT card_id,value FROM child_details WHERE child=? AND seq<=? ORDER BY seq",
                                   (source_id, saved["detailCursor"])).fetchall():
            detail = {**json.loads(row["value"]), "childRunId": child_id}
            cursor = self.db.execute("INSERT INTO child_details(child,card_id,value) VALUES (?,?,?)",
                                     (child_id, row["card_id"], encode(detail))).lastrowid
        saved.update(conversationId=conversation, turnId=turn, detailCursor=cursor)
        child = {**source, **saved}
        self.db.execute("INSERT INTO child_runs VALUES (?,?,?,?)", (child_id, conversation, turn, encode(child)))

    async def fork(self, cid, workspace, item):
        original, _, card, seq = self.boundary(cid, workspace, item, "assistant")
        dest = uuid4().hex
        saver = self.service.engine.checkpointer
        await checkpoints.copy_thread(saver, self.thread(cid), dest, card["checkpointId"])
        material_files = self.service.root / "attachments"
        cards = self.cards_at(cid, seq)
        attachments = {old: uuid4().hex for _, saved in cards.values() for old in saved.get("attachments", [])}
        private_files = self.service.root / "files"
        try:
            for old, copied in attachments.items():
                await asyncio.to_thread(shutil.copyfile, material_files / old, material_files / copied)
            if (private_files / cid).is_dir():
                await asyncio.to_thread(shutil.copytree, private_files / cid, private_files / dest)
            with self.store.transaction():
                new = {**original, "id": dest, "title": (original["title"] + " (fork)")[:160],
                       "queued": [], "pinned": False, "archived": False}
                self.store.save_conversation(new)
                settings = self.store.runtime_settings(cid)
                self.db.execute("INSERT OR REPLACE INTO conversation_runtime VALUES (?,?)", (dest, encode(settings)))
                for old, copied in attachments.items():
                    metadata = self.service.attachment(old, cid)
                    self.db.execute("INSERT INTO attachments VALUES (?,?,?)", (copied, dest, encode({**metadata, "id":copied})))
                turns = {}
                for old_turn, saved in cards.values():
                    turn = None
                    if old_turn:
                        if old_turn not in turns:
                            turns[old_turn] = uuid4().hex
                            prior = self.store.turn(old_turn)
                            self.store.save_turn({**prior,"id":turns[old_turn],"conversationId":dest,
                                "attachments":[attachments[a] for a in prior["attachments"] if a in attachments],
                                "inbox":[],"interrupts":{},"state":"completed"})
                        turn = turns[old_turn]
                    source_id = saved["id"]
                    prefix = "model_" if source_id.startswith("model_") else ""
                    saved = {**saved, "id":prefix + uuid4().hex}
                    if saved["type"] == "answer":
                        self.copy_answer(saved, dest, turn)
                    if saved["type"] == "child_run":
                        self.copy_child(saved, source_id, dest, turn)
                    if "attachments" in saved:
                        saved["attachments"] = [attachments[a] for a in saved["attachments"]]
                        saved.pop("materials", None)
                    if saved["type"] == "changes":
                        saved["items"] = [{**item,"reversible":False} for item in saved["items"]]
                    if saved["type"] == "memory_saved":
                        saved["changeId"] = None
                    if saved["type"] == "approval":
                        saved.update(conversationId=dest, turnId=turn)
                    self.db.execute("INSERT INTO cards VALUES (?,?,?,?,?)", (saved["id"],dest,turn or "",saved["createdAt"],encode(saved)))
                    self.store.event(dest, turn, "card", saved)
                self.store.event(dest, None, "conversation", new)
            return self.store.describe(new)
        except BaseException:
            await saver.adelete_thread(dest)
            for copied in attachments.values():
                (material_files / copied).unlink(missing_ok=True)
            if (private_files / dest).is_dir():
                await asyncio.to_thread(shutil.rmtree, private_files / dest)
            raise

    async def rewrite(self, cid, workspace, item, text, undo):
        if not isinstance(text, str) or not text.strip() or len(text) > 100000 or type(undo) is not bool:
            raise ContractError("invalid_input", "Enter an edited message and choose whether to undo changes")
        original, _, card, seq = self.boundary(cid, workspace, item, "user")
        dest = uuid4().hex
        saver = self.service.engine.checkpointer
        if card["checkpointId"] is not None:
            await checkpoints.copy_thread(saver, self.thread(cid), dest, card["checkpointId"])
        try:
            if undo:
                for row in self.db.execute("SELECT turn_id FROM turn_changes WHERE conversation=? AND changed>=? "
                                           "AND EXISTS (SELECT 1 FROM cards WHERE cards.turn_id=turn_changes.turn_id) "
                                           "GROUP BY turn_id ORDER BY MAX(changed) DESC", (cid, card["createdAt"])).fetchall():
                    await self.service.changes.revert(workspace, cid, row[0], "undo", since=card["createdAt"])
            keep = self.cards_at(cid, seq - 1)
            with self.store.transaction():
                self.db.execute("INSERT INTO graph_branches VALUES (?,?)", (dest, cid))
                self.db.execute("DELETE FROM events WHERE conversation=? AND seq>=?", (cid, seq))
                self.db.execute("DELETE FROM cards WHERE conversation=?", (cid,))
                for key, (turn, value) in keep.items():
                    self.db.execute("INSERT INTO cards VALUES (?,?,?,?,?)",
                                    (key, cid, turn, value["createdAt"], encode(value)))
                self.store.event(cid, None, "rewind", {"messageId": item})
                turn = self.store.new_turn(cid, workspace, text, view=card["view"], attachments=card["attachments"])
                self.service.record_user(turn)
        except BaseException:
            await saver.adelete_thread(dest)
            raise
        self.service.schedule(cid)
        return self.store.describe(original)
