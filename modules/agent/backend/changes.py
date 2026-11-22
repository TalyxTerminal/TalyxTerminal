"""The conversation's change cards. Module owners keep their own restoration data."""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .store import encode


class Changes:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.db = self.store.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS turn_changes (
                conversation TEXT NOT NULL, turn_id TEXT NOT NULL, call_id TEXT NOT NULL,
                owner TEXT NOT NULL, object_id TEXT, item TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0,
                changed REAL);
            CREATE INDEX IF NOT EXISTS turn_changes_owner ON turn_changes(conversation,owner,object_id);
            CREATE TABLE IF NOT EXISTS file_changes (
                call_id TEXT PRIMARY KEY, path TEXT NOT NULL, before BLOB, after BLOB);
            CREATE TABLE IF NOT EXISTS file_images (digest TEXT PRIMARY KEY, content BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS workspace_files (
                workspace TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL, modified INTEGER NOT NULL,
                digest TEXT NOT NULL, PRIMARY KEY(workspace,path));
            CREATE INDEX IF NOT EXISTS workspace_files_digest ON workspace_files(digest);
        """)

    @staticmethod
    def owner(action):
        if action in {"delete", "edit_file", "write_file"}:
            return "files"
        return "bots" if action.startswith("bot_") else action.partition(".")[0]

    def created(self, conversation, action, target):
        """Whether this chat, or any line of this bot, made the target and has not deleted it since."""
        if not isinstance(target, str):
            return False
        bot = conversation.get("botId")
        lines = ("SELECT id FROM conversations WHERE workspace=? AND json_extract(value,'$.botId')=?",
                 (conversation["workspaceId"], bot)) if bot else ("SELECT ?", (conversation["id"],))
        row = self.db.execute("SELECT json_extract(item,'$.kind') FROM turn_changes WHERE conversation IN (" + lines[0] + ") "
                              "AND owner=? AND object_id=? AND undone=0 AND json_extract(item,'$.kind') "
                              "IN ('created','deleted') ORDER BY rowid DESC LIMIT 1",
                              (*lines[1], self.owner(action), target)).fetchone()
        return row is not None and row[0] == "created"

    def add(self, turn, owner, item):
        self.db.execute("INSERT INTO turn_changes(conversation,turn_id,call_id,owner,object_id,item,changed) VALUES(?,?,?,?,?,?,?)",
                        (turn["conversationId"], turn["id"], item["callId"], owner, item["objectId"], encode(item), time.time()))

    async def collect(self, turn, action, call_id):
        receipt = await self.service.ai_call(turn, "platform.changes.list", {"callIds": [call_id]})
        items = receipt["result"]
        with self.store.transaction():
            for item in items:
                self.add(turn, self.owner(action), {**item, "reversible": True})
            if not items:
                self.add(turn, self.owner(action), {"callId": call_id, "objectId": None, "label": action,
                                                    "openTarget": None, "kind": "updated", "reversible": False})

    def keep(self, content):
        """A file's content, kept once by its digest."""
        if content is None:
            return None
        digest = hashlib.sha256(content).hexdigest()
        self.db.execute("INSERT OR IGNORE INTO file_images VALUES (?,?)", (digest, content))
        return digest

    def record_file(self, turn, call_id, virtual_path, path, before, after):
        with self.store.transaction():
            self._record_file(turn, call_id, virtual_path, path, self.keep(before), self.keep(after))

    def _record_file(self, turn, call_id, virtual_path, path, before, after):
        """One file a call created, changed or deleted, by the digests of its content before and after."""
        call_id = uuid5(NAMESPACE_URL, turn["id"] + ":" + call_id).hex
        self.db.execute("INSERT INTO file_changes VALUES (?,?,?,?)", (call_id, str(path), before, after))
        self.add(turn, "files", {"callId": call_id, "objectId": virtual_path, "label": path.name,
                                 "kind": "created" if before is None else "deleted" if after is None else "updated",
                                 "openTarget": None, "reversible": True})

    async def scan(self, workspace, root):
        """The workspace folder's files now, by path. A file whose size and modification time are as the last scan
        found them keeps that digest; any other is read and its content kept, so what a run changes can be undone."""
        known = {row[0]: tuple(row[1:]) for row in self.db.execute(
            "SELECT path,size,modified,digest FROM workspace_files WHERE workspace=?", (workspace,))}

        def walk():
            found, contents = {}, []
            for path in root.rglob("*"):
                try:
                    if not path.is_file():
                        continue
                    stat = path.stat()
                    name = path.relative_to(root).as_posix()
                    entry = known.get(name)
                    if entry is None or entry[:2] != (stat.st_size, stat.st_mtime_ns):
                        content = path.read_bytes()
                        contents.append(content)
                        entry = (stat.st_size, stat.st_mtime_ns, hashlib.sha256(content).hexdigest())
                except (FileNotFoundError, PermissionError):
                    continue  # removed meanwhile, or held open by another program: not followed this time
                found[name] = entry
            return found, contents
        found, contents = await asyncio.to_thread(walk)
        with self.store.transaction():
            for content in contents:
                self.keep(content)
            self.db.execute("DELETE FROM workspace_files WHERE workspace=?", (workspace,))
            self.db.executemany("INSERT INTO workspace_files VALUES (?,?,?,?,?)",
                                [(workspace, name, *entry) for name, entry in found.items()])
        return {name: entry[2] for name, entry in found.items()}

    def forget_images(self):
        """Drop the contents that no change and no last scan of a workspace folder refers to."""
        self.db.execute("DELETE FROM file_images WHERE NOT EXISTS (SELECT 1 FROM file_changes WHERE before=digest OR after=digest) "
                        "AND NOT EXISTS (SELECT 1 FROM workspace_files w WHERE w.digest=file_images.digest)")

    def record_run(self, turn, call_id, root, before, after):
        """The files a code run created, changed or deleted in the workspace folder, one change each."""
        with self.store.transaction():
            for name in sorted(before.keys() | after.keys()):
                if before.get(name) != after.get(name):
                    self._record_file(turn, call_id + ":" + name, "/workspace/" + name, root / name,
                                      before.get(name), after.get(name))

    async def collect_run(self, turn, run_id):
        receipt = await self.service.ai_call(turn, "platform.changes.list", {"runId": run_id})
        with self.store.transaction():
            for item in receipt["result"]:
                self.add(turn, item["openTarget"]["module"] if item["openTarget"] else "code", {**item, "reversible": True})

    def card(self, turn):
        # A memory a bot remembered has its own "Remembered" line; only its run record lists it with the changes.
        rows = self.db.execute("SELECT owner,item,undone FROM turn_changes WHERE turn_id=? AND owner!='memory' ORDER BY rowid",
                               (turn["id"],)).fetchall()
        if rows:
            reversible = [row for row in rows if json.loads(row[1])["reversible"]]
            # One line per thing changed: a file written twice in the turn is one change, created if the turn made it.
            items, kinds = {}, {}
            for owner, raw, _ in rows:
                item = json.loads(raw)
                key = (owner, item["objectId"]) if item["objectId"] is not None else item["callId"]
                first, last = kinds.get(key, (item["kind"], item["kind"]))[0], item["kind"]
                kinds[key] = (first, last)
                items[key] = {**item, "kind": first if first == "created" else last if last == "deleted" else "updated",
                              "reversible": items[key]["reversible"] and item["reversible"] if key in items else item["reversible"]}
            shown = [item for key, item in items.items() if kinds[key] != ("created", "deleted")]
            self.service.runtime_card(turn, {"id": "changes_" + turn["id"], "type": "changes", "items": shown,
                                             "undone": bool(reversible) and all(row[2] for row in reversible)})

    async def revert(self, workspace, conversation, turn_id, direction, *, since=0):
        self.store.conversation(conversation, workspace)
        turn = self.store.turn(turn_id)
        if turn["conversationId"] != conversation or direction not in {"undo", "redo"}:
            raise ContractError("invalid_input", t("Choose changes from this conversation and undo or redo"))
        undo = direction == "undo"
        rows = self.db.execute("SELECT DISTINCT call_id,owner FROM turn_changes WHERE turn_id=? AND undone=? "
                               "AND changed>=? AND json_extract(item,'$.reversible')=1 ORDER BY rowid", (turn_id, int(not undo), since)).fetchall()
        for call_id, owner in reversed(rows) if undo else rows:
            if owner == "files":
                path, before, after = self.db.execute("SELECT path,before,after FROM file_changes WHERE call_id=?",
                                                      (call_id,)).fetchone()
                contents = before if undo else after
                if isinstance(contents, str):
                    contents = self.db.execute("SELECT content FROM file_images WHERE digest=?", (contents,)).fetchone()[0]
                if contents is None:
                    await asyncio.to_thread(Path(path).unlink, missing_ok=True)
                else:
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(Path(path).write_bytes, contents)
            elif owner == "bots":
                self.service.bots.restore(workspace, call_id, direction)
            elif owner == "memory":
                card = json.loads(self.db.execute("SELECT value FROM cards WHERE id=?", (call_id,)).fetchone()[0])
                self.service.knowledge.memory_decision(turn, card, redo=not undo)
            else:
                await self.service.ai_call(turn, "platform.changes.revert", {"callId": call_id, "direction": direction})
            with self.store.transaction():
                self.db.execute("UPDATE turn_changes SET undone=? WHERE turn_id=? AND call_id=?", (undo, turn_id, call_id))
        self.card(turn)
