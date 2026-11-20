"""Trusted attribution inherited by model, summary, search and child requests."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageContext:
    workspace_id: str | None = None
    conversation_id: str | None = None
    group_id: str | None = None
    member_id: str | None = None
    work_run_id: str | None = None
    child_run_id: str | None = None
    unattended: bool = False

    def __post_init__(self):
        if self.group_id is not None and self.workspace_id is None:
            raise ValueError("Group usage requires its workspace identity")


DEFAULT_CONTEXT = UsageContext()
CURRENT_USAGE = ContextVar("talyx_agent_usage", default=DEFAULT_CONTEXT)

