from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .schemas import RunStatus

MAX_REQUEST_CHARS = 2000


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    request: str
    status: RunStatus
    result: str | None
    error: str | None
    run_type: str
    input: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True)
class RunEvent:
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class RunTrace:
    run: RunRecord
    steps: list[dict[str, Any]] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)


class ValidationError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field} {reason}")
        self.field = field
        self.reason = reason


class RunStoreError(Exception):
    """A database read or write could not be confirmed."""


class DuplicateIdempotencyKeyError(RunStoreError):
    """The same organization already used this idempotency key."""


def validate_request(request: object) -> str:
    if not isinstance(request, str):
        raise ValidationError("request", "必须是字符串")

    text = request.strip()
    if not text:
        raise ValidationError("request", "不能为空")
    if len(text) > MAX_REQUEST_CHARS:
        raise ValidationError("request", f"不能超过 {MAX_REQUEST_CHARS} 个字符")
    return text
