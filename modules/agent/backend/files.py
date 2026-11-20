"""Virtual workspace and knowledge mounts for the framework's file tools."""

import asyncio
import mimetypes
import shutil
import subprocess
import weakref
from pathlib import Path, PurePosixPath
from uuid import uuid4

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.filesystem import _raise_if_symlink_loop
from deepagents.middleware.filesystem import (
    FilesystemMiddleware,
    FilesystemPermission,
    _permission_error,
)
from langchain_core.messages import ToolMessage
from talyx_sdk import ContractError
from wcmatch import glob as wcglob


class Files:
    def __init__(self, service):
        self.service = service
        self.edits = weakref.WeakValueDictionary()
        self.db = service.store.db
        self.db.execute("CREATE TABLE IF NOT EXISTS knowledge_folders "
                        "(id TEXT PRIMARY KEY, workspace TEXT, path TEXT NOT NULL, writable INTEGER NOT NULL)")

    def folders(self, workspace):
        return [dict(row) for row in self.db.execute("SELECT * FROM knowledge_folders WHERE workspace IS NULL OR workspace=? "
                                                    "ORDER BY rowid", (workspace,))]

    def bind(self, workspace, path, writable):
        root = Path(path)
        if not root.is_absolute() or not root.is_dir() or type(writable) is not bool:
            raise ContractError("invalid_input", "Choose a local folder and its write permission")
        with self.service.store.transaction():
            self.db.execute("INSERT INTO knowledge_folders VALUES (?,?,?,?)",
                            (uuid4().hex, workspace, str(root.resolve()), writable))

    def unbind(self, workspace, identity):
        with self.service.store.transaction():
            if not self.db.execute("DELETE FROM knowledge_folders WHERE id=? AND (workspace IS NULL OR workspace=?)",
                                   (identity, workspace)).rowcount:
                raise ContractError("not_found", "The knowledge folder is unavailable")

    async def roots(self, workspace):
        return {"/workspace/": Path(await self.service.ctx.platform.workspace_folder(workspace)),
                **{"/knowledge/" + item["id"] + "/": Path(item["path"]) for item in self.folders(workspace)}}

    async def resolve(self, workspace, virtual):
        for prefix, root in (await self.roots(workspace)).items():
            if virtual.startswith(prefix):
                path = (root / virtual[len(prefix):]).resolve()
                if path.is_relative_to(root.resolve()) and path.is_file():
                    return path
        raise ContractError("not_found", "Choose a file in this workspace or a connected knowledge folder")

    async def choices(self, workspace, query=""):
        roots = await self.roots(workspace)
        def scan():
            choices = []
            for prefix, root in roots.items():
                for path in root.rglob("*"):
                    if path.is_file() and query.casefold() in path.name.casefold() and path.resolve().is_relative_to(root):
                        choices.append({"path": prefix + path.relative_to(root).as_posix(),
                                        "place": "workspace" if prefix == "/workspace/" else "knowledge"})
                        if len(choices) == 250:
                            return choices
            return choices
        return await asyncio.to_thread(scan)

    async def attach(self, workspace, conversation, virtual):
        path = await self.resolve(workspace, virtual)
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ContractError("attachment_too_large", "Each attachment may contain at most 10 MiB")
        data = await asyncio.to_thread(path.read_bytes)
        mime = mimetypes.guess_type(path.name)[0] or "text/plain"
        if mime.startswith("text/") and mime not in {"text/markdown", "text/csv"}:
            mime = "text/plain"
        return self.service.attach(conversation, workspace, path.name, mime, data)

    async def open(self, workspace, virtual, conversation):
        if virtual.startswith("/attachments/"):
            identity = virtual.removeprefix("/attachments/")
            attachment = self.service.attachment(identity, conversation)
            directory = self.service.root / "files" / conversation / "attachment-previews"
            suffix = mimetypes.guess_extension(attachment["mime"]) or ".txt"
            path = directory / (identity + suffix)
            def prepare():
                directory.mkdir(parents=True, exist_ok=True)
                if not path.is_file():
                    shutil.copyfile(self.service.root / "attachments" / identity, path)
            await asyncio.to_thread(prepare)
        else:
            path = await self.resolve(workspace, virtual)
        ctx = self.service.ctx
        implementations = await ctx.extensions("open_file")
        if implementations:
            target = implementations[0]
            return await ctx.call_extension("open_file", target["moduleId"], target["id"], "open",
                                            {"path": str(path)}, workspace=workspace)
        await asyncio.to_thread(subprocess.Popen, ["explorer.exe", "/select,", str(path)])
        return {}

    async def mount(self, turn):
        workspace = turn["workspaceId"]
        folder = Path(await self.service.ctx.platform.workspace_folder(workspace))
        # The framework's own artifacts (evicted tool results, full run outputs under /.agent/) stay private to the chat.
        private = self.service.root / "files" / turn["conversationId"]
        private.mkdir(parents=True, exist_ok=True)
        mounts = {"/workspace/": folder, "/skills/global/": self.service.skills.user_root,
                  "/skills/workspace/": folder / "skills"}
        for root in (mounts["/skills/global/"], mounts["/skills/workspace/"]):
            root.mkdir(parents=True, exist_ok=True)
        permissions = []
        bot = self.service.bots.profile(self.service.execution_conversation(turn))
        def permit_selected(prefix, selected):
            for virtual in selected:
                # The mounted directory and every ancestor of a selected entry must be listable.
                path = PurePosixPath(virtual)
                for parent in (path, *path.parents):
                    if (str(parent) + "/").startswith(prefix):
                        name = wcglob.escape(str(parent), unix=True)
                        permissions.append(FilesystemPermission(operations=["read"], paths=[name, name + "/"], mode="allow"))
                literal = wcglob.escape(virtual.rstrip("/"), unix=True)
                permissions.append(FilesystemPermission(operations=["write"], paths=[literal], mode="allow"))
                physical = mounts[prefix] / virtual.removeprefix(prefix)
                if physical.is_dir():
                    permissions.append(FilesystemPermission(operations=["read","write"], paths=[literal + "/**"], mode="allow"))
            permissions.append(FilesystemPermission(operations=["read","write"], paths=[prefix.rstrip("/"), prefix+"**"], mode="deny"))
        if bot:
            mounts["/workspace/skills/"] = folder / "skills"
            permit_selected("/workspace/skills/", ["/workspace/skills/" + name for name in bot["skills"]
                                                    if "/" not in name and name not in (".", "..")])
            for prefix in ("/skills/global/", "/skills/workspace/"):
                permit_selected(prefix, [prefix + name for name in bot["skills"] if "/" not in name and name not in (".", "..")])
        for item in self.folders(workspace):
            prefix = "/knowledge/" + item["id"] + "/"
            mounts[prefix] = Path(item["path"])
            if not item["writable"]:
                permissions.append(FilesystemPermission(operations=["write"], paths=[prefix + "**"], mode="deny"))
            if bot:
                permit_selected(prefix, [path for path in bot["knowledge"] if (path.rstrip("/") + "/").startswith(prefix)])
        routes = {prefix: Folder(root_dir=path, virtual_mode=True) for prefix, path in mounts.items()}
        backend = CompositeBackend(default=Folder(root_dir=private, virtual_mode=True), routes=routes,
                                   artifacts_root="/.agent")
        return backend, RecordedFiles(self.service, turn, routes, backend=backend, permissions=permissions,
                                     tool_token_limit_before_evict=5000, custom_tool_descriptions=FILE_TOOLS,
                                     tools=["ls", "glob", "grep", "read_file", "write_file", "edit_file", "delete"])


