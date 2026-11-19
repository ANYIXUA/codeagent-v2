from __future__ import annotations

import secrets
from collections.abc import Callable

from .model import RunRecord, validate_request
from .runtime import ExecutionOutcome, Executor


def default_run_id() -> str:
    return f"run_{secrets.token_hex(6)}"


class InMemoryRunStore:
    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}

    def put(self, record: RunRecord) -> None:
        self._records[record.run_id] = record

    def get(self, run_id: str) -> RunRecord | None:
        return self._records.get(run_id)

    def count(self) -> int:
        return len(self._records)


class RunAPI:
    def __init__(
        self,
        store: InMemoryRunStore,
        executor: Executor,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._id_factory = id_factory or default_run_id

    def _new_run_id(self) -> str:
        run_id = self._id_factory()
        while self._store.get(run_id) is not None:
            run_id = self._id_factory()
        return run_id

    def submit_run(self, request: str) -> RunRecord:
        text = validate_request(request)
        run_id = self._new_run_id()

        try:
            outcome = self._executor.execute(text)
        except Exception as exc:  # Keep the API contract for valid requests.
            outcome = ExecutionOutcome(
                ok=False,
                result=None,
                error=f"执行器异常：{type(exc).__name__}: {exc}",
            )

        record = RunRecord(
            run_id=run_id,
            request=text,
            status="succeeded" if outcome.ok else "failed",
            result=outcome.result if outcome.ok else None,
            error=outcome.error if not outcome.ok else None,
        )
        self._store.put(record)
        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._store.get(run_id)
