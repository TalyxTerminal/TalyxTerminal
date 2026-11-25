"""What the document actions do beyond the service: paging, checkpoints, and Developer's own document routes."""
from __future__ import annotations

import base64
import copy
import json
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .checkpoints import digest
from .service import origin

# An answer stays under the platform's limit for what one call returns to AI.
PAGE_BYTES = 18000


def receipt(snapshot):
    return {key: snapshot[key] for key in ("documentId", "uri", "name", "version", "dirty", "conflict")}


def page(items, query, cursor=None, limit=50):
    """One page of ``items`` and the cursor of the next; a cursor only reads on while the items stay the same."""
    identity = digest([query, items])
    offset = 0
    if cursor:
        try:
            value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if value["identity"] != identity:
                raise ContractError("conflict", t("The page contents or query changed; read again without a cursor."))
            offset = value["offset"]
            if type(offset) is not int or offset < 0 or offset > len(items):
                raise ValueError("Invalid offset")
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ContractError("invalid_input", t("Invalid page cursor")) from exc
    selected = []
    for item in items[offset:offset + limit]:
        if len(json.dumps([*selected, item], ensure_ascii=False).encode()) > PAGE_BYTES - 1000:
            if not selected:
                raise ContractError("too_large", t("One entry exceeds the response budget."))
            break
        selected.append(item)
    end = offset + len(selected)
    token = base64.urlsafe_b64encode(json.dumps({"identity": identity, "offset": end}).encode()).decode()
    return selected, token if end < len(items) else None


def read_chars(snapshot, offset, limit=PAGE_BYTES, *, fields=("content", "baseContent")):
    """A character page of both versions at the same offset, including documents with one very long line."""
    total = max(len(snapshot[key] or "") for key in fields)
    length = min(max(total - offset, 0), limit)
    while True:
        end = offset + length
        part = {**snapshot, **{key: snapshot[key][offset:end] if snapshot[key] is not None else None for key in fields},
                "startChar":offset, "nextChar":end if end < total else None}
        if len(json.dumps(part, ensure_ascii=False).encode()) <= PAGE_BYTES:
            return part
        if length <= 1:
            raise ContractError("too_large", t("The document metadata exceeds the response budget."))
        length //= 2


def whole_document(snapshot):
    return {**snapshot, "baseContent": None} if snapshot["content"] == snapshot["baseContent"] else snapshot


def read_page(snapshot, options):
    """The whole document when asked for in full; otherwise a page of whole lines from startLine or the cursor.
    Without unsaved edits the saved text is the same and is not sent twice (baseContent null): two copies of a large
    file would not fit in one answer."""
    if options.full:
        return whole_document(snapshot)
    start, char = options.startLine or 1, options.startChar
    if options.cursor is not None:
        line, _, offset = options.cursor.partition(":")
        start, char = int(line), int(offset) if offset else None
    return read_lines(snapshot, start, options.lineCount or 2000, char)


def read_lines(snapshot, start, count, char=None):
    """``count`` lines of the buffer from line ``start`` (1-based), within the page budget. Without unsaved edits the
    saved text is the same and is not repeated (baseContent null). A line longer than a page is read in parts."""
    same = snapshot["content"] == snapshot["baseContent"]
    lines = snapshot["content"].splitlines(keepends=True)
    base = [] if same else snapshot["baseContent"].splitlines(keepends=True)
    total = max(len(lines), len(base))
    snapshot = {**snapshot, "content": "", "baseContent": None if same else "", "startLine": start,
                "totalLines": len(lines), "nextLine": None, "nextCursor": None}
    for index in range(start - 1, min(total, start - 1 + count)):
        following = index + 2 if index + 1 < total else None
        candidate = {**snapshot, "content": snapshot["content"] + (lines[index] if index < len(lines) else ""),
                     "nextLine": following, "nextCursor": None if following is None else str(following)}
        if not same:
            candidate["baseContent"] = snapshot["baseContent"] + (base[index] if index < len(base) else "")
        fits = len(json.dumps(candidate, ensure_ascii=False).encode()) <= PAGE_BYTES
        if index == start - 1 and (char is not None or not fits):
            part = read_chars(candidate, char or 0)
            if part["nextChar"] is not None:
                part["nextCursor"] = f"{start}:{part['nextChar']}"
            return part
        if not fits:
            break
        snapshot = candidate
    return snapshot


