"""A notebook as the panel sees it, and as the .ipynb file holds it.

Quant parses and writes the notebook; Developer only keeps the file as a text document, so a save goes through
`developer.documents.edit` with the version the panel read, and VS Code opens the same file.
"""
from __future__ import annotations

import json
from uuid import uuid4

from talyx_sdk import ContractError

from .materials import plain

OUTPUT_LIMIT = 2 * 1024 * 1024
TOO_LARGE = "Output too large, not saved. Run the cell again to see it."
KERNELSPEC = {"display_name": "Python 3 (TALYX research)", "language": "python", "name": "python3"}
CELL_TYPES = {"code", "markdown", "raw"}
# nbformat stores these as lists of lines; every other value is kept as it is.
LINE_MIMES = ("application/javascript", "image/svg+xml")


def new_cell(kind="code", source=""):
    return {"id": uuid4().hex[:12], "type": kind, "source": source, "outputs": [], "executionCount": None, "metadata": {}}


def joined(value):
    return "".join(value) if isinstance(value, list) else value


def source_text(value):
    return joined(value).replace('\r\n', '\n').replace('\r', '\n')


def read_output(output):
    """An output with its multi-line strings joined; text and display data keep their nbformat shape."""
    kind = output["output_type"]
    if kind == "stream":
        return {**output, "text": joined(output["text"])}
    if kind in {"display_data", "execute_result"}:
        data = {mime: value if mime == "application/json" or mime.endswith("+json") else joined(value) for mime, value in output["data"].items()}
        return {**output, "data": data, "metadata": output["metadata"] if "metadata" in output else {}}
    return output


def parse(text):
    """The panel's model of an .ipynb file: cells with source, outputs and execution count, and the notebook's own metadata."""
    try:
        raw = json.loads(text)
        if raw["nbformat"] != 4:
            raise ContractError("unavailable", f"This notebook is nbformat {raw['nbformat']}; only nbformat 4 is supported.")
        cells = []
        used = {cell["id"] for cell in raw["cells"] if "id" in cell}
        for index, item in enumerate(raw["cells"]):
            kind = item["cell_type"]
            if kind not in CELL_TYPES:
                raise ContractError("invalid_input", f"A cell has the unknown type {kind}.")
            cell_id = item.get("id")
            if cell_id is None:
                cell_id = f"cell-{index}"
                while cell_id in used:
                    cell_id += "-"
                used.add(cell_id)
            cell = {"id": cell_id, "type": kind, "source": source_text(item["source"]),
                    "outputs": [], "executionCount": None, "metadata": item["metadata"] if "metadata" in item else {}}
            if kind == "code":
                cell["outputs"] = [read_output(output) for output in item["outputs"]] if "outputs" in item else []
                cell["executionCount"] = item["execution_count"] if "execution_count" in item else None
            if "attachments" in item:
                cell["attachments"] = item["attachments"]
            cells.append(cell)
        metadata = {key: value for key, value in (raw["metadata"] if "metadata" in raw else {}).items() if key != "kernelspec"}
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ContractError("invalid_input", "This file is not a notebook Quant can read.", {"reason": str(error)}) from error
    return {"cells": cells, "metadata": metadata}


def split(text):
    return text.splitlines(True) if isinstance(text, str) else text


def write_output(output):
    """The output as nbformat stores it; one over 2 MB is replaced by a line saying so."""
    if len(json.dumps(output, ensure_ascii=False, allow_nan=False).encode("utf-8")) > OUTPUT_LIMIT:
        return {"output_type": "stream", "name": "stderr", "text": split(TOO_LARGE)}
    kind = output["output_type"]
    if kind == "stream":
        return {**output, "text": split(output["text"])}
    if kind in {"display_data", "execute_result"}:
        data = {mime: split(value) if mime.startswith("text/") or mime in LINE_MIMES else value for mime, value in output["data"].items()}
        return {**output, "data": data}
    return output


def check_model(model):
    if not isinstance(model, dict) or not isinstance(model.get("cells"), list):
        raise ContractError("invalid_input", "A notebook needs a list of cells.")
    seen = set()
    for cell in model["cells"]:
        if (not isinstance(cell, dict) or cell.get("type") not in CELL_TYPES or not isinstance(cell.get("id"), str) or not cell["id"]
                or not isinstance(cell.get("source"), str) or not isinstance(cell.get("outputs"), list)):
            raise ContractError("invalid_input", "Each cell needs an id, a type (code, markdown or raw), its source text and a list of outputs.")
        if cell["id"] in seen:
            raise ContractError("invalid_input", "Cell ids must be unique.", {"id": cell["id"]})
        seen.add(cell["id"])
        if cell["type"] != "code" and cell["outputs"]:
            raise ContractError("invalid_input", "Only code cells can have outputs.")
        if "attachments" in cell and (cell["type"] == "code" or not isinstance(cell["attachments"], dict)):
            raise ContractError("invalid_input", "Only Markdown and raw cells can contain an attachment bundle.")
        for output in cell["outputs"]:
            if not isinstance(output, dict) or output.get("output_type") not in {"stream", "display_data", "execute_result", "error"}:
                raise ContractError("invalid_input", "An output needs an output_type: stream, display_data, execute_result or error.")
            kind = output["output_type"]
            if kind == "stream" and (output.get("name") not in ("stdout", "stderr") or not isinstance(output.get("text"), str)):
                raise ContractError("invalid_input", "Stream output needs stdout or stderr and its text.")
            if kind in {"display_data", "execute_result"} and not isinstance(output.get("data"), dict):
                raise ContractError("invalid_input", "Display output needs its MIME data bundle.")
            if kind == "error" and not (isinstance(output.get("ename"), str) and isinstance(output.get("evalue"), str)
                                         and isinstance(output.get("traceback"), list)):
                raise ContractError("invalid_input", "Error output needs ename, evalue and traceback.")


