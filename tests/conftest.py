from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from pg_helpers import (
    create_schema,
    drop_schema,
    engine_for_schema,
    make_store,
    migrate_schema,
    new_schema_name,
    session_factory_for,
    test_database_url,
    truncate_all,
)


@pytest.fixture(scope="session")
def pg_url() -> str:
    url = test_database_url()
    if not url:
        pytest.skip("未配置 CODEAGENT_TEST_DATABASE_URL，跳过 PostgreSQL 集成测试")
    return url


@pytest.fixture(scope="session")
def pg16_url(pg_url: str) -> str:
    engine = engine_for_schema(pg_url, "public")
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SHOW server_version_num")).scalar_one()
    finally:
        engine.dispose()
    if not str(version).startswith("16"):
        pytest.skip(f"本章验收要求 PostgreSQL 16，当前 server_version_num={version}")
    return pg_url


@pytest.fixture(scope="session")
def migrated_schema(pg16_url: str) -> str:
    schema = new_schema_name()
    create_schema(pg16_url, schema)
    try:
        migrate_schema(pg16_url, schema)
        yield schema
    finally:
        drop_schema(pg16_url, schema)


@pytest.fixture()
def fresh_schema(pg16_url: str) -> str:
    schema = new_schema_name("codeagent_fresh")
    create_schema(pg16_url, schema)
    try:
        migrate_schema(pg16_url, schema)
        yield schema
    finally:
        drop_schema(pg16_url, schema)


@pytest.fixture()
def pg_store(migrated_schema: str, pg16_url: str) -> SimpleNamespace:
    engine = engine_for_schema(pg16_url, migrated_schema)
    session_factory = session_factory_for(engine)
    namespace = SimpleNamespace(
        url=pg16_url,
        schema=migrated_schema,
        engine=engine,
        session_factory=session_factory,
        store=make_store(session_factory),
    )
    try:
        yield namespace
    finally:
        try:
            truncate_all(engine)
        finally:
            engine.dispose()
