"""Installed modules: their manifests and contracts, read before any of their code runs.

Official modules are the folders under ``modules/``; installed ones are folders under the user's modules
directory. Both are the same kind of thing and run the same way. A module's manifest (``talyx.module.json``) is
small and hand-written; its contract (``public/contract.json``) is generated from its code.
"""

from __future__ import annotations

import functools
import hashlib
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import yaml
from talyx_sdk.errors import ContractError

from .files import write_json

MANIFEST = "talyx.module.json"
MANIFEST_VERSION = 5
MANIFEST_SCHEMA = Path(__file__).resolve().parents[1] / "contracts" / "manifest_v5.schema.json"


@dataclass
class Installed:
    id: str
    directory: Path
    manifest: dict
    contract: dict
    source: str                     # "official" or "installed"
    digest: str
    enabled: bool = True
    python: str = sys.executable
    environment_problem: str | None = None
    guide: dict[str, str] | None = None
    docs: dict[str, dict[str, str]] = field(default_factory=dict)   # authoring docs by topic

    @property
    def package(self) -> str:
        """Import name of the module's package: its path under the platform (``modules.<id>``), or its folder's
        name for a module installed elsewhere."""
        platform = Path(__file__).resolve().parents[1]
        if self.directory.resolve().is_relative_to(platform):
            return ".".join(self.directory.resolve().relative_to(platform).parts)
        return self.directory.name

    @property
    def requires(self) -> dict:
        return self.manifest.get("requires", {})


def read_manifest(directory: Path) -> dict:
    try:
        manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ContractError("invalid_module", f"{directory.name}: the manifest cannot be read ({exc})") from exc
    if not isinstance(manifest, dict):
        raise ContractError("invalid_module", f"{directory.name}: the manifest must be an object")
    if manifest.get("manifestVersion") != MANIFEST_VERSION:
        raise ContractError("invalid_module", f"{directory.name}: manifest version {manifest.get('manifestVersion')} "
                            f"is not {MANIFEST_VERSION}")
    errors = sorted(_manifest_schema().iter_errors(manifest), key=lambda error: list(error.path))
    if errors:
        raise ContractError("invalid_module", f"{directory.name}: the manifest is not valid: " + "; ".join(
            f"{'.'.join(map(str, error.path)) or 'manifest'}: {error.message}" for error in errors[:5]))
    return manifest


def _markdown(directory: Path, declared: str, field: str) -> Path:
    path = (directory / declared).resolve()
    if Path(declared).is_absolute() or not path.is_relative_to(directory.resolve()) or path.suffix.lower() != ".md":
        raise ContractError("invalid_module", f"{directory.name}: {field} must name a Markdown file inside the module")
    return path


def read_guide(directory: Path, declared: str) -> dict[str, str]:
    path = _markdown(directory, declared, "ai.guide")
    try:
        text = path.read_text(encoding="utf-8")
        header, separator, body = text.removeprefix("---\n").partition("\n---\n")
        if not text.startswith("---\n") or not separator:
            raise ValueError("expected YAML frontmatter")
        metadata = yaml.safe_load(header)
        if not isinstance(metadata, dict) or not all(isinstance(metadata.get(key), str) and
                                                     metadata[key].strip() for key in ("name", "description")):
            raise ValueError("expected name and description in frontmatter")
        if len(body) > 2500:
            raise ValueError(f"guide body is {len(body)} characters; maximum is 2500")
        return {"name": metadata["name"], "description": metadata["description"], "text": body}
    except (ValueError, OSError, yaml.YAMLError) as exc:
        raise ContractError("invalid_module", f"{directory.name}: cannot read ai.guide {declared}: {exc}") from exc


def read_docs(directory: Path, declared: list[dict]) -> dict[str, dict[str, str]]:
    """The module's authoring docs by topic: ``{title, text}``. They are read whole and served a page at a time."""
    docs = {}
    for entry in declared:
        if entry["topic"] in docs:
            raise ContractError("invalid_module", f"{directory.name}: ai.docs names the topic {entry['topic']} twice")
        path = _markdown(directory, entry["file"], "ai.docs")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise ContractError("invalid_module", f"{directory.name}: cannot read ai.docs {entry['file']}: {exc}") from exc
        docs[entry["topic"]] = {"title": entry["title"], "text": text}
    return docs


@functools.cache
def _manifest_schema():
    from jsonschema import Draft202012Validator
    return Draft202012Validator(json.loads(MANIFEST_SCHEMA.read_text(encoding="utf-8")))


def _digest(directory: Path, manifest: dict, contract: dict) -> str:
    """Changes with the declarations and the frontend entry's code, so a window loads the frontend again after a
    reload that changed it."""
    value = hashlib.sha256(json.dumps([manifest, contract], sort_keys=True).encode())
    frontend = directory / manifest["entries"]["frontend"] if "frontend" in manifest["entries"] else None
    if frontend is not None and frontend.is_file():
        value.update(frontend.read_bytes())
    return value.hexdigest()[:32]


@dataclass
class UnreadableModule:
    directory: Path
    source: str
    name: str
    problem: str

    @property
    def key(self):
        # A folder remains identifiable even if its manifest cannot be read, or repeats another module's id.
        return f"{self.source}:{self.directory.name}"

    def state(self):
        return {"id": self.key, "name": self.name, "source": self.source, "problem": self.problem, "state": "failed",
                "enabled": True, "description": "", "version": "", "pid": None}


