from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, Callable

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from .db_schema import (
    agent_runs,
    agent_steps,
    checkpoints,
    model_calls,
    run_events,
    tool_calls,
)
from .model import (
    DuplicateIdempotencyKeyError,
    RunEvent,
    RunRecord,
    RunStoreError,
    RunTrace,
)
from .schemas import RunStatus

RUN_CREATED = "run.created"
IDEMPOTENCY_CONSTRAINT = "uq_agent_runs_org_idempotency"


def _default_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _to_utc(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return value


def _error_reason(exc: SQLAlchemyError) -> str:
    original = getattr(exc, "orig", None)
    return str(original if original is not None else exc).strip()


class PostgresRunStore:
    """All PostgreSQL reads and writes for one organization."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        org_id: str,
        user_id: str,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.org_id = org_id
        self.user_id = user_id
        self._id_factory = id_factory or _default_id

    def add(self, record: RunRecord, *, idempotency_key: str) -> bool:
        session = self._session_factory()
        try:
            with session.begin():
                existing_id = session.scalar(
                    sa.select(agent_runs.c.id)
                    .where(agent_runs.c.org_id == self.org_id)
                    .where(agent_runs.c.idempotency_key == idempotency_key)
                )
                if existing_id is not None:
                    raise DuplicateIdempotencyKeyError(
                        "同一组织已使用该 idempotency_key，已保留原有运行记录"
                    )
                statement = (
                    pg_insert(agent_runs)
                    .values(
                        id=record.run_id,
                        org_id=self.org_id,
                        user_id=self.user_id,
                        run_type=record.run_type,
                        status=record.status.value,
                        idempotency_key=idempotency_key,
                        input_json=record.input,
                        output_json=(
                            {"text": record.result} if record.result is not None else None
                        ),
                        error_json=(
                            {"code": "RUN_FAILED", "message": record.error}
                            if record.error is not None
                            else None
                        ),
                        created_at=record.created_at,
                        started_at=record.started_at,
                        ended_at=record.ended_at,
                        updated_at=record.ended_at or datetime.now(UTC),
                    )
                    .on_conflict_do_nothing(index_elements=[agent_runs.c.id])
                    .returning(agent_runs.c.id)
                )
                inserted = session.execute(statement).scalar_one_or_none()
                if inserted is None:
                    return False
                self._insert_created_event(session, record)
            return True
        except IntegrityError as exc:
            constraint_name = getattr(
                getattr(exc.orig, "diag", None),
                "constraint_name",
                None,
            )
            if constraint_name == IDEMPOTENCY_CONSTRAINT or IDEMPOTENCY_CONSTRAINT in str(exc):
                raise DuplicateIdempotencyKeyError(
                    "同一组织已使用该 idempotency_key，已保留原有运行记录"
                ) from exc
            raise RunStoreError(f"保存运行记录失败：{_error_reason(exc)}") from exc
        except SQLAlchemyError as exc:
            raise RunStoreError(f"保存运行记录失败：{_error_reason(exc)}") from exc
        finally:
            session.close()

    def _insert_created_event(self, session: Session, record: RunRecord) -> None:
        session.execute(
            sa.insert(run_events).values(
                id=self._id_factory("evt"),
                run_id=record.run_id,
                sequence=1,
                event_type=RUN_CREATED,
                payload_json={
                    "run_id": record.run_id,
                    "run_type": record.run_type,
                    "status": record.status.value,
                    "input": record.input,
                },
                created_at=record.created_at,
            )
        )

    def get(self, run_id: str) -> RunRecord | None:
        session = self._session_factory()
        try:
            row = (
                session.execute(
                    sa.select(agent_runs)
                    .where(agent_runs.c.org_id == self.org_id)
                    .where(agent_runs.c.id == run_id)
                )
                .mappings()
                .one_or_none()
            )
            return self._record_from_row(row) if row is not None else None
        except SQLAlchemyError as exc:
            raise RunStoreError(f"查询运行记录失败：{_error_reason(exc)}") from exc
        finally:
            session.close()

    def list_events(self, run_id: str) -> list[RunEvent] | None:
        session = self._session_factory()
        try:
            if not self._run_exists(session, run_id):
                return None
            rows = (
                session.execute(
                    sa.select(run_events)
                    .where(run_events.c.run_id == run_id)
                    .order_by(run_events.c.sequence)
                )
                .mappings()
                .all()
            )
            return [self._event_from_row(row) for row in rows]
        except SQLAlchemyError as exc:
            raise RunStoreError(f"查询运行事件失败：{_error_reason(exc)}") from exc
        finally:
            session.close()

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> RunEvent | None:
        session = self._session_factory()
        try:
            with session.begin():
                locked = session.execute(
                    sa.select(agent_runs.c.id)
                    .where(agent_runs.c.org_id == self.org_id)
                    .where(agent_runs.c.id == run_id)
                    .with_for_update()
                ).scalar_one_or_none()
                if locked is None:
                    return None

                sequence = (
                    session.scalar(
                        sa.select(sa.func.max(run_events.c.sequence)).where(
                            run_events.c.run_id == run_id
                        )
                    )
                    or 0
                ) + 1
                event = RunEvent(
                    event_id=self._id_factory("evt"),
                    run_id=run_id,
                    sequence=sequence,
                    event_type=event_type,
                    payload=payload,
                    created_at=datetime.now(UTC),
                )
                session.execute(
                    sa.insert(run_events).values(
                        id=event.event_id,
                        run_id=event.run_id,
                        sequence=event.sequence,
                        event_type=event.event_type,
                        payload_json=event.payload,
                        created_at=event.created_at,
                    )
                )
            return event
        except SQLAlchemyError as exc:
            raise RunStoreError(f"保存运行事件失败：{_error_reason(exc)}") from exc
        finally:
            session.close()

    def get_trace(self, run_id: str) -> RunTrace | None:
        session = self._session_factory()
        try:
            row = (
                session.execute(
                    sa.select(agent_runs)
                    .where(agent_runs.c.org_id == self.org_id)
                    .where(agent_runs.c.id == run_id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            record = self._record_from_row(row)

            steps = self._rows(
                session,
                sa.select(agent_steps)
                .where(agent_steps.c.run_id == run_id)
                .order_by(agent_steps.c.step_index),
            )
            events = [
                self._event_from_row(event_row)
                for event_row in self._rows(
                    session,
                    sa.select(run_events)
                    .where(run_events.c.run_id == run_id)
                    .order_by(run_events.c.sequence),
                )
            ]
            calls = self._rows(
                session,
                sa.select(model_calls)
                .where(model_calls.c.run_id == run_id)
                .order_by(model_calls.c.created_at, model_calls.c.id),
            )
            tools = self._rows(
                session,
                sa.select(tool_calls)
                .where(tool_calls.c.run_id == run_id)
                .order_by(tool_calls.c.created_at, tool_calls.c.id),
            )
            saved_checkpoints = self._rows(
                session,
                sa.select(checkpoints)
                .where(checkpoints.c.run_id == run_id)
                .order_by(checkpoints.c.checkpoint_index),
            )
            return RunTrace(
                run=record,
                steps=steps,
                events=events,
                model_calls=calls,
                tool_calls=tools,
                checkpoints=saved_checkpoints,
            )
        except SQLAlchemyError as exc:
            raise RunStoreError(f"查询执行轨迹失败：{_error_reason(exc)}") from exc
        finally:
            session.close()

    @staticmethod
    def _rows(session: Session, statement: Any) -> list[dict[str, Any]]:
        return [
            {key: _to_utc(value) for key, value in row.items()}
            for row in session.execute(statement).mappings().all()
        ]

    def _run_exists(self, session: Session, run_id: str) -> bool:
        return (
            session.execute(
                sa.select(agent_runs.c.id)
                .where(agent_runs.c.org_id == self.org_id)
                .where(agent_runs.c.id == run_id)
            ).first()
            is not None
        )

    @staticmethod
    def _record_from_row(row: Any) -> RunRecord:
        input_data = row["input_json"]
        if not isinstance(input_data, dict):
            input_data = {"value": input_data}
        output = row["output_json"]
        error = row["error_json"]
        result = output.get("text") if isinstance(output, dict) else None
        error_message = error.get("message") if isinstance(error, dict) else None
        query = input_data.get("query")
        return RunRecord(
            run_id=row["id"],
            request=query if isinstance(query, str) else "",
            status=RunStatus(row["status"]),
            result=result if isinstance(result, str) else None,
            error=error_message if isinstance(error_message, str) else None,
            run_type=row["run_type"],
            input=dict(input_data),
            created_at=_to_utc(row["created_at"]),
            started_at=_to_utc(row["started_at"]),
            ended_at=_to_utc(row["ended_at"]),
        )

    @staticmethod
    def _event_from_row(row: Any) -> RunEvent:
        return RunEvent(
            event_id=row["id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            event_type=row["event_type"],
            payload=row["payload_json"],
            created_at=_to_utc(row["created_at"]),
        )
