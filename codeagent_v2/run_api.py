from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from .model import RunRecord, validate_request
from .runtime import ExecutionOutcome, Executor
from .schemas import CreateRunRequest, RunResponse, RunStatus


logger = logging.getLogger(__name__)


def default_run_id() -> str:
    return f"run_{secrets.token_hex(6)}"


def _run_response(record: RunRecord) -> RunResponse:
    return RunResponse(
        run_id=record.run_id,
        run_type=record.run_type,
        status=record.status,
        input=deepcopy(record.input),
        output={"text": record.result} if record.result is not None else None,
        error={"code": "RUN_FAILED", "message": record.error} if record.error is not None else None,
        created_at=record.created_at,
        started_at=record.started_at,
        ended_at=record.ended_at,
    )


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

    def create_run(self, request: CreateRunRequest) -> RunResponse:
        record = self.submit_run(
            request.input.get("query"),
            run_type=request.run_type,
            run_input=request.input,
        )
        return _run_response(record)

    def query_run(self, run_id: str) -> RunResponse | None:
        record = self.get_run(run_id)
        return _run_response(record) if record is not None else None

    def submit_run(
        self,
        request: object,
        *,
        run_type: str = "text",
        run_input: dict[str, Any] | None = None,
    ) -> RunRecord:
        text = validate_request(request)
        created_at = datetime.now(UTC)
        run_id = self._new_run_id()

        started_at = datetime.now(UTC)
        try:
            outcome = self._executor.execute(text)
        except Exception:
            logger.debug("Run executor raised an exception", exc_info=True)
            outcome = ExecutionOutcome(
                ok=False,
                result=None,
                error="执行器异常",
            )

        record = RunRecord(
            run_id=run_id,
            request=text,
            status=RunStatus.SUCCEEDED if outcome.ok else RunStatus.FAILED,
            result=outcome.result if outcome.ok else None,
            error=outcome.error if not outcome.ok else None,
            run_type=run_type,
            input=deepcopy(run_input) if run_input is not None else {"query": text},
            created_at=created_at,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )
        self._store.put(record)
        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._store.get(run_id)
