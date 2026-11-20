from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL_ENV = "CODEAGENT_DATABASE_URL"
DATABASE_SCHEMA_ENV = "CODEAGENT_DATABASE_SCHEMA"
DEFAULT_ORG_ID = "local-org"
DEFAULT_USER_ID = "local-user"
_SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class DatabaseError(RuntimeError):
    """Base class for database configuration and startup failures."""


class DatabaseConfigError(DatabaseError):
    pass


class DatabaseUnavailable(DatabaseError):
    pass


class DatabaseNotReady(DatabaseError):
    pass


def get_database_url() -> str:
    url = os.environ.get(DATABASE_URL_ENV, "").strip()
    if not url:
        raise DatabaseConfigError(
            f"未配置 {DATABASE_URL_ENV}。示例："
            "postgresql+psycopg://<用户>:<密码>@<主机>:<端口>/<数据库>"
        )
    try:
        parsed = make_url(url)
    except Exception as exc:  # SQLAlchemy raises ArgumentError for malformed URLs.
        raise DatabaseConfigError(f"{DATABASE_URL_ENV} 格式无效：{exc}") from exc
    if not parsed.drivername.startswith("postgresql"):
        raise DatabaseConfigError(
            f"{DATABASE_URL_ENV} 必须使用 PostgreSQL 连接串（postgresql+psycopg://...）"
        )
    return url


def get_database_schema() -> str | None:
    schema = os.environ.get(DATABASE_SCHEMA_ENV, "").strip()
    if not schema:
        return None
    if _SCHEMA_PATTERN.fullmatch(schema) is None:
        raise DatabaseConfigError(
            f"{DATABASE_SCHEMA_ENV} 只能包含字母、数字和下划线，且不能以数字开头。"
        )
    return schema


def get_identity() -> tuple[str, str]:
    org_id = os.environ.get("CODEAGENT_ORG_ID", "").strip() or DEFAULT_ORG_ID
    user_id = os.environ.get("CODEAGENT_USER_ID", "").strip() or DEFAULT_USER_ID
    return org_id, user_id


def create_database_engine(
    url: str | None = None,
    *,
    schema: str | None = None,
) -> Engine:
    database_url = url or get_database_url()
    connect_args: dict[str, object] = {"connect_timeout": 10}
    target_schema = schema if schema is not None else get_database_schema()
    if target_schema is not None:
        connect_args["options"] = f"-c search_path={target_schema}"
    try:
        return create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
    except (SQLAlchemyError, TypeError, ValueError) as exc:
        raise DatabaseConfigError(f"无法创建数据库引擎：{exc}") from exc


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@lru_cache(maxsize=1)
def get_alembic_head() -> str:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise DatabaseNotReady("仓库中没有可用的 Alembic 迁移版本。")
    return head


def get_database_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def ensure_database_ready(engine: Engine) -> None:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            current = MigrationContext.configure(connection).get_current_revision()
    except SQLAlchemyError as exc:
        raise DatabaseUnavailable(f"无法连接数据库：{exc}") from exc

    head = get_alembic_head()
    if current != head:
        current_label = current or "无"
        raise DatabaseNotReady(
            f"数据库迁移版本未就绪（当前：{current_label}，期望：{head}）。"
            "请先运行 python -m alembic upgrade head"
        )


@dataclass
class Database:
    engine: Engine
    session_factory: sessionmaker[Session]

    @classmethod
    def from_env(cls) -> "Database":
        engine = create_database_engine()
        return cls(engine=engine, session_factory=create_session_factory(engine))

    def ensure_ready(self) -> None:
        ensure_database_ready(self.engine)

    def close(self) -> None:
        self.engine.dispose()
