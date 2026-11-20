from __future__ import annotations

import io
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import func, insert, select, text

from codeagent_v2.cli import run_repl
from codeagent_v2.db_schema import (
    agent_runs,
    agent_steps,
    checkpoints,
    model_calls,
    run_events,
    tool_calls,
)
from codeagent_v2.model import DuplicateIdempotencyKeyError, RunStoreError, ValidationError
from codeagent_v2.repository import PostgresRunStore
from codeagent_v2.run_api import RunAPI
from codeagent_v2.runtime import ExecutionOutcome, FixedExecutor
from codeagent_v2.schemas import CreateRunRequest, RunStatus
from pg_helpers import (
    engine_for_schema,
    make_store,
    run_cli,
    session_factory_for,
)

RUN_ID_PATTERN = re.compile(r"run_[0-9a-f]{12}")


def make_api(
    store,
    *,
    ids: list[str] | None = None,
    executor=None,
) -> RunAPI:
    if ids is None:
        id_factory = None
    else:
        iterator = iter(ids)
        id_factory = lambda: next(iterator)
    return RunAPI(store, executor or FixedExecutor(), id_factory=id_factory)


def count_rows(engine, table) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(table)).scalar_one()


def create_request(query: str, key: str) -> CreateRunRequest:
    return CreateRunRequest(run_type="text", idempotency_key=key, input={"query": query})


def test_success_round_trip_matches_response(pg_store) -> None:
    api = make_api(pg_store.store, ids=["run_pg_success_01"])
    response = api.create_run(create_request("解释任务队列", "pg-success-key-01"))

    assert response.status == RunStatus.SUCCEEDED
    assert response.output == {"text": "固定执行器完成：解释任务队列"}
    assert api.query_run(response.run_id) == response

    with pg_store.engine.connect() as connection:
        row = connection.execute(
            select(agent_runs).where(agent_runs.c.id == response.run_id)
        ).mappings().one()
        event = connection.execute(
            select(run_events).where(run_events.c.run_id == response.run_id)
        ).mappings().one()
    assert row["org_id"] == "org-a"
    assert row["user_id"] == "user-a"
    assert row["status"] == "succeeded"
    assert row["input_json"] == {"query": "解释任务队列"}
    assert row["output_json"] == {"text": "固定执行器完成：解释任务队列"}
    assert row["error_json"] is None
    assert row["created_at"] == response.created_at
    assert row["started_at"] == response.started_at
    assert row["ended_at"] == response.ended_at
    assert event["sequence"] == 1
    assert event["event_type"] == "run.created"
    assert event["payload_json"]["run_id"] == response.run_id


def test_postgres_normalized_numbers_match_in_create_and_query(pg_store) -> None:
    api = make_api(pg_store.store)
    response = api.create_run(
        CreateRunRequest(
            run_type="text",
            idempotency_key="pg-large-number-key",
            input={"query": "数值往返", "metadata": {"number": 1e30}},
        )
    )

    assert api.query_run(response.run_id) == response
    assert response.input == {"query": "数值往返", "metadata": {"number": 10**30}}


def test_failure_round_trip_matches_response(pg_store) -> None:
    api = make_api(pg_store.store, ids=["run_pg_failure_01"])
    response = api.create_run(create_request("[fail] 数据库验收", "pg-failure-key-01"))

    assert response.status == RunStatus.FAILED
    assert response.output is None
    assert response.error == {
        "code": "RUN_FAILED",
        "message": "固定执行器按要求返回失败",
    }
    assert api.query_run(response.run_id) == response

    with pg_store.engine.connect() as connection:
        row = connection.execute(
            select(agent_runs).where(agent_runs.c.id == response.run_id)
        ).mappings().one()
    assert row["status"] == "failed"
    assert row["output_json"] is None
    assert row["error_json"]["message"] == "固定执行器按要求返回失败"


def test_record_survives_new_engine_and_session(pg16_url: str, pg_store) -> None:
    api = make_api(pg_store.store, ids=["run_pg_restart_001"])
    response = api.create_run(create_request("重启后查询", "pg-restart-key-01"))

    new_engine = engine_for_schema(pg16_url, pg_store.schema)
    try:
        new_store = make_store(session_factory_for(new_engine))
        new_api = RunAPI(new_store, FixedExecutor())
        assert new_api.query_run(response.run_id) == response
    finally:
        new_engine.dispose()