class Catalog:
    def __init__(self, official_root: Path, installed_root: Path, state_path: Path, environments_root: Path):
        self.official_root, self.installed_root = official_root, installed_root
        self.state_path, self.environments_root = state_path, environments_root
        self.modules: dict[str, Installed] = {}
        self.skipped: dict[str, UnreadableModule] = {}
        self.disabled: set[str] = set(json.loads(state_path.read_text(encoding="utf-8"))["disabled"]) \
            if state_path.exists() else set()

    def discover(self) -> None:
        self.modules.clear()
        self.skipped.clear()
        for source, root in (("official", self.official_root), ("installed", self.installed_root)):
            if not root.exists():
                continue
            for directory in sorted(path for path in root.iterdir() if (path / MANIFEST).exists()):
                manifest = None
                try:
                    manifest = read_manifest(directory)
                    item = self.load(directory, source, manifest=manifest)
                    self.register(item)
                except ContractError as exc:
                    failed = UnreadableModule(directory, source, manifest["name"] if manifest else directory.name,
                                              exc.message)
                    self.skipped[failed.key] = failed

    def register(self, item: Installed) -> None:
        if item.id in self.modules:
            raise ContractError("conflict", f"{item.id} is already installed")
        self.modules[item.id] = item
        self.skipped.pop(f"{item.source}:{item.directory.name}", None)

    def load(self, directory: Path, source: str, *, manifest: dict | None = None) -> Installed:
        if manifest is None:
            manifest = read_manifest(directory)
        try:
            contract = json.loads((directory / "public" / "contract.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError("invalid_module", f"{manifest['id']}: the contract cannot be read ({exc})") from exc
        if not isinstance(contract, dict):
            raise ContractError("invalid_module", f"{directory.name}: the contract must be an object")
        if contract.get("module") != manifest["id"]:
            raise ContractError("invalid_module", f"{manifest['id']}: the contract belongs to {contract.get('module')}")
        item = Installed(manifest["id"], directory, manifest, contract, source, _digest(directory, manifest, contract),
                         enabled=manifest["id"] not in self.disabled)
        if item.enabled and "ai" in manifest:
            item.guide = read_guide(directory, manifest["ai"]["guide"])
            item.docs = read_docs(directory, manifest["ai"].get("docs", []))
        if manifest.get("python", {}).get("requirements"):
            item.python = str(self.environment_python(item))
        return item

    def environment_python(self, item: Installed) -> Path:
        folder = self.environments_root / item.id
        return folder / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def save_state(self) -> None:
        write_json(self.state_path, {"disabled": sorted(self.disabled)})

    def get(self, module_id: str) -> Installed:
        try:
            return self.modules[module_id]
        except KeyError:
            raise ContractError("not_found", f"No module {module_id} is installed") from None

    def dependents(self, module_id: str) -> list[str]:
        """Enabled modules that cannot work without ``module_id``."""
        return sorted(other.id for other in self.modules.values() if other.enabled and other.id != module_id
                      and module_id in other.requires and not other.requires[module_id].get("optional"))

    def implementations(self, point: str) -> list[dict]:
        """``{moduleId, id, descriptor}`` of every enabled module's implementation of ``point``."""
        return [{"moduleId": item.id, "id": entry["id"], "descriptor": entry["descriptor"]}
                for item in self.modules.values() if item.enabled
                for entry in item.manifest.get("provides", {}).get(point, [])]

    # ── installing ───────────────────────────────────────────────────────

    def install(self, source: Path) -> Installed:
        """Install a module from a folder or a zip file into the user's modules directory. A module folder already
        there (one written in place) is installed where it is."""
        if source.is_dir() and source.resolve().parent == self.installed_root.resolve():
            item = self.load(source, "installed")
            if item.id in self.modules:
                raise ContractError("conflict", f"{item.id} is already installed; uninstall it first")
            if item.id != source.name:
                raise ContractError("invalid_module", f"The folder of module {item.id} must be named {item.id}")
            self.register(item)
            return item
        staging = self.installed_root / f".install-{uuid4().hex}"
        staging.mkdir(parents=True)
        try:
            if source.is_dir():
                shutil.copytree(source, staging, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__", "node_modules", ".git"))
            elif zipfile.is_zipfile(source):
                with zipfile.ZipFile(source) as archive:
                    for member in archive.namelist():
                        target = (staging / member).resolve()
                        if not target.is_relative_to(staging.resolve()):
                            raise ContractError("invalid_module", f"The archive writes outside its folder: {member}")
                    archive.extractall(staging)
            else:
                raise ContractError("invalid_input", f"{source} is neither a folder nor a zip file")
            roots = [path for path in staging.iterdir() if (path / MANIFEST).exists()]
            root = staging if (staging / MANIFEST).exists() else roots[0] if len(roots) == 1 else None
            if root is None:
                raise ContractError("invalid_module", "The package has no talyx.module.json")
            item = self.load(root, "installed")
            if item.id in self.modules:
                raise ContractError("conflict", f"{item.id} is already installed; uninstall it first")
            target = self.installed_root / item.id
            root.rename(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        item.directory = target
        self.register(item)
        return item

    def uninstall(self, module_id: str) -> None:
        item = self.get(module_id)
        del self.modules[module_id]
        shutil.rmtree(item.directory)
        shutil.rmtree(self.environments_root / module_id, ignore_errors=True)
        self.disabled.discard(module_id)
        self.save_state()
