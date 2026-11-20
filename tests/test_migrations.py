from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text

from codeagent_v2.database import (
    DatabaseNotReady,
    create_session_factory,
    ensure_database_ready,
)
from codeagent_v2.db_schema import metadata
from codeagent_v2.model import RunRecord
from codeagent_v2.repository import PostgresRunStore
from codeagent_v2.schemas import RunStatus
from pg_helpers import (
    alembic_command,
    create_schema,
    drop_schema,
    engine_for_schema,
    make_store,
    new_schema_name,
    run_cli,
)


EXPECTED_COLUMNS = {
    "agent_runs": [
        ("id", "TEXT", False, None),
        ("org_id", "TEXT", False, None),
        ("user_id", "TEXT", False, None),
        ("run_type", "TEXT", False, None),
        ("status", "TEXT", False, None),
        ("idempotency_key", "TEXT", False, None),
        ("input_json", "JSONB", False, None),
        ("output_json", "JSONB", True, None),
        ("error_json", "JSONB", True, None),
        ("created_at", "TIMESTAMP", False, "now()"),
        ("started_at", "TIMESTAMP", True, None),
        ("ended_at", "TIMESTAMP", True, None),
        ("updated_at", "TIMESTAMP", False, "now()"),
    ],
    "agent_steps": [
        ("id", "TEXT", False, None),
        ("run_id", "TEXT", False, None),
        ("step_index", "INTEGER", False, None),
        ("step_type", "TEXT", False, None),
        ("name", "TEXT", False, None),
        ("status", "TEXT", False, None),
        ("input_json", "JSONB", True, None),
        ("output_json", "JSONB", True, None),
        ("error_json", "JSONB", True, None),
        ("started_at", "TIMESTAMP", False, "now()"),
        ("ended_at", "TIMESTAMP", True, None),
    ],
    "run_events": [
        ("id", "TEXT", False, None),
        ("run_id", "TEXT", False, None),
        ("sequence", "INTEGER", False, None),
        ("event_type", "TEXT", False, None),
        ("payload_json", "JSONB", False, None),
        ("created_at", "TIMESTAMP", False, "now()"),
    ],
    "model_calls": [
        ("id", "TEXT", False, None),
        ("run_id", "TEXT", False, None),
        ("step_id", "TEXT", True, None),
        ("provider", "TEXT", False, None),
        ("model", "TEXT", False, None),
        ("request_json", "JSONB", False, None),
        ("response_json", "JSONB", True, None),
        ("prompt_tokens", "INTEGER", True, None),
        ("completion_tokens", "INTEGER", True, None),
        ("latency_ms", "INTEGER", True, None),
        ("status", "TEXT", False, None),
        ("error_json", "JSONB", True, None),
        ("created_at", "TIMESTAMP", False, "now()"),
    ],
    "tool_calls": [
        ("id", "TEXT", False, None),
        ("run_id", "TEXT", False, None),
        ("step_id", "TEXT", True, None),
        ("tool_name", "TEXT", False, None),
        ("args_json", "JSONB", False, None),
        ("result_json", "JSONB", True, None),
        ("status", "TEXT", False, None),
        ("idempotency_key", "TEXT", True, None),
        ("latency_ms", "INTEGER", True, None),
        ("error_json", "JSONB", True, None),
        ("created_at", "TIMESTAMP", False, "now()"),
        ("ended_at", "TIMESTAMP", True, None),
    ],
    "checkpoints": [
        ("id", "TEXT", False, None),
        ("run_id", "TEXT", False, None),
        ("checkpoint_index", "INTEGER", False, None),
        ("state_json", "JSONB", False, None),
        ("created_at", "TIMESTAMP", False, "now()"),
    ],
    "usage_records": [
        ("id", "TEXT", False, None),
        ("run_id", "TEXT", False, None),
        ("org_id", "TEXT", False, None),
        ("user_id", "TEXT", False, None),
        ("provider", "TEXT", False, None),
        ("model", "TEXT", True, None),
        ("prompt_tokens", "INTEGER", True, "0"),
        ("completion_tokens", "INTEGER", True, "0"),
        ("total_tokens", "INTEGER", True, "0"),
        ("estimated_cost_cents", "INTEGER", True, "0"),
        ("created_at", "TIMESTAMP", False, "now()"),
    ],
    "audit_logs": [
        ("id", "TEXT", False, None),
        ("org_id", "TEXT", False, None),
        ("user_id", "TEXT", False, None),
        ("run_id", "TEXT", True, None),
        ("action", "TEXT", False, None),
        ("target_type", "TEXT", False, None),
        ("target_id", "TEXT", False, None),
        ("metadata_json", "JSONB", True, None),
        ("created_at", "TIMESTAMP", False, "now()"),
    ],
}

