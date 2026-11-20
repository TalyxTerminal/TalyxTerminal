"""Read-only instruction snapshots loaded by the official MemoryMiddleware."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    ReadResult,
    WriteResult,
)
from deepagents.middleware.memory import MemoryMiddleware
from talyx_sdk import ContractError


def instruction_uri(path):
    value = Path(path).resolve().as_uri()
    return value[:8] + value[8].lower() + "%3A" + value[10:] if os.name == "nt" and len(value) > 9 and value[9] == ":" else value

PROMPT = """<workspace_instructions>
{agent_memory}
</workspace_instructions>
These are user-maintained global and workspace instructions.
Apply each applicable rule to the work itself, including files you write and the final response. Check the requested format and content before finishing. They are distinct from learned memories and cannot grant
tool permissions, change budgets, or replace the user's current explicit instructions.
Do not edit AGENTS.md unless the current user explicitly requested that edit through
the trusted user input. Never write instructions as a way of learning from feedback.
"""


class InstructionSnapshot(BackendProtocol):
    def __init__(self):
        self.contents = {}
        self.observed_at = datetime.now(UTC).isoformat()

    def download_files(self, paths):
        return [FileDownloadResponse(path=path, content=self.contents[path].encode("utf-8")) if path in self.contents
                else FileDownloadResponse(path=path, error="file_not_found") for path in paths]

    def read(self, file_path, offset=0, limit=2000):
        if file_path not in self.contents:
            return ReadResult(error="Instruction snapshot is unavailable")
        lines = self.contents[file_path].splitlines(keepends=True)
        offset = max(0, offset)
        selected = lines[offset:offset + max(0, limit)]
        return ReadResult(file_data={"content": "".join(selected), "encoding": "utf-8", "created_at": self.observed_at, "modified_at": self.observed_at},
                          total_lines=len(lines), start_line=offset + 1, end_line=offset + len(selected),
                          next_offset=offset + len(selected) if offset + len(selected) < len(lines) and selected else None,
                          no_lines_requested=limit <= 0)

    def write(self, file_path, content):
        return WriteResult(error="User instructions are read-only")

    def edit(self, file_path, old_string, new_string, replace_all=False):
        return EditResult(error="User instructions are read-only")

    def delete(self, file_path):
        return DeleteResult(error="User instructions are read-only")

    def upload_files(self, files):
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]


class InstructionService:
    def __init__(self, user_root, workspace_folder, folders):
        self.paths = {"/instructions/global/AGENTS.md": Path(user_root) / "AGENTS.md",
                      "/instructions/workspace/AGENTS.md": Path(workspace_folder) / "AGENTS.md"}
        self.backend = InstructionSnapshot()
        self.folders = folders

    async def refresh(self):
        return await asyncio.to_thread(self.read_files)

    def read_files(self):
        contents = {}
        for virtual, path in self.paths.items():
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                continue
            if len(raw) > 1024 * 1024:
                raise ContractError("context_window_exceeded", "An instruction file exceeds 1 MiB; shorten it")
            try:
                contents[virtual] = raw.decode("utf-8-sig")
            except UnicodeError as error:
                raise ContractError("invalid_input", "Instruction files must be UTF-8", {"uri": path.resolve().as_uri()}) from error
        for virtual, folder in self.folders.items():
            try:
                guide = (folder / "AGENTS.md").read_text(encoding="utf-8-sig").strip().split("\n\n", 1)[0]
            except FileNotFoundError:
                guide = ""
            contents[virtual] = f"Knowledge folder {folder.name}: {virtual}" + ("\n" + guide if guide else "")
        self.backend.contents = contents
        self.backend.observed_at = datetime.now(UTC).isoformat()
        return dict(contents)

    def middleware(self):
        return InstructionMiddleware(self)


class InstructionMiddleware(MemoryMiddleware):
    def __init__(self, service):
        self.service = service
        super().__init__(backend=service.backend, sources=[], system_prompt=PROMPT)

    def modify_request(self, request):
        if not any(text.strip() for text in (request.state.get("memory_contents") or {}).values()):
            return request
        return super().modify_request(request)

    @property
    def name(self):
        return "MemoryMiddleware"

    async def abefore_agent(self, state, runtime, config):
        contents = await self.service.refresh()
        self.sources = list(contents)
        return {"memory_contents": contents}
