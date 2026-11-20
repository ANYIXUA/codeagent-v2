from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from threading import Lock
from typing import Any, Protocol

from .model import (
    DuplicateIdempotencyKeyError,
    RunEvent,
    RunRecord,
    RunStoreError,
    RunTrace,
    ValidationError,
    validate_request,
)
from .runtime import ExecutionOutcome, Executor
from .schemas import (
    CreateRunRequest,
    RunResponse,
    RunStatus,
    find_null_character,
    validate_json_value,
)


logger = logging.getLogger(__name__)

MAX_ID_ATTEMPTS = 100


def default_run_id() -> str:
    return f"run_{secrets.token_hex(6)}"


def _default_event_id() -> str:
    return f"evt_{secrets.token_hex(6)}"


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


class RunStore(Protocol):
    def submission(
        self, *, idempotency_key: str
    ) -> AbstractContextManager[Callable[[RunRecord], RunRecord | None]]: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def list_events(self, run_id: str) -> list[RunEvent] | None: ...

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunEvent | None: ...

    def get_trace(self, run_id: str) -> RunTrace | None: ...


class InMemoryRunStore:
    """Test double that follows the same contract as the PostgreSQL store."""

    def __init__(self, org_id: str = "local-org") -> None:
        self.org_id = org_id
        self._records: dict[str, RunRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._events: dict[str, list[RunEvent]] = {}
        self._submission_lock = Lock()

    def put(self, record: RunRecord) -> None:
        self._records[record.run_id] = record
        self._events.setdefault(record.run_id, [])

    def add(self, record: RunRecord, *, idempotency_key: str) -> bool:
        with self.submission(idempotency_key=idempotency_key) as save:
            return save(record) is not None

    @contextmanager
    def submission(
        self, *, idempotency_key: str
    ) -> Iterator[Callable[[RunRecord], RunRecord | None]]:
        with self._submission_lock:
            if idempotency_key in self._idempotency:
                raise DuplicateIdempotencyKeyError()
            yield partial(self._save_run, idempotency_key=idempotency_key)

    def _save_run(self, record: RunRecord, *, idempotency_key: str) -> RunRecord | None:
        if record.run_id in self._records:
            return None
        self._idempotency[idempotency_key] = record.run_id
        self._records[record.run_id] = record
        self._events[record.run_id] = [
            RunEvent(
                event_id=_default_event_id(),
                run_id=record.run_id,
                sequence=1,
                event_type="run.created",
                payload={
                    "run_id": record.run_id,
                    "run_type": record.run_type,
                    "status": record.status.value,
                    "input": deepcopy(record.input),
                },
                created_at=record.created_at,
            )
        ]
        return record

    def get(self, run_id: str) -> RunRecord | None:
        return self._records.get(run_id)

    def list_events(self, run_id: str) -> list[RunEvent] | None:
        if run_id not in self._records:
            return None
        return sorted(self._events.get(run_id, []), key=lambda event: event.sequence)

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunEvent | None:
        if run_id not in self._records:
            return None
        events = self._events.setdefault(run_id, [])
        sequence = max((event.sequence for event in events), default=0) + 1
        event = RunEvent(
            event_id=_default_event_id(),
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            payload=deepcopy(payload),
            created_at=datetime.now(UTC),
        )
        events.append(event)
        return event

    def get_trace(self, run_id: str) -> RunTrace | None:
        record = self._records.get(run_id)
        if record is None:
            return None
        return RunTrace(
            run=record,
            events=self.list_events(run_id) or [],
        )

    def count(self) -> int:
        return len(self._records)


class RunAPI:
    def __init__(
        self,
        store: RunStore,
        executor: Executor,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._id_factory = id_factory or default_run_id

    def _new_run_id(self) -> str:
        return self._id_factory()

    def create_run(self, request: CreateRunRequest) -> RunResponse:
        null_path = find_null_character(request.input)
        if null_path is not None:
            raise ValidationError("input", f"在 {null_path} 包含 U+0000 字符")
        record = self.submit_run(
            request.input.get("query"),
            run_type=request.run_type,
            run_input=request.input,
            idempotency_key=request.idempotency_key,
        )
        return _run_response(record)

    def query_run(self, run_id: str) -> RunResponse | None:
        record = self.get_run(run_id)
        return _run_response(record) if record is not None else None

    def list_events(self, run_id: str) -> list[RunEvent] | None:
        return self._store.list_events(run_id)

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunEvent | None:
        return self._store.append_event(run_id, event_type, payload)

    def get_trace(self, run_id: str) -> RunTrace | None:
        return self._store.get_trace(run_id)

    def submit_run(
        self,
        request: object,
        *,
        run_type: str = "text",
        run_input: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> RunRecord:
        text = validate_request(request)
        input_data = deepcopy(run_input) if run_input is not None else {"query": text}
        null_path = find_null_character(input_data)
        if null_path is not None:
            raise ValidationError("input", f"在 {null_path} 包含 U+0000 字符")
        try:
            validate_json_value(input_data)
        except ValueError as exc:
            raise ValidationError("input", str(exc)) from exc

        key = idempotency_key or f"key_{secrets.token_hex(12)}"
        with self._store.submission(idempotency_key=key) as save:
            created_at = datetime.now(UTC)
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
                run_id=self._new_run_id(),
                request=text,
                status=RunStatus.SUCCEEDED if outcome.ok else RunStatus.FAILED,
                result=outcome.result if outcome.ok else None,
                error=outcome.error if not outcome.ok else None,
                run_type=run_type,
                input=input_data,
                created_at=created_at,
                started_at=started_at,
                ended_at=datetime.now(UTC),
            )

            for _ in range(MAX_ID_ATTEMPTS):
                saved_record = save(record)
                if saved_record is not None:
                    return saved_record
                record = replace(record, run_id=self._new_run_id())
            raise RunStoreError("无法分配唯一的运行 ID，未保存运行记录")

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._store.get(run_id)