async def create_checkpoint(documents, value):
    request = sorted(value["documents"], key=lambda item: item["documentId"])
    if len({item["documentId"] for item in request}) != len(request):
        raise ContractError("invalid_input", t("Capture each document only once."))
    previous = documents.checkpoints.submission(value["submissionId"], request)
    if previous:
        return previous
    async with documents.lock:
        captured = []
        for selection in request:
            record = documents.record(selection["documentId"])
            documents.check_version(record, selection["expectedVersion"])
            captured.append({"capturedDocumentId": record["documentId"], **{key: record[key] for key in (
                "source", "name", "content", "version", "baseContent")}, "sourceVersion": record["baseVersion"]})
        return documents.checkpoints.create(value["submissionId"], request, captured)


async def restore_checkpoint(documents, value, call):
    fixed = documents.checkpoints.read(value["checkpoint"], value.get("sourceKeys"))
    resolutions = {item["sourceKey"]: item for item in value.get("resolutions", [])}
    results = []
    for saved in fixed["documents"]:
        result = {"sourceKey": saved["sourceKey"], "name": saved["name"], "documentId": None, "version": None,
                  "sourceVersion": None, "status": "unavailable", "message": None, "preservedCheckpoint": None}
        try:
            document_id = (await documents.open({"source": saved["source"]}, call))["documentId"]
            result["documentId"] = document_id
            async with documents.lock:
                record = copy.deepcopy(documents.record(document_id))
                result.update(version=record["version"], sourceVersion=record["baseVersion"])
                resolution = resolutions.get(saved["sourceKey"])
                matches = record["baseVersion"] == saved["sourceVersion"]
                accepted = (record["version"] == resolution["expectedVersion"] and record["baseVersion"] == resolution["expectedSourceVersion"]) \
                    if resolution else matches and not record["conflict"] and (
                        record["documentId"] == saved["capturedDocumentId"] and record["version"] == saved["version"]
                        or record["content"] == record["baseContent"] == saved["baseContent"])
                if record["readOnly"]:
                    result.update(status="read_only", message=t("This source is read-only."))
                elif record["content"] == saved["content"] and not record["conflict"] and record["externalState"] == "present":
                    result["status"] = "unchanged"
                elif not accepted:
                    result.update(status="conflict", message=t("Current input and the saved version are both kept. Compare before replacing."))
                else:
                    if resolution:
                        preserved = {"capturedDocumentId": document_id, "source": record["source"], "name": record["name"],
                                     "content": record["content"], "version": record["version"], "baseContent": record["baseContent"],
                                     "sourceVersion": record["baseVersion"]}
                        result["preservedCheckpoint"] = documents.checkpoints.create("restore:" + uuid4().hex, [], [preserved])["checkpoint"]
                    restored = documents.set_content(record, saved["content"], origin(call))
                    result.update(status="restored", version=restored["version"])
        except ContractError as exc:
            result.update(status="conflict" if exc.code == "conflict" else "unavailable", message=exc.message)
        results.append(result)
    return {"checkpoint": value["checkpoint"], "documents": results}


class Resolution(BaseModel):
    mode: str
    expectedVersion: int
    expectedSourceVersion: str | int | None


def router(documents):
    """Recovery and external-change review for Developer's own panel."""
    routes = APIRouter(prefix="/documents")

    @routes.get("/unsaved")
    async def unsaved():
        return {"documents": documents.unsaved()}

    @routes.get("/{document_id}/text")
    async def editor_text(document_id: str):
        # The editor accepts 16 MiB of source. JSON escaping, metadata or a dirty baseline can exceed
        # the action transport budget even for a supported file. HTTP carries the snapshot in one read.
        return whole_document(documents.snapshot(documents.record(document_id)))

    @routes.get("/{document_id}/versions")
    async def versions(document_id: str):
        return await documents.versions(document_id)

    @routes.post("/{document_id}/resolve")
    async def resolve(document_id: str, value: Resolution):
        if value.mode not in {"mine", "external"}:
            raise ContractError("invalid_input", t("Choose to keep your edits or use the external version."))
        return await documents.resolve(document_id, value.mode, value.expectedVersion, value.expectedSourceVersion)

    @routes.get("/checkpoints/{checkpoint_id}")
    async def checkpoint(checkpoint_id: str, digest: str):
        return documents.checkpoints.read({"checkpointId": checkpoint_id, "digest": digest})

    return routes