def test_duplicate_idempotency_key_preserves_existing_run_and_events(pg_store) -> None:
    api = make_api(
        pg_store.store,
        ids=["run_pg_idem_00001", "run_pg_idem_00002"],
    )
    first = api.create_run(create_request("第一次提交", "pg-idempotency-key-01"))
    before = count_rows(pg_store.engine, run_events)

    with pytest.raises(DuplicateIdempotencyKeyError):
        api.create_run(create_request("第二次提交", "pg-idempotency-key-01"))

    assert count_rows(pg_store.engine, agent_runs) == 1
    assert count_rows(pg_store.engine, run_events) == before
    assert api.query_run(first.run_id) == first
    events = api.list_events(first.run_id)
    assert events is not None
    assert [(event.sequence, event.event_type) for event in events] == [
        (1, "run.created")
    ]
    assert events[0].payload["input"] == {"query": "第一次提交"}


def test_concurrent_duplicate_idempotency_key_executes_once(pg_store) -> None:
    ready = Barrier(2)
    executed_requests = []

    class CountingExecutor:
        def execute(self, request: str) -> ExecutionOutcome:
            executed_requests.append(request)
            return FixedExecutor().execute(request)

    def submit_duplicate(query: str) -> str | None:
        store = make_store(pg_store.session_factory)
        api = RunAPI(store, CountingExecutor())
        ready.wait(timeout=5)
        try:
            return api.create_run(create_request(query, "pg-concurrent-duplicate")).run_id
        except DuplicateIdempotencyKeyError:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        run_ids = list(workers.map(submit_duplicate, ["并发请求甲", "并发请求乙"]))

    assert run_ids.count(None) == 1
    assert len(executed_requests) == 1
    assert count_rows(pg_store.engine, agent_runs) == 1
    assert count_rows(pg_store.engine, run_events) == 1


def test_primary_key_conflict_retries_and_executes_once(pg_store) -> None:
    first_api = make_api(pg_store.store, ids=["run_pg_conflict_01"])
    first = first_api.create_run(create_request("原始运行", "pg-conflict-key-01"))

    calls = 0

    class CountingExecutor:
        def execute(self, request: str) -> ExecutionOutcome:
            nonlocal calls
            calls += 1
            return FixedExecutor().execute(request)

    second_api = make_api(
        pg_store.store,
        ids=["run_pg_conflict_01", "run_pg_conflict_02"],
        executor=CountingExecutor(),
    )
    second = second_api.create_run(create_request("冲突后重试", "pg-conflict-key-02"))

    assert calls == 1
    assert second.run_id == "run_pg_conflict_02"
    assert count_rows(pg_store.engine, agent_runs) == 2
    assert count_rows(pg_store.engine, run_events) == 2
    assert second_api.query_run(first.run_id) == first


def test_null_character_is_rejected_before_execution_and_literal_is_saved(pg_store) -> None:
    calls = 0

    class CountingExecutor:
        def execute(self, request: str) -> ExecutionOutcome:
            nonlocal calls
            calls += 1
            return FixedExecutor().execute(request)

    api = make_api(
        pg_store.store,
        ids=["run_pg_null_00001", "run_pg_null_00002"],
        executor=CountingExecutor(),
    )
    invalid = create_request("空字符", "pg-null-key-0001")
    invalid.input["metadata"] = {"nested": ["ok", "bad\x00value"]}
    with pytest.raises(ValidationError):
        api.create_run(invalid)
    assert calls == 0
    assert count_rows(pg_store.engine, agent_runs) == 0

    literal = "保留 \\u0000 文本"
    response = api.create_run(
        CreateRunRequest(
            run_type="text",
            idempotency_key="pg-null-key-0002",
            input={"query": literal, "metadata": {"key": literal}},
        )
    )
    stored = api.query_run(response.run_id)
    assert stored is not None
    assert stored.input["query"] == literal
    assert stored.input["metadata"]["key"] == literal


