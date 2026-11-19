from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

FAIL_MARKER = "[fail]"
FIXED_FAILURE_REASON = "固定执行器按要求返回失败"


@dataclass(frozen=True)
class ExecutionOutcome:
    ok: bool
    result: str | None
    error: str | None


class Executor(Protocol):
    def execute(self, request: str) -> ExecutionOutcome: ...


class FixedExecutor:
    """Deterministic executor used by the RunAPI chapter."""

    def execute(self, request: str) -> ExecutionOutcome:
        if request.casefold().startswith(FAIL_MARKER):
            return ExecutionOutcome(
                ok=False,
                result=None,
                error=FIXED_FAILURE_REASON,
            )
        return ExecutionOutcome(
            ok=True,
            result=f"固定执行器完成：{request}",
            error=None,
        )