EXPECTED_FOREIGN_KEYS = {
    "agent_steps": [("run_id", "agent_runs", "id")],
    "run_events": [("run_id", "agent_runs", "id")],
    "model_calls": [
        ("run_id", "agent_runs", "id"),
        ("step_id", "agent_steps", "id"),
    ],
    "tool_calls": [
        ("run_id", "agent_runs", "id"),
        ("step_id", "agent_steps", "id"),
    ],
    "checkpoints": [("run_id", "agent_runs", "id")],
    "usage_records": [("run_id", "agent_runs", "id")],
    "audit_logs": [("run_id", "agent_runs", "id")],
}

EXPECTED_UNIQUE = {
    "agent_runs": [("org_id", "idempotency_key")],
    "agent_steps": [("run_id", "step_index")],
    "run_events": [("run_id", "sequence")],
    "tool_calls": [("tool_name", "idempotency_key")],
    "checkpoints": [("run_id", "checkpoint_index")],
}

EXPECTED_INDEXES = {
    "idx_agent_runs_org_created": "(org_id, created_at DESC)",
    "idx_agent_runs_status_created": "(status, created_at)",
    "idx_agent_steps_run_index": "(run_id, step_index)",
    "idx_run_events_run_sequence": "(run_id, sequence)",
    "idx_model_calls_run_created": "(run_id, created_at)",
    "idx_tool_calls_run_created": "(run_id, created_at)",
    "idx_tool_calls_tool_status": "(tool_name, status, created_at)",
    "idx_usage_records_org_created": "(org_id, created_at)",
    "idx_audit_logs_org_created": "(org_id, created_at)",
}


def test_empty_schema_migration_matches_metadata_and_is_repeatable(
    pg16_url: str,
    migrated_schema: str,
) -> None:
    engine = engine_for_schema(pg16_url, migrated_schema)
    try:
        inspector = inspect(engine)
        actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert actual_tables == set(EXPECTED_COLUMNS)
        assert set(metadata.tables) == set(EXPECTED_COLUMNS)

        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            columns = inspector.get_columns(table_name)
            actual = [
                (column["name"], str(column["type"]), column["nullable"], column["default"])
                for column in columns
            ]
            assert actual == expected_columns, table_name
            for column in columns:
                if column["name"].endswith("_at"):
                    assert column["type"].timezone is True, f"{table_name}.{column['name']}"

            primary_key = inspector.get_pk_constraint(table_name)
            assert primary_key["constrained_columns"] == ["id"], table_name

            actual_foreign_keys = {
                (
                    foreign_key["constrained_columns"][0],
                    foreign_key["referred_table"],
                    foreign_key["referred_columns"][0],
                )
                for foreign_key in inspector.get_foreign_keys(table_name)
            }
            assert actual_foreign_keys == set(
                EXPECTED_FOREIGN_KEYS.get(table_name, [])
            ), table_name

            actual_unique = {
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table_name)
            }
            assert actual_unique == set(EXPECTED_UNIQUE.get(table_name, [])), table_name

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = :schema AND indexname LIKE 'idx_%'"
                ),
                {"schema": migrated_schema},
            ).all()
        index_definitions = {row[0]: row[1] for row in rows}
        assert set(index_definitions) == set(EXPECTED_INDEXES)
        for index_name, expected_definition in EXPECTED_INDEXES.items():
            assert expected_definition in index_definitions[index_name], index_name
        assert sum(
            1 for table in metadata.tables.values() for _ in table.indexes
        ) == 9

        check = alembic_command(migrated_schema, "check", url=pg16_url)
        assert check.returncode == 0, check.stderr
        assert "No new upgrade operations detected" in check.stdout + check.stderr

        repeat = alembic_command(migrated_schema, "upgrade", "head", url=pg16_url)
        assert repeat.returncode == 0, repeat.stderr
        current = alembic_command(migrated_schema, "current", url=pg16_url)
        assert "0001_initial_schema (head)" in current.stdout + current.stderr
    finally:
        engine.dispose()


