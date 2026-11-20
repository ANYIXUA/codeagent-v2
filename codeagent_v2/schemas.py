from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


MAX_INPUT_NESTING = 64


def _check_json_depth(value: object, depth: int = 0) -> None:
    if isinstance(value, dict):
        children = value.values()
    elif isinstance(value, (list, tuple)):
        children = value
    else:
        return

    if depth >= MAX_INPUT_NESTING:
        raise ValueError(f"Input can contain at most {MAX_INPUT_NESTING} nested JSON containers.")
    for child in children:
        _check_json_depth(child, depth + 1)


def find_null_character(value: object, path: str = "input") -> str | None:
    """Return the first path containing an actual U+0000 character."""

    if isinstance(value, dict):
        for key, child in value.items():
            key_text = key if isinstance(key, str) else str(key)
            child_path = f"{path}.{key_text}"
            if "\x00" in key_text:
                return child_path
            found = find_null_character(child, child_path)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = find_null_character(child, f"{path}[{index}]")
            if found is not None:
                return found
    elif isinstance(value, str) and "\x00" in value:
        return path
    return None


def validate_json_value(value: object) -> None:
    _check_json_depth(value)
    null_path = find_null_character(value)
    if null_path is not None:
        raise ValueError("Input must not contain the actual U+0000 character.")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Input must contain valid UTF-8 JSON values and finite numbers.") from exc


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CreateRunRequest(BaseModel):
    run_type: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=200)
    input: dict[str, Any]

    @field_validator("input")
    @classmethod
    def validate_input_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_json_value(value)
        return value


class RunResponse(BaseModel):
    run_id: str
    run_type: str
    status: RunStatus
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class CancelRunRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class ResumeRunRequest(BaseModel):
    resume_token: str = Field(min_length=1, max_length=200)
    input: dict[str, Any] = Field(default_factory=dict)
