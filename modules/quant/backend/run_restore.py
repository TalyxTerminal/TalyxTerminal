"""Restore one run's project through Developer and report the files left as they are."""
import asyncio
from pathlib import Path

from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from .materials import plain
from .project_paths import source_identity


async def restore_files(ctx, obj, source, common, common_root, *, expected=None):
    root = Path(obj["directory"])
    old_common = {item["path"]: item["sha256"] for item in common["files"]} if common else {}
    now_common = await asyncio.to_thread(source_identity, common_root)
    report = {"extraFiles": [], "nonTextFiles": [],
              "commonDifferences": sorted(key for key in old_common.keys() | now_common.keys() if old_common.get(key) != now_common.get(key))}
    if source is None:
        return report
    source = Path(source)
    if not source.is_dir():
        raise ContractError("unavailable", t("The project files for this run are no longer available."))
    frozen, current = await asyncio.gather(asyncio.to_thread(source_identity, source), asyncio.to_thread(source_identity, root))
    if expected is not None and frozen != expected:
        raise ContractError("conflict", t("The code saved with this run changed. It cannot be restored."))
    report["extraFiles"] = sorted(current.keys() - frozen.keys())
    project = plain(await ctx.developer("developer.projects.open", {"directory": str(root), "name": obj["name"]}))
    if project is None:
        raise ContractError("unavailable", t("Enable Developer to restore this run's project files."))
    for name in sorted(frozen):
        content = text_of(source / name)
        if content is None or (name in current and text_of(root / name) is None):
            report["nonTextFiles"].append(name)
            continue
        if name not in current:
            await ctx.call("developer.documents.create", {"projectId": project["projectId"], "relativePath": name, "content": content})
            continue
        args = {"source": {"kind": "file", "uri": (root / name).as_uri()}}
        doc = plain(await ctx.call("developer.documents.open", args))
        if doc["conflict"]:
            raise ContractError("conflict", t("Resolve the conflicting edits to {name} in Developer first.", name=name))
        if not doc["dirty"] and current[name] == frozen[name]:
            continue
        if doc["dirty"]:
            await save(ctx, doc, name)
            doc = plain(await ctx.call("developer.documents.open", args))
        edited = plain(await ctx.call("developer.documents.edit", {"documentId": doc["documentId"], "expectedVersion": doc["version"], "content": content}))
        await save(ctx, edited, name)
    return report


def text_of(path):
    """The file's text, or None when it is not UTF-8 text (Developer edits text only)."""
    try:
        content = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in content else content


async def save(ctx, doc, name):
    saved = plain(await ctx.call("developer.documents.save", {"documentId": doc["documentId"], "expectedVersion": doc["version"]}))
    if saved["status"] not in {"saved", "clean"}:
        raise ContractError("conflict", t("{name} could not be saved: {message}.", name=name, message=saved['message'] or saved['status']))