def test_repeated_migration_preserves_existing_rows(
    pg16_url: str,
    pg_store,
) -> None:
    timestamp = datetime(2026, 11, 20, 8, 30, 0, 123456, tzinfo=UTC)
    record = RunRecord(
        run_id="run_migration_0001",
        request="迁移前记录",
        status=RunStatus.SUCCEEDED,
        result="固定执行器完成：迁移前记录",
        error=None,
        run_type="text",
        input={"query": "迁移前记录"},
        created_at=timestamp,
        started_at=timestamp,
        ended_at=timestamp,
    )
    assert pg_store.store.add(record, idempotency_key="migration-key-0001")

    result = alembic_command(pg_store.schema, "upgrade", "head", url=pg16_url)
    assert result.returncode == 0, result.stderr

    loaded = pg_store.store.get(record.run_id)
    assert loaded == record
    events = pg_store.store.list_events(record.run_id)
    assert events is not None
    assert [(event.sequence, event.event_type) for event in events] == [
        (1, "run.created")
    ]


def test_database_not_ready_before_first_migration(pg16_url: str) -> None:
    schema = new_schema_name("codeagent_empty")
    create_schema(pg16_url, schema)
    engine = engine_for_schema(pg16_url, schema)
    try:
        with pytest.raises(DatabaseNotReady):
            ensure_database_ready(engine)
    finally:
        engine.dispose()
        drop_schema(pg16_url, schema)


def test_cli_refuses_unmigrated_database(pg16_url: str) -> None:
    schema = new_schema_name("codeagent_cli_empty")
    create_schema(pg16_url, schema)
    try:
        result = run_cli(schema, ["/exit"])
        assert result.returncode == 2
        assert "数据库启动失败" in result.stderr
        assert "迁移版本未就绪" in result.stderr
        assert "python -m alembic upgrade head" in result.stderr
    finally:
        drop_schema(pg16_url, schema)


def test_migration_reports_unreachable_database() -> None:
    result = alembic_command(
        "unused",
        "upgrade",
        "head",
        url="postgresql+psycopg://postgres@127.0.0.1:1/unreachable?connect_timeout=2",
        timeout=30,
    )
    assert result.returncode != 0
    assert "unreachable" in result.stderr.lower() or "connect" in result.stderr.lower()


def test_store_session_factory_is_independent_of_engine_lifetime(pg16_url: str) -> None:
    schema = new_schema_name("codeagent_sessions")
    create_schema(pg16_url, schema)
    try:
        result = alembic_command(schema, "upgrade", "head", url=pg16_url)
        assert result.returncode == 0, result.stderr
        engine = engine_for_schema(pg16_url, schema)
        factory = create_session_factory(engine)
        store = make_store(factory)
        assert isinstance(store, PostgresRunStore)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        engine.dispose()
    finally:
        drop_schema(pg16_url, schema)
