from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MAX_REQUEST_CHARS = 2000

RunStatus = Literal["succeeded", "failed"]


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    request: str
    status: RunStatus
    result: str | None
    error: str | None


class ValidationError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field} {reason}")
        self.field = field
        self.reason = reason


def validate_request(request: object) -> str:
    if not isinstance(request, str):
        raise ValidationError("request", "必须是字符串")

    text = request.strip()
    if not text:
        raise ValidationError("request", "不能为空")
    if len(text) > MAX_REQUEST_CHARS:
        raise ValidationError("request", f"不能超过 {MAX_REQUEST_CHARS} 个字符")
    return text