def test_events_created_then_appended_in_order(pg_store) -> None:
    api = make_api(pg_store.store, ids=["run_pg_events_001"])
    record = api.create_run(create_request("事件顺序", "pg-events-key-01"))

    api.append_event(record.run_id, "step.started", {"step": 0})
    api.append_event(record.run_id, "step.finished", {"step": 0})

    events = api.list_events(record.run_id)
    assert events is not None
    assert [(event.sequence, event.event_type) for event in events] == [
        (1, "run.created"),
        (2, "step.started"),
        (3, "step.finished"),
    ]


def test_concurrent_event_append_allocates_unique_increasing_sequences(pg_store) -> None:
    api = make_api(pg_store.store, ids=["run_pg_concurrent_1"])
    record = api.create_run(create_request("并发追加", "pg-concurrent-key-1"))
    workers = 12

    def append(index: int) -> int:
        store = PostgresRunStore(
            pg_store.session_factory,
            org_id="org-a",
            user_id="user-a",
        )
        event = store.append_event(record.run_id, "worker.event", {"index": index})
        assert event is not None
        return event.sequence

    with ThreadPoolExecutor(max_workers=workers) as pool:
        sequences = list(pool.map(append, range(workers)))

    assert sorted(sequences) == list(range(2, workers + 2))
    assert len(set(sequences)) == workers
    events = api.list_events(record.run_id)
    assert events is not None
    assert [event.sequence for event in events] == list(range(1, workers + 2))
    assert events[0].event_type == "run.created"


def test_trace_aggregates_only_the_requested_run(pg_store) -> None:
    first_api = make_api(
        pg_store.store,
        ids=["run_pg_trace_00001", "run_pg_trace_00002"],
    )
    first = first_api.create_run(create_request("第一个轨迹", "pg-trace-key-01"))
    second = first_api.create_run(create_request("第二个轨迹", "pg-trace-key-02"))
    now = datetime.now(UTC)

    with pg_store.session_factory.begin() as session:
        session.execute(
            insert(agent_steps).values(
                id="step_trace_0001",
                run_id=first.run_id,
                step_index=0,
                step_type="plan",
                name="制定计划",
                status="succeeded",
                input_json={"query": "第一个轨迹"},
                output_json={"plan": ["step"]},
                started_at=now,
                ended_at=now,
            )
        )
        session.execute(
            insert(agent_steps).values(
                id="step_trace_0002",
                run_id=second.run_id,
                step_index=0,
                step_type="plan",
                name="另一个运行",
                status="succeeded",
                started_at=now,
                ended_at=now,
            )
        )
        session.execute(
            insert(model_calls).values(
                id="call_trace_0001",
                run_id=first.run_id,
                step_id="step_trace_0001",
                provider="fixed",
                model="fixed-model",
                request_json={"prompt": "hello"},
                response_json={"text": "world"},
                prompt_tokens=3,
                completion_tokens=2,
                latency_ms=5,
                status="succeeded",
                created_at=now,
            )
        )
        session.execute(
            insert(tool_calls).values(
                id="tool_trace_0001",
                run_id=first.run_id,
                step_id="step_trace_0001",
                tool_name="read_file",
                args_json={"path": "README.md"},
                result_json={"ok": True},
                status="succeeded",
                idempotency_key="tool-trace-key-1",
                latency_ms=2,
                created_at=now,
                ended_at=now,
            )
        )
        session.execute(
            insert(checkpoints).values(
                id="ckpt_trace_0001",
                run_id=first.run_id,
                checkpoint_index=0,
                state_json={"step": 0},
                created_at=now,
            )
        )
    first_api.append_event(first.run_id, "trace.seeded", {"run": "first"})

    trace = first_api.get_trace(first.run_id)
    assert trace is not None
    assert trace.run.run_id == first.run_id
    assert trace.run.status == first.status
    assert trace.run.input == first.input
    assert trace.run.result == first.output["text"]
    assert trace.run.created_at == first.created_at
    assert trace.run.started_at == first.started_at
    assert trace.run.ended_at == first.ended_at
    assert [step["id"] for step in trace.steps] == ["step_trace_0001"]
    assert [call["id"] for call in trace.model_calls] == ["call_trace_0001"]
    assert [call["id"] for call in trace.tool_calls] == ["tool_trace_0001"]
    assert [checkpoint["id"] for checkpoint in trace.checkpoints] == ["ckpt_trace_0001"]
    assert [event.sequence for event in trace.events] == [1, 2]
    assert trace.events[-1].event_type == "trace.seeded"

    second_trace = first_api.get_trace(second.run_id)
    assert second_trace is not None
    assert [step["id"] for step in second_trace.steps] == ["step_trace_0002"]
    assert second_trace.model_calls == []
    assert second_trace.tool_calls == []
    assert second_trace.checkpoints == []


