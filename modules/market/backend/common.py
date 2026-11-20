"""Shared Market vocabulary: error codes, time units and small conversions."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from typing import Any

from talyx_sdk import ContractError

NS = 1_000_000_000
DAY_NS = 86_400 * NS

# execution / effects per code (overview: one error format).
_NOT_STARTED = {"invalid_input", "not_found", "ambiguous", "unsupported", "fill_too_large", "invalid_file",
                "insufficient_space"}
_FAILED = {"no_data", "too_large", "source_unavailable", "network_unreachable", "rate_limited", "unauthorized",
           "forbidden", "cancelled"}
SOURCE_CODES = {"not_found", "no_data", "unsupported", "source_unavailable", "network_unreachable",
                "rate_limited", "unauthorized", "forbidden"}


class MarketError(ContractError):
    """A Market failure with its public code; details carry sourceId and the source's words."""

    def __init__(self, code: str, message: str, *, source_id: str | None = None, status: int | None = None,
                 retry_at: int | None = None, details: dict[str, Any] | None = None,
                 execution: str | None = None, effects: str | None = None):
        detail = dict(details or {})
        if source_id is not None:
            detail["sourceId"] = source_id
        if status is not None:
            detail["status"] = status
        if retry_at is not None:
            detail["retryAt"] = str(retry_at)
        if execution is None:
            execution = "not_started" if code in _NOT_STARTED else "failed"
        if effects is None:
            effects = "may_have_occurred" if code == "disk_full" else "none"
        super().__init__(code, message[:2000], detail or None, execution=execution, effects=effects)
        self.source_id, self.status, self.retry_at = source_id, status, retry_at

    def problem(self) -> dict[str, Any]:
        code = self.code if self.code in PROBLEM_CODES else "source_unavailable"
        return {"code": code, "message": self.message[:300], "sourceId": self.source_id, "status": self.status,
                "retryAt": None if self.retry_at is None else str(self.retry_at)}


PROBLEM_CODES = {"not_found", "no_data", "unsupported", "source_unavailable", "network_unreachable", "rate_limited",
                 "unauthorized", "forbidden", "fill_too_large", "disk_full", "cancelled"}


def problem_of(exc: BaseException, source_id: str | None = None) -> dict[str, Any]:
    if isinstance(exc, MarketError):
        value = exc.problem()
        if value["sourceId"] is None:
            value["sourceId"] = source_id
        return value
    if isinstance(exc, ContractError):
        code = exc.code if exc.code in PROBLEM_CODES else "source_unavailable"
        return {"code": code, "message": exc.message[:300], "sourceId": source_id, "status": None, "retryAt": None}
    return {"code": "source_unavailable", "message": f"{type(exc).__name__}: {exc}"[:300], "sourceId": source_id,
            "status": None, "retryAt": None}


_clock = time.time_ns


def now_ns() -> int:
    """Wall-clock nanoseconds (tests replace the clock with ``set_clock``)."""
    return _clock()


def set_clock(clock) -> None:
    global _clock
    _clock = clock or time.time_ns


def ns_of_date(value: date | str) -> int:
    day = date.fromisoformat(value) if isinstance(value, str) else value
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()) * NS


def date_of_ns(value: int) -> date:
    return datetime.fromtimestamp(value // NS, UTC).date()


def iso_of_ns(value: int | None) -> str | None:
    if value is None:
        return None
    moment = datetime.fromtimestamp(value / NS, UTC)
    text = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return text + "Z" if value % NS == 0 else text + f".{(value % NS) // 1_000_000:03d}Z"


def ns_of_iso(text: str) -> int:
    """ISO date or date-time (UTC when no offset) to epoch nanoseconds."""
    text = text.strip()
    if len(text) == 10:
        return ns_of_date(text)
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1_000_000) * 1000


def ns_text(value: int | None) -> str | None:
    return None if value is None else str(int(value))


def time_range(start: int, end: int) -> dict[str, str]:
    return {"start": str(start), "end": str(end), "unit": "ns", "bounds": "[)"}