class Folder(FilesystemBackend):
    """The framework's folder backend. On Windows a path whose folder another write creates while it resolves comes
    back with the \\\\?\\ prefix (CPython's realpath keeps it then); the framework would see it outside its root."""

    def _resolve_path(self, key):
        path = key if key.startswith("/") else "/" + key
        if ".." in path or path.startswith("~"):
            raise ValueError("Path traversal not allowed")
        text = str((self.cwd / path.lstrip("/")).resolve())
        full = Path("\\\\" + text[8:] if text.startswith("\\\\?\\UNC\\") else text.removeprefix("\\\\?\\"))
        if not full.is_relative_to(self.cwd):
            raise ValueError(f"Path:{full} outside root directory: {self.cwd}")
        _raise_if_symlink_loop(full)
        return full


# Short descriptions in place of the framework's general-purpose ones (which tell the model to list folders first).
FILE_TOOLS = {
    "ls": "List one folder: /workspace/ (this workspace's folder), /skills/global/, /skills/workspace/ or a knowledge "
          "folder. Use it when the request is about files there.",
    "read_file": "Read a text file a page of lines at a time (offset, limit; default the first 100 lines). A tool "
                 "result too large for the chat is saved as a file and its path given: read it here.",
    "write_file": "Create a file or replace its whole content.",
    "edit_file": "Replace exact text in a file you have read: old_string must occur once unless replace_all.",
    "delete": "Delete a file, or a folder with its contents.",
    "glob": "Find files whose path matches a pattern (*.py, data/**/*.csv) under path.",
    "grep": "Find literal text (not a regular expression) in files under path; output_mode gives the files, the "
            "matching lines or the counts.",
}