def test_queries_are_scoped_by_organization(pg_store) -> None:
    api = make_api(pg_store.store, ids=["run_pg_org_000001"])
    response = api.create_run(create_request("组织隔离", "pg-org-key-0001"))
    other_store = make_store(pg_store.session_factory, org_id="org-b")
    other_api = RunAPI(other_store, FixedExecutor())

    assert other_api.query_run(response.run_id) is None
    assert other_api.list_events(response.run_id) is None
    assert other_api.get_trace(response.run_id) is None
    assert other_api.append_event(response.run_id, "should.not.write", {}) is None
    assert count_rows(pg_store.engine, agent_runs) == 1
    assert count_rows(pg_store.engine, run_events) == 1


def test_created_event_failure_rolls_back_run(pg_store) -> None:
    api = make_api(pg_store.store, ids=["run_pg_rollback_01"])
    normal = api.create_run(create_request("正常提交", "pg-rollback-key-01"))
    with pg_store.engine.connect() as connection:
        assert connection.execute(
            select(func.count()).select_from(agent_runs).where(
                agent_runs.c.id == normal.run_id
            )
        ).scalar_one() == 1
        assert connection.execute(
            select(func.count()).select_from(run_events).where(
                run_events.c.run_id == normal.run_id,
                run_events.c.sequence == 1,
            )
        ).scalar_one() == 1

    with pg_store.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE FUNCTION fail_run_events() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'forced run_events failure';
                END;
                $$;
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER fail_run_events_trigger
                BEFORE INSERT ON run_events
                FOR EACH ROW EXECUTE FUNCTION fail_run_events();
                """
            )
        )

    try:
        failing_api = make_api(pg_store.store, ids=["run_pg_rollback_02"])
        with pytest.raises(RunStoreError):
            failing_api.create_run(create_request("事件失败", "pg-rollback-key-02"))
    finally:
        with pg_store.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER IF EXISTS fail_run_events_trigger ON run_events")
            )
            connection.execute(text("DROP FUNCTION IF EXISTS fail_run_events()"))

    assert count_rows(pg_store.engine, agent_runs) == 1
    assert count_rows(pg_store.engine, run_events) == 1
    assert failing_api.query_run("run_pg_rollback_02") is None


def test_cli_two_processes_and_events(pg_store) -> None:
    first = run_cli(
        pg_store.schema,
        ["/run 解释任务队列", "/exit"],
        org_id="org-a",
    )
    assert first.returncode == 0, first.stderr
    match = RUN_ID_PATTERN.search(first.stdout)
    assert match is not None, first.stdout
    run_id = match.group(0)

    store = make_store(pg_store.session_factory)
    store.append_event(run_id, "step.started", {"step": 0})
    store.append_event(run_id, "step.finished", {"step": 0})

    second = run_cli(
        pg_store.schema,
        [f"/get {run_id}", f"/events {run_id}", "/exit"],
        org_id="org-a",
    )
    assert second.returncode == 0, second.stderr
    assert f"运行：{run_id}" in second.stdout
    assert "状态：执行成功" in second.stdout
    assert "结果：固定执行器完成：解释任务队列" in second.stdout
    assert f"事件：{run_id}" in second.stdout
    event_lines = [
        line for line in second.stdout.splitlines() if re.match(r"^\d+\. ", line)
    ]
    assert [line.split(".")[0] for line in event_lines] == ["1", "2", "3"]
    assert event_lines[0].startswith("1. run.created")
    assert event_lines[1].startswith("2. step.started")
    assert event_lines[2].startswith("3. step.finished")


def test_cli_failure_run_is_queryable_after_restart(pg_store) -> None:
    first = run_cli(pg_store.schema, ["/run [fail] 数据库验收", "/exit"])
    assert first.returncode == 0, first.stderr
    match = RUN_ID_PATTERN.search(first.stdout)
    assert match is not None
    run_id = match.group(0)
    assert "状态：执行失败" in first.stdout
    assert "原因：固定执行器按要求返回失败" in first.stdout

    second = run_cli(pg_store.schema, [f"/get {run_id}", "/exit"])
    assert second.returncode == 0, second.stderr
    assert f"运行：{run_id}" in second.stdout
    assert "状态：执行失败" in second.stdout
    assert "原因：固定执行器按要求返回失败" in second.stdout


def test_cli_identity_defaults_and_environment_overrides(pg_store) -> None:
    default_run = run_cli(pg_store.schema, ["/run 默认身份", "/exit"])
    assert default_run.returncode == 0, default_run.stderr
    default_id = RUN_ID_PATTERN.search(default_run.stdout)
    assert default_id is not None

    override_run = run_cli(
        pg_store.schema,
        ["/run 指定身份", "/exit"],
        org_id="org-env",
        user_id="user-env",
    )
    assert override_run.returncode == 0, override_run.stderr
    override_id = RUN_ID_PATTERN.search(override_run.stdout)
    assert override_id is not None

    with pg_store.engine.connect() as connection:
        rows = connection.execute(
            select(
                agent_runs.c.id,
                agent_runs.c.org_id,
                agent_runs.c.user_id,
            ).order_by(agent_runs.c.id)
        ).all()
    identities = {row[0]: (row[1], row[2]) for row in rows}
    assert identities[default_id.group(0)] == ("local-org", "local-user")
    assert identities[override_id.group(0)] == ("org-env", "user-env")


def test_cli_invalid_requests_and_unknown_id(pg_store) -> None:
    result = run_cli(
        pg_store.schema,
        [
            "/run",
            "/run " + "a" * 2001,
            "/get run_missing",
            "/events run_missing",
            "/trace run_missing",
            "/exit",
        ],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("（未创建运行记录）") == 2
    assert result.stdout.count("找不到这次运行：run_missing") == 3
    assert count_rows(pg_store.engine, agent_runs) == 0


def test_cli_cross_organization_uses_same_not_found_message(pg_store) -> None:
    first = run_cli(
        pg_store.schema,
        ["/run 跨组织运行", "/exit"],
        org_id="org-a",
    )
    assert first.returncode == 0, first.stderr
    match = RUN_ID_PATTERN.search(first.stdout)
    assert match is not None
    run_id = match.group(0)

    other = run_cli(
        pg_store.schema,
        [f"/get {run_id}", f"/events {run_id}", f"/trace {run_id}", "/exit"],
        org_id="org-b",
    )
    assert other.returncode == 0, other.stderr
    assert other.stdout.count(f"找不到这次运行：{run_id}") == 3


def test_cli_trace_renders_seeded_records(pg_store) -> None:
    run_cli_org = "org-a"
    api = make_api(pg_store.store, ids=["run_pg_cli_trace_1"])
    response = api.create_run(create_request("CLI 轨迹", "pg-cli-trace-key-1"))
    now = datetime.now(UTC)
    with pg_store.session_factory.begin() as session:
        session.execute(
            insert(agent_steps).values(
                id="step_cli_trace_1",
                run_id=response.run_id,
                step_index=0,
                step_type="plan",
                name="准备",
                status="succeeded",
                started_at=now,
                ended_at=now,
            )
        )
        session.execute(
            insert(model_calls).values(
                id="model_cli_trace_1",
                run_id=response.run_id,
                step_id="step_cli_trace_1",
                provider="fixed",
                model="fixed-model",
                request_json={"prompt": "hi"},
                status="succeeded",
                created_at=now,
            )
        )
        session.execute(
            insert(tool_calls).values(
                id="tool_cli_trace_1",
                run_id=response.run_id,
                step_id="step_cli_trace_1",
                tool_name="read_file",
                args_json={"path": "README.md"},
                status="succeeded",
                idempotency_key="cli-tool-key-1",
                created_at=now,
            )
        )
        session.execute(
            insert(checkpoints).values(
                id="ckpt_cli_trace_1",
                run_id=response.run_id,
                checkpoint_index=0,
                state_json={"step": 0},
                created_at=now,
            )
        )

    result = run_cli(
        pg_store.schema,
        [f"/trace {response.run_id}", "/exit"],
        org_id=run_cli_org,
    )
    assert result.returncode == 0, result.stderr
    assert f"运行：{response.run_id}" in result.stdout
    assert "0. plan 准备 [succeeded]" in result.stdout
    assert "1. run.created" in result.stdout
    assert "model_cli_trace_1 fixed/fixed-model [succeeded]" in result.stdout
    assert "tool_cli_trace_1 read_file [succeeded]" in result.stdout
    assert "检查点：\n  0\n" in result.stdout


def test_cli_write_failure_reports_unconfirmed_and_continues(pg_store) -> None:
    with pg_store.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE FUNCTION fail_cli_events() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'forced cli event failure'
                    USING DETAIL = NEW.payload_json -> 'input' ->> 'query';
                END;
                $$;
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TRIGGER fail_cli_events_trigger
                BEFORE INSERT ON run_events
                FOR EACH ROW EXECUTE FUNCTION fail_cli_events();
                """
            )
        )
    try:
        result = run_cli(
            pg_store.schema,
            ["/run 写入故障", "/help", "/exit"],
        )
    finally:
        with pg_store.engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER IF EXISTS fail_cli_events_trigger ON run_events")
            )
            connection.execute(text("DROP FUNCTION IF EXISTS fail_cli_events()"))

    assert result.returncode == 0, result.stderr
    assert "保存未确认：" in result.stdout
    assert "forced cli event failure" not in result.stdout + result.stderr
    assert "写入故障" not in result.stdout + result.stderr
    assert "/events <run_id>" in result.stdout
    assert "再见。" in result.stdout
    assert count_rows(pg_store.engine, agent_runs) == 0
    assert count_rows(pg_store.engine, run_events) == 0


