"""Display the skill catalog produced by the same middleware that loads skills into the graph."""
from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.middleware.skills import SkillsMiddleware
from talyx_sdk import ContractError
from talyx_sdk.i18n import t

from ..store import digest


class SkillsService:
    def __init__(self, user_root):
        self.user_root = Path(user_root)
        self.user_root.mkdir(parents=True, exist_ok=True)

    def snapshot(self, workspace_folder=None):
        roots = {"/skills/user/": self.user_root}
        if workspace_folder is not None and workspace_folder.is_dir():
            roots["/skills/workspace/"] = workspace_folder
        mounts = {prefix: FilesystemBackend(root_dir=folder, virtual_mode=True) for prefix,folder in roots.items()}
        backend = CompositeBackend(default=mounts["/skills/user/"], routes=mounts)
        found = SkillsMiddleware(backend=backend, sources=list(roots)).before_agent({}, None, {})
        if found["skills_load_errors"]:
            raise ContractError("skill_invalid", t("Fix the skill metadata before using Skills"), found["skills_load_errors"])
        items = found["skills_metadata"]
        return {"version": digest(items), "items": items}
