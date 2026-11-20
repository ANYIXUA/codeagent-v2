from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterable

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from codeagent_v2.database import create_database_engine, create_session_factory
from codeagent_v2.repository import PostgresRunStore

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL_ENV = "CODEAGENT_TEST_DATABASE_URL"


def test_database_url() -> str:
    return os.environ.get(TEST_DATABASE_URL_ENV, "").strip()


def new_schema_name(prefix: str = "codeagent_test") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def admin_engine(url: str) -> Engine:
    return create_engine(url, isolation_level="AUTOCOMMIT")


def create_schema(url: str, schema: str) -> None:
    engine = admin_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        engine.dispose()


def drop_schema(url: str, schema: str) -> None:
    engine = admin_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        engine.dispose()


def alembic_command(
    schema: str,
    *arguments: str,
    url: str | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["CODEAGENT_DATABASE_URL"] = url or test_database_url()
    env["CODEAGENT_DATABASE_SCHEMA"] = schema
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=timeout,
    )


def migrate_schema(url: str, schema: str) -> None:
    result = alembic_command(schema, "upgrade", "head", url=url)
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed for schema {schema}:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def engine_for_schema(url: str, schema: str) -> Engine:
    return create_database_engine(url, schema=schema)


def session_factory_for(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


def make_store(
    session_factory: sessionmaker[Session],
    *,
    org_id: str = "org-a",
    user_id: str = "user-a",
) -> PostgresRunStore:
    return PostgresRunStore(session_factory, org_id=org_id, user_id=user_id)


def truncate_all(engine: Engine) -> None:
    statement = (
        "TRUNCATE audit_logs, usage_records, checkpoints, tool_calls, "
        "model_calls, run_events, agent_steps, agent_runs RESTART IDENTITY CASCADE"
    )
    with engine.begin() as connection:
        connection.execute(text(statement))


def cli_env(
    schema: str,
    *,
    org_id: str | None = None,
    user_id: str | None = None,
    url: str | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["CODEAGENT_DATABASE_URL"] = url or test_database_url()
    env["CODEAGENT_DATABASE_SCHEMA"] = schema
    if org_id is not None:
        env["CODEAGENT_ORG_ID"] = org_id
    else:
        env.pop("CODEAGENT_ORG_ID", None)
    if user_id is not None:
        env["CODEAGENT_USER_ID"] = user_id
    else:
        env.pop("CODEAGENT_USER_ID", None)
    return env


def run_cli(
    schema: str,
    lines: Iterable[str],
    *,
    org_id: str | None = None,
    user_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "codeagent_v2"],
        input="\n".join(lines) + "\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        cwd=REPO_ROOT,
        env=cli_env(schema, org_id=org_id, user_id=user_id),
        timeout=60,
    )
