"""Quant facts share the business owner's SQLite transaction and source cursor."""

import asyncio
import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import uuid4

from talyx_sdk import ContractError
from talyx_sdk.i18n import t


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stamp(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()


class QuantTriggers:
    def __init__(self, db):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS quant_trigger_epochs(source TEXT PRIMARY KEY,epoch TEXT NOT NULL)")
        for source in ("research", "library"):
            db.execute("INSERT OR IGNORE INTO quant_trigger_epochs VALUES (?,?)", (source, uuid4().hex))
        self.epochs = {row[0]: row[1] for row in db.execute("SELECT source,epoch FROM quant_trigger_epochs")}
        db.execute("""CREATE TABLE IF NOT EXISTS quant_trigger_facts (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, workspace TEXT NOT NULL,
            account TEXT, event_id TEXT NOT NULL, value TEXT, UNIQUE(source,workspace,event_id))""")
        db.execute("CREATE INDEX IF NOT EXISTS quant_trigger_scope ON quant_trigger_facts(source,workspace,seq)")
        db.commit()

    def append(self, db, *, source, workspace, event_id, kind, object_id, object_kind, payload, observed_at,
               occurred_at=None, correlation=None):
        if workspace is None:
            return None
        item = {"eventId": event_id, "workspaceId": workspace, "type": "quant." + kind,
                "occurredAt": stamp(observed_at if occurred_at is None else occurred_at), "observedAt": stamp(observed_at),
                "object": {"module": "quant", "kind": object_kind, "id": object_id}, "payload": payload}
        if correlation is not None:
            item["correlationId"] = str(correlation)
        inserted = db.execute("INSERT OR IGNORE INTO quant_trigger_facts(source,workspace,account,event_id,value) VALUES (?,?,?,?,?)",
                              (source, workspace, None, event_id, encoded(item)))
        return item if inserted.rowcount else None

    def record(self, db, kind, record, previous):
        facts = []
        if record.get("workspaceId") is None:
            return facts
        if kind == "qrun" and record["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            if previous is not None and previous.get("state") == record["state"]:
                return facts
            item = self.append(db, source="research", workspace=record.get("workspaceId"),
                event_id=f"research:{record['id']}:{record['state']}", kind="research_completed", object_id=record["id"],
                object_kind="run", payload={"runId": record["id"], "state": record["state"],
                    "objectId": record["objectId"]},
                observed_at=record["updatedAt"])
            if item is not None:
                facts.append(item)
        if kind == "qlibevent":
            item = self.append(db, source="library", workspace=record["workspaceId"], event_id=record["id"], kind=record["type"], object_id=record["itemId"],
                object_kind="library", payload={key: record[key] for key in ("itemId", "versionId", "name", "version", "message")}, observed_at=record["updatedAt"])
            if item is not None:
                facts.append(item)
        return facts

    def token(self, source, workspace, sequence):
        return base64.urlsafe_b64encode(encoded([source, self.epochs[source], workspace, sequence]).encode()).decode().rstrip("=")

    def sequence(self, source, workspace, token):
        try:
            owner, epoch, selected, sequence = json.loads(base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True))
        except (ValueError, UnicodeError, binascii.Error) as error:
            raise ContractError("invalid_input", t("Invalid Quant source cursor")) from error
        if owner != source or selected != workspace or type(sequence) is not int or sequence < 0:
            raise ContractError("invalid_input", t("Quant cursor belongs to another source or workspace"))
        return sequence if epoch == self.epochs[source] else None

    def scope(self, source, scope):
        if scope["source"] != "quant:" + source:
            raise ContractError("permission_denied", t("Quant source scope mismatch"))
        return scope["workspaceId"]

    def head(self, source, scope):
        workspace = self.scope(source, scope)
        sequence = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM quant_trigger_facts WHERE source=? AND workspace=?",
                                   (source, workspace)).fetchone()[0]
        return {"cursor": self.token(source, workspace, sequence)}

    def read(self, source, args):
        workspace = self.scope(source, args["scope"])
        current = self.head(source, args["scope"])["cursor"]
        lower, upper, head = [self.sequence(source, workspace, token) for token in (args["after"], args["through"], current)]
        empty = {"items": [], "nextCursor": args["after"], "watermark": args["through"], "gap": True}
        if lower is None or upper is None or lower > upper or upper > head:
            return empty
        rows = self.db.execute("SELECT seq,account,value FROM quant_trigger_facts WHERE source=? AND workspace=? AND seq>? AND seq<=? ORDER BY seq",
                               (source, workspace, lower, upper))
        items, cursor = [], lower
        for row in rows:
            cursor = row["seq"]
            if row["value"] is None:
                return empty
            items.append(json.loads(row["value"]))
            if len(items) == args["limit"]:
                break
        else:
            cursor = upper
        return {"items": items, "nextCursor": self.token(source, workspace, cursor), "watermark": args["through"], "gap": False}


class TriggerFeed:
    """Quant's trigger streams (research and library facts of a workspace): a consumer reads from its cursor, then
    hears each new fact as it is committed; one that was away misses nothing. Each fact is also published as an event."""

    def __init__(self, ctx, records):
        self.ctx, self.records = ctx, records
        self.changed = asyncio.Event()
        records.on_trigger = self.committed

    def committed(self, fact):
        self.changed.set()
        self.changed = asyncio.Event()
        try:
            self.ctx.publish(fact["type"].removeprefix("quant."), fact["payload"])
        except ContractError as error:
            # The fact is already committed with the business record; cursor readers resume from it.
            if error.code != "provider_restarted":
                raise

    async def follow(self, source, workspace, cursor):
        """(fact, cursor) after ``cursor`` (from now when None), then each new one."""
        triggers = self.records.triggers
        scope = {"source": "quant:" + source, "workspaceId": workspace}
        if cursor is None:
            cursor = triggers.head(source, scope)["cursor"]
        while True:
            waiting = self.changed
            through = triggers.head(source, scope)["cursor"]
            while cursor != through:
                page = triggers.read(source, {"scope": scope, "after": cursor, "through": through, "limit": 1})
                if page["gap"]:
                    cursor = through     # the facts before Quant's records were reset are gone: go on from now
                    break
                cursor = page["nextCursor"]
                for item in page["items"]:
                    yield item, cursor
            await waiting.wait()