def serialize(model):
    """The .ipynb text of the panel's model."""
    check_model(model)
    cells = []
    for cell in model["cells"]:
        item = {"cell_type": cell["type"], "id": cell["id"], "metadata": cell.get("metadata") or {}, "source": split(source_text(cell["source"]))}
        if cell["type"] == "code":
            item.update(execution_count=cell.get("executionCount"), outputs=[write_output(output) for output in cell["outputs"]])
        if "attachments" in cell and cell["type"] in {"markdown", "raw"}:
            item["attachments"] = cell["attachments"]
        cells.append(item)
    metadata = {**(model.get("metadata") or {}), "kernelspec": KERNELSPEC}
    return json.dumps({"cells": cells, "metadata": metadata, "nbformat": 4, "nbformat_minor": 5}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


def code_of(model, cell_id):
    cell = next((item for item in model["cells"] if item["id"] == cell_id), None)
    if cell is None or cell["type"] != "code":
        raise ContractError("not_found", "This notebook has no code cell with that id.", {"cellId": cell_id})
    return cell["source"]


class NotebookFiles:
    """The notebook object's .ipynb, read and written the way every Developer file is."""

    def __init__(self, ctx, store):
        self.ctx, self.store = ctx, store

    def path(self, object_id):
        record = self.store.read(object_id)
        if record["kind"] != "notebook":
            raise ContractError("invalid_input", "This Quant object is not a notebook.")
        return self.store.source_path(record), record

    async def document(self, path):
        opened = plain(await self.ctx.developer("developer.documents.open", {"source": {"kind": "file", "uri": path.as_uri()}}))
        if opened is None:
            # No Developer: the file on disk is the document; its version is its content's digest.
            import hashlib
            return {"documentId": None, "dirty": False, "conflict": False,
                    "version": hashlib.sha256(path.read_bytes()).hexdigest()[:16]}
        if opened["conflict"]:
            raise ContractError("conflict", f"{path.name} changed on disk while it has unsaved edits. Resolve it in Developer first.")
        return opened

    async def saved(self, path):
        """The document with its edits saved, so the file on disk is what Developer holds."""
        opened = await self.document(path)
        if opened["dirty"]:
            saved = plain(await self.ctx.call("developer.documents.save", {"documentId": opened["documentId"], "expectedVersion": opened["version"]}))
            if saved["status"] not in {"saved", "clean"}:
                raise ContractError("conflict", f"{path.name} could not be saved before it was read: {saved['message'] or saved['status']}.")
            opened = await self.document(path)
        return opened

    async def read(self, object_id):
        path, record = self.path(object_id)
        opened = await self.saved(path)
        return {"objectId": object_id, "name": record["name"], "version": opened["version"], "documentId": opened["documentId"],
                "notebook": parse(path.read_text(encoding="utf-8"))}

    async def write(self, object_id, model, expected_version):
        """Replace the whole file with the panel's model, if nothing changed it since the panel read it."""
        path, _ = self.path(object_id)
        content = serialize(model)
        opened = await self.document(path)
        if opened["version"] != expected_version:
            raise ContractError("conflict", "The notebook changed since it was read. Reload it before saving.", {"version": opened["version"]})
        if opened["documentId"] is None:   # no Developer: written directly
            path.write_text(content, encoding="utf-8")
            return {"objectId": object_id, "version": (await self.document(path))["version"], "documentId": None}
        edited = plain(await self.ctx.call("developer.documents.edit", {"documentId": opened["documentId"], "expectedVersion": opened["version"], "content": content}))
        saved = plain(await self.ctx.call("developer.documents.save", {"documentId": edited["documentId"], "expectedVersion": edited["version"]}))
        if saved["status"] not in {"saved", "clean"}:
            raise ContractError("conflict", f"{path.name} could not be saved: {saved['message'] or saved['status']}.")
        return {"objectId": object_id, "version": saved["version"], "documentId": edited["documentId"]}

    async def cell_code(self, object_id, cell_id):
        """A saved cell's code, for the run an AI asks for."""
        return code_of((await self.read(object_id))["notebook"], cell_id)

    async def save_execution(self, object_id, cell_id, code, result):
        read = await self.read(object_id)
        for cell in read["notebook"]["cells"]:
            if cell["id"] == cell_id and cell["type"] == "code" and cell["source"] == code:
                cell.update(outputs=result["outputs"], executionCount=result["executionCount"])
                await self.write(object_id, read["notebook"], read["version"])
                return
        raise ContractError("conflict", "The executed cell was edited or removed before its output could be saved.", {"cellId": cell_id})