class RecordedFiles(FilesystemMiddleware):
    @property
    def name(self):
        return "FilesystemMiddleware"

    def __init__(self, service, turn, mounts, **options):
        super().__init__(**options)
        self.service, self.turn, self.mounts = service, turn, mounts

    def skill_path(self, virtual):
        for prefix, folder in self.mounts.items():
            if prefix.startswith("/skills/") and virtual.startswith(prefix):
                if error := _permission_error(self._permissions, "read", self.backend, virtual):
                    raise ContractError("invalid_input", error)
                try:
                    return folder._resolve_path(virtual[len(prefix):])
                except ValueError as error:
                    raise ContractError("invalid_input", str(error)) from error
        raise ContractError("invalid_input", "Choose a workspace or global skill script")

    async def awrap_tool_call(self, request, handler):
        call = request.tool_call
        if call["name"] not in {"write_file", "edit_file", "delete"}:
            return await super().awrap_tool_call(request, handler)
        virtual = call["args"]["file_path"]
        mount = next((prefix for prefix in self.mounts if virtual.startswith(prefix)), None)
        if mount is None:
            return ToolMessage(content="Choose a workspace, skill or knowledge file.", tool_call_id=call["id"], status="error")
        try:
            path = self.mounts[mount]._resolve_path("/" + virtual[len(mount):])
        except ValueError as error:  # the model's path leaves the folder
            return ToolMessage(content=str(error), tool_call_id=call["id"], status="error")
        def contents():
            try:
                return path.read_bytes()
            except FileNotFoundError:
                return None
        if path.is_dir():
            return ToolMessage(content="Choose a file to edit or delete.", tool_call_id=call["id"], status="error")
        async with self.service.files.edits.setdefault(path, asyncio.Lock()):
            before = await asyncio.to_thread(contents)
            result = await super().awrap_tool_call(request, handler)
            after = await asyncio.to_thread(contents)
            if before != after:
                self.service.changes.record_file(self.turn, call["id"], virtual, path, before, after)
                if after is not None and path.name == "SKILL.md" and path.parent.parent in {
                    self.mounts["/skills/global/"].cwd, self.mounts["/skills/workspace/"].cwd,
                }:
                    import yaml
                    from deepagents.middleware.skills import (
                        _FRONTMATTER_PATTERN,
                        MAX_SKILL_DESCRIPTION_LENGTH,
                        _parse_skill_metadata,
                        _validate_skill_name,
                    )

                    content = after.decode("utf-8")
                    metadata = _parse_skill_metadata(content, virtual, path.parent.name)
                    if metadata is None:
                        problem = "Use YAML front matter with a name and description."
                    else:
                        _, problem = _validate_skill_name(metadata["name"], path.parent.name)
                        if len(metadata["description"]) == MAX_SKILL_DESCRIPTION_LENGTH:
                            frontmatter = yaml.safe_load(_FRONTMATTER_PATTERN.match(content).group(1))
                            if len(str(frontmatter["description"]).strip()) > MAX_SKILL_DESCRIPTION_LENGTH:
                                problem = f"Shorten description to {MAX_SKILL_DESCRIPTION_LENGTH} characters or fewer."
                    if problem:
                        return ToolMessage(content=f"Saved {virtual}. Skill check: {problem}",
                                           tool_call_id=call["id"], status="error")
                    return ToolMessage(content=f"Saved and checked skill {metadata['name']} at {virtual}.",
                                       tool_call_id=call["id"])
            return result
