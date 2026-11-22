"""The one error format: every failure says whether it ran (``execution``) and whether it may have had effects."""

from __future__ import annotations

from typing import Any

from .i18n import platform_t


class ContractError(Exception):
    def __init__(self, code: str, message: str | None = None, details: Any = None,
                 *, execution: str = "not_started", effects: str = "none", call_id: str | None = None):
        super().__init__(message or code)
        self.code, self.message, self.details = code, message or code, details
        self.execution, self.effects = execution, effects
        self.call_id = call_id

    def to_dict(self) -> dict:
        value = {"code": self.code, "message": self.message, "execution": self.execution, "effects": self.effects}
        if self.details is not None:
            value["details"] = self.details
        if self.call_id is not None:
            value["callId"] = self.call_id
        return value

    @classmethod
    def from_dict(cls, value: dict) -> ContractError:
        return cls(value["code"], value["message"], value.get("details"), execution=value["execution"],
                   effects=value["effects"], call_id=value.get("callId"))


def provider_restarted(name: str) -> ContractError:
    """A call or stream whose provider's process went away before it answered."""
    return ContractError("provider_restarted", platform_t("The module providing {name} restarted before it answered; whether it ran is unknown", name=name), execution="unknown", effects="may_have_occurred")
