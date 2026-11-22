"""The running Developer: its services, what other modules provide at its points, and its panel's own routes."""
import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from talyx_sdk import ContractError
from talyx_sdk.i18n import t
from talyx_sdk.process import Context

from . import sdk_sources, templates, vscode
from .documents.actions import page, read_chars
from .documents.actions import router as document_routes
from .documents.service import DocumentService
from .language.service import LanguageService
from .projects.service import ProjectService, canonical_uri
from .supports import DevelopmentSupports

POINTS = ("development_support", "document_source")
STATUS = {"not_found": 404, "conflict": 409, "invalid_input": 400, "permission_denied": 403,
          "source_unavailable": 409, "source_replaced": 409, "unavailable": 503}


class NewModule(BaseModel):
    name: str
    moduleId: str | None = None


class Developer:
    def __init__(self, ctx: Context, directories: dict):
        self.ctx = ctx
        data = Path(directories["dataDir"])
        self.modules_dir = Path(directories["modulesDir"])
        # Nothing of TALYX's own data, no module's own folder and no installed module goes to the Recycle Bin from here.
        self.projects = ProjectService(data, protected_paths=(data.parents[1], Path(directories["userDir"]).parent,
                                                              self.modules_dir))
        self.documents = DocumentService(self, self.projects, data)
        self.projects.before_remove = self.check_unsaved_project
        self.projects.after_write = lambda path: self.documents.refresh()
        self.supports = DevelopmentSupports(self, self.documents, self.projects)
        self.language = LanguageService(self.documents, self.supports, data)
        self.provided: dict[str, list[dict]] = {}

    async def check_unsaved_project(self, project_id, mode):
        if any(item["context"]["projectId"] == project_id for item in self.documents.unsaved_records()):
            raise ContractError("conflict", t("Save or discard this project's unsaved files before removing it."))

    async def start(self) -> None:
        for point in POINTS:
            self.provided[point] = await self.ctx.extensions(point)
            self.ctx.watch_extensions(point, lambda items, point=point: self.provided.__setitem__(point, items))
        loop = asyncio.get_running_loop()
        self.projects.on_changed = lambda: loop.call_soon_threadsafe(self.publish, "projects_changed", {})
        self.documents.start()

    # ── what others provide ──────────────────────────────────────────────

    def implementations(self, point: str) -> list[dict]:
        """``{moduleId, id, descriptor}`` of every enabled module's implementation of one of Developer's points."""
        return self.provided[point]

    async def extension(self, point: str, module_id: str, id: str, name: str, value: dict) -> dict:
        if module_id == "developer":   # Developer's own (the SDK references)
            return await {"read": sdk_sources.read, "write": sdk_sources.write}[name](value)
        return await self.ctx.call_extension(point, module_id, id, name, value)

    def publish(self, event: str, payload: dict) -> None:
        asyncio.get_running_loop().create_task(self.ctx.publish(event, payload))

    def help(self) -> list[dict]:
        """What Help lists: the SDK references, then each support's guides and examples."""
        return [{"moduleId": "developer", "name": "TALYX SDK", "documentation": sdk_sources.documentation()}] + [
            {"moduleId": item["moduleId"], "name": item["descriptor"]["name"], "documentation": item["descriptor"].get("documentation", [])}
            for item in self.implementations("development_support") if item["descriptor"].get("documentation")]

    # ── modules written here ─────────────────────────────────────────────

    async def create_module(self, name: str, identifier: str | None) -> dict:
        identifier, directory = await templates.create_module(self.modules_dir, name, identifier)
        project = self.projects.open(str(directory), name=name.strip())
        return {"moduleId": identifier, "projectId": project["projectId"], "name": project["name"],
                "rootUri": project["rootUri"], "directory": str(directory)}

    def module_of(self, project_id: str) -> dict | None:
        """The TALYX module a project is (its manifest's id and name), or None."""
        manifest = self.projects.path(project_id, "talyx.module.json")
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        root = self.projects.path(project_id)
        return {"id": value.get("id"), "name": value.get("name"), "directory": str(root),
                "installed": root.parent == self.modules_dir.resolve()}

    async def module_runtime(self, module_id: str, workspace: str | None = None) -> dict | None:
        states = await self.ctx.call("platform.modules.list", {}, workspace=workspace)
        return next((state for state in states if state["id"] == module_id), None)

    async def debug_in_vscode(self, project_id: str) -> dict:
        module = self.module_of(project_id)
        if module is None:
            raise ContractError("invalid_input", t("Only a TALYX module's backend is debugged from here."))
        state = await self.module_runtime(module["id"])
        if state is None or state["pid"] is None:
            raise ContractError("unavailable", t("Start this module in TALYX before debugging it."))
        root = self.projects.path(project_id)
        path = vscode.write_launch(root, [vscode.module_attach(root, module["name"] or module["id"], state["pid"])])
        vscode.open_folder(root)
        return {"launch": canonical_uri(path)}

    # ── the panel's routes ───────────────────────────────────────────────

    def routes(self) -> FastAPI:
        app = FastAPI()
        projects, documents = self.projects, self.documents

        @app.exception_handler(ContractError)
        async def failed(_request, error: ContractError):
            return JSONResponse({"error": error.to_dict()}, status_code=STATUS.get(error.code, 500))

        @app.get("/projects/{project_id}/module")
        async def module(project_id: str):
            return {"module": self.module_of(project_id)}

        @app.post("/projects/{project_id}/vscode")
        async def open_in_vscode(project_id: str):
            root = projects.path(project_id)
            vscode.open_folder(root)
            return {"opened": str(root)}

        @app.post("/projects/{project_id}/debug")
        async def debug(project_id: str):
            return await self.debug_in_vscode(project_id)

        @app.post("/modules")
        async def new_module(value: NewModule):
            return await self.create_module(value.name, value.moduleId)

        @app.get("/help")
        async def help_entries():
            return {"supports": self.help()}

        @app.post("/language/resolve")
        async def resolve(value: dict):
            return await self.language.resolve(value["documentId"])

        @app.websocket("/language/sessions/{session}")
        async def socket(websocket: WebSocket, session: str):
            await self.language.serve(websocket, session)

        app.include_router(projects.router())
        app.include_router(document_routes(documents))
        return app

    def tree_page(self, project_id: str, path: str, cursor: str | None, limit: int) -> dict:
        entries = [{key: entry[key] for key in ("path", "uri", "kind", "size")} for entry in self.projects.children(project_id, path)]
        selected, next_cursor = page(entries, [project_id, path], cursor, limit)
        return {"entries": selected, "nextCursor": next_cursor}

    def instructions(self, project_id: str, start: int, count: int, expected: str | None, char: int | None = None) -> dict:
        path = self.projects.path(project_id, "AGENTS.md")
        raw = path.read_bytes() if path.is_file() else b""
        digest = hashlib.sha256(raw).hexdigest() if raw else None
        if expected is not None and expected != digest:
            raise ContractError("conflict", t("The project's instructions changed; read again from the first line"))
        lines = raw.decode("utf-8-sig").splitlines(keepends=True)
        selected = lines[start - 1:start - 1 + count]
        if selected and (char is not None or len(json.dumps(selected[0], ensure_ascii=False).encode()) > 16000):
            chunk = read_chars({"content":selected[0],"baseContent":""}, char or 0)
            return {"projectId":project_id,"uri":canonical_uri(path),"exists":bool(raw),"digest":digest,"content":chunk["content"],
                    "startLine":start,"nextLine":start + 1 if start < len(lines) else None,
                    "startChar":chunk["startChar"],"nextChar":chunk["nextChar"]}
        while selected and len(json.dumps("".join(selected), ensure_ascii=False).encode()) > 16000:
            selected.pop()
        end = start - 1 + len(selected)
        return {"projectId": project_id, "uri": canonical_uri(path), "exists": bool(raw), "digest": digest,
                "content": "".join(selected), "startLine": start, "nextLine": end + 1 if end < len(lines) else None}