def test_cli_lock_timeout_returns_to_prompt(pg_store) -> None:
    api = make_api(pg_store.store)
    response = api.create_run(create_request("锁等待", "pg-cli-lock-timeout"))
    with pg_store.engine.begin() as connection:
        assert connection.scalar(text("SHOW statement_timeout")) != "0"
        assert connection.scalar(text("SHOW lock_timeout")) != "0"
        connection.execute(text("LOCK TABLE run_events IN ACCESS EXCLUSIVE MODE"))
        result = run_cli(
            pg_store.schema,
            [f"/events {response.run_id}", "/help", "/exit"],
            org_id=pg_store.store.org_id,
        )

    assert result.returncode == 0, result.stderr
    assert "查询失败：" in result.stdout
    assert "超时" in result.stdout
    assert "/events <run_id>" in result.stdout
    assert "再见。" in result.stdout


def test_cli_accepts_postgresql_url_without_explicit_driver(pg_store, monkeypatch) -> None:
    monkeypatch.setenv(
        "CODEAGENT_TEST_DATABASE_URL",
        pg_store.url.replace("postgresql+psycopg://", "postgresql://", 1),
    )

    result = run_cli(pg_store.schema, ["/run 连接串验收", "/exit"])

    assert result.returncode == 0, result.stderr
    assert "执行成功" in result.stdout


def test_cli_read_failure_reports_query_failure_and_continues(
    pg16_url: str,
    fresh_schema: str,
) -> None:
    engine = engine_for_schema(pg16_url, fresh_schema)
    store = make_store(session_factory_for(engine))
    api = make_api(store, ids=["run_pg_read_fail_1"])
    response = api.create_run(create_request("读取故障", "pg-read-fail-key-1"))
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE agent_runs CASCADE"))

    stdout = io.StringIO()
    code = run_repl(
        api,
        io.StringIO(
            f"/get {response.run_id}\n"
            f"/events {response.run_id}\n"
            f"/trace {response.run_id}\n"
            "/help\n"
            "/exit\n"
        ),
        stdout,
    )
    output = stdout.getvalue()
    engine.dispose()

    assert code == 0
    assert output.count("查询失败：") == 3
    assert "/trace <run_id>" in output
    assert "再见。" in output
