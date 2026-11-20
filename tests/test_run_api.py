from __future__ import annotations

import io
import os
import re
import signal
import subprocess
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError as SchemaValidationError

from codeagent_v2.cli import BANNER, run_repl
from codeagent_v2.model import (
    MAX_REQUEST_CHARS,
    DuplicateIdempotencyKeyError,
    RunRecord,
    RunStoreError,
    ValidationError,
    validate_request,
)
from codeagent_v2.run_api import InMemoryRunStore, RunAPI
from codeagent_v2.runtime import ExecutionOutcome, FixedExecutor
from codeagent_v2.schemas import CreateRunRequest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = "run_000000000001"


def make_api(
    ids: list[str] | None = None,
    executor: object | None = None,
) -> tuple[RunAPI, InMemoryRunStore]:
    store = InMemoryRunStore()
    if ids is None:
        id_factory = None
    else:
        iterator = iter(ids)
        id_factory = lambda: next(iterator)

    return (
        RunAPI(
            store,
            executor if executor is not None else FixedExecutor(),  # type: ignore[arg-type]
            id_factory=id_factory,
        ),
        store,
    )


def run_session(api: RunAPI, lines: list[str]) -> tuple[int, str]:
    stdout = io.StringIO()
    stdin = io.StringIO("\n".join(lines) + "\n")
    code = run_repl(api, stdin, stdout)
    return code, stdout.getvalue()


class ValidationTests(unittest.TestCase):
    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            validate_request("")
        self.assertEqual(caught.exception.field, "request")
        self.assertEqual(caught.exception.reason, "不能为空")

    def test_rejects_whitespace_only(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            validate_request(" \t\r\n ")
        self.assertEqual(caught.exception.reason, "不能为空")

    def test_rejects_non_string(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            validate_request(None)
        self.assertEqual(caught.exception.reason, "必须是字符串")

    def test_accepts_2000_chars(self) -> None:
        text = "a" * MAX_REQUEST_CHARS
        self.assertEqual(validate_request(text), text)

    def test_rejects_2001_chars(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            validate_request("a" * (MAX_REQUEST_CHARS + 1))
        self.assertEqual(caught.exception.reason, "不能超过 2000 个字符")

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(validate_request("  问题  "), "问题")


class FixedExecutorTests(unittest.TestCase):
    def test_success(self) -> None:
        outcome = FixedExecutor().execute("给出一个 Python 冒泡排序示例")
        self.assertEqual(
            outcome,
            ExecutionOutcome(
                ok=True,
                result="固定执行器完成：给出一个 Python 冒泡排序示例",
                error=None,
            ),
        )

    def test_failure_markers(self) -> None:
        executor = FixedExecutor()
        for request in ("[fail] 触发失败用例", "[FAIL] 任意内容", "[fail]"):
            with self.subTest(request=request):
                self.assertEqual(
                    executor.execute(request),
                    ExecutionOutcome(
                        ok=False,
                        result=None,
                        error="固定执行器按要求返回失败",
                    ),
                )

    def test_is_deterministic(self) -> None:
        executor = FixedExecutor()
        self.assertEqual(
            executor.execute("重复请求"),
            executor.execute("重复请求"),
        )


class RunAPITests(unittest.TestCase):
    def test_submit_success_returns_run_id(self) -> None:
        api, store = make_api([DEFAULT_RUN_ID])
        record = api.submit_run("给出一个 Python 冒泡排序示例")

        self.assertIsInstance(record, RunRecord)
        self.assertEqual(record.run_id, DEFAULT_RUN_ID)
        self.assertEqual(record.status, "succeeded")
        self.assertEqual(record.result, "固定执行器完成：给出一个 Python 冒泡排序示例")
        self.assertIsNone(record.error)
        self.assertEqual(store.count(), 1)
        self.assertEqual(api.get_run(DEFAULT_RUN_ID), record)

    def test_default_run_id_shape(self) -> None:
        api, _ = make_api()
        record = api.submit_run("任意请求")
        self.assertRegex(record.run_id, r"^run_[0-9a-f]{12}$")

    def test_submit_failure_still_returns_run_id(self) -> None:
        api, store = make_api([DEFAULT_RUN_ID])
        record = api.submit_run("[fail] 触发失败用例")

        self.assertEqual(record.run_id, DEFAULT_RUN_ID)
        self.assertEqual(record.status, "failed")
        self.assertIsNone(record.result)
        self.assertEqual(record.error, "固定执行器按要求返回失败")
        self.assertEqual(store.count(), 1)
        self.assertEqual(api.get_run(DEFAULT_RUN_ID), record)

    def test_executor_exception_still_returns_run_id(self) -> None:
        class ExplodingExecutor:
            def execute(self, request: str) -> ExecutionOutcome:
                raise RuntimeError("boom")

        api, store = make_api([DEFAULT_RUN_ID], executor=ExplodingExecutor())
        record = api.submit_run("触发异常")

        self.assertEqual(record.run_id, DEFAULT_RUN_ID)
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error, "执行器异常")
        self.assertEqual(store.count(), 1)

    def test_invalid_submission_creates_no_record(self) -> None:
        created_ids: list[str] = []

        def id_factory() -> str:
            created_ids.append(DEFAULT_RUN_ID)
            return DEFAULT_RUN_ID

        store = InMemoryRunStore()
        api = RunAPI(store, FixedExecutor(), id_factory=id_factory)

        for request in ("", "   ", "a" * (MAX_REQUEST_CHARS + 1)):
            with self.subTest(request=request):
                with self.assertRaises(ValidationError):
                    api.submit_run(request)

        self.assertEqual(store.count(), 0)
        self.assertEqual(created_ids, [])

    def test_get_unknown_returns_none(self) -> None:
        api, _ = make_api()
        self.assertIsNone(api.get_run("run_000000000000"))

    def test_records_are_session_local(self) -> None:
        api, _ = make_api([DEFAULT_RUN_ID])
        record = api.submit_run("会话内请求")

        other_api = RunAPI(InMemoryRunStore(), FixedExecutor())
        self.assertIsNone(other_api.get_run(record.run_id))

    def test_duplicate_request_creates_new_run_id(self) -> None:
        api, store = make_api(["run_000000000001", "run_000000000002"])
        first = api.submit_run("同一请求")
        second = api.submit_run("同一请求")

        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(store.count(), 2)


class RunAPIStorageContractTests(unittest.TestCase):
    def test_id_conflict_retries_without_executing_again(self) -> None:
        store = InMemoryRunStore()
        first_api = RunAPI(
            store,
            FixedExecutor(),
            id_factory=lambda: "run_000000000001",
        )
        first = first_api.submit_run("第一次运行", idempotency_key="key_first_0001")

        calls = 0

        class CountingExecutor:
            def execute(self, request: str) -> ExecutionOutcome:
                nonlocal calls
                calls += 1
                return FixedExecutor().execute(request)

        ids = iter(("run_000000000001", "run_000000000002"))
        second_api = RunAPI(store, CountingExecutor(), id_factory=lambda: next(ids))
        second = second_api.submit_run("第二次运行", idempotency_key="key_second_0002")

        self.assertEqual(calls, 1)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(second.run_id, "run_000000000002")
        self.assertEqual(store.count(), 2)
        self.assertEqual(store.get(first.run_id), first)

    def test_duplicate_idempotency_key_is_a_conflict(self) -> None:
        store = InMemoryRunStore()
        api = RunAPI(store, FixedExecutor(), id_factory=lambda: "run_000000000001")
        first = api.create_run(
            CreateRunRequest(
                run_type="text",
                idempotency_key="same-key-0001",
                input={"query": "第一次"},
            )
        )

        with self.assertRaises(DuplicateIdempotencyKeyError):
            api.create_run(
                CreateRunRequest(
                    run_type="text",
                    idempotency_key="same-key-0001",
                    input={"query": "第二次"},
                )
            )

        self.assertEqual(store.count(), 1)
        self.assertEqual(api.query_run(first.run_id), first)
        events = api.list_events(first.run_id)
        self.assertIsNotNone(events)
        self.assertEqual([event.sequence for event in events or []], [1])
        self.assertEqual((events or [])[0].event_type, "run.created")

    def test_first_event_and_appended_event_order(self) -> None:
        store = InMemoryRunStore()
        api = RunAPI(store, FixedExecutor(), id_factory=lambda: "run_000000000001")
        record = api.submit_run("事件顺序", idempotency_key="key_events_0001")

        self.assertIsNotNone(api.append_event(record.run_id, "step.started", {"index": 0}))
        self.assertIsNotNone(api.append_event(record.run_id, "step.finished", {"index": 0}))

        events = api.list_events(record.run_id)
        self.assertEqual(
            [(event.sequence, event.event_type) for event in events or []],
            [
                (1, "run.created"),
                (2, "step.started"),
                (3, "step.finished"),
            ],
        )
        trace = api.get_trace(record.run_id)
        self.assertIsNotNone(trace)
        self.assertEqual(trace.run, record)
        self.assertEqual([event.sequence for event in trace.events], [1, 2, 3])

    def test_actual_null_character_is_rejected_before_execution(self) -> None:
        calls = 0

        class CountingExecutor:
            def execute(self, request: str) -> ExecutionOutcome:
                nonlocal calls
                calls += 1
                return FixedExecutor().execute(request)

        store = InMemoryRunStore()
        api = RunAPI(store, CountingExecutor())
        request = CreateRunRequest(
            run_type="text",
            idempotency_key="key_null_0001",
            input={"query": "含空字符", "metadata": {"value": "ok"}},
        )
        request.input["metadata"]["value"] = "bad\x00value"

        with self.assertRaises(ValidationError):
            api.create_run(request)

        self.assertEqual(calls, 0)
        self.assertEqual(store.count(), 0)

    def test_null_character_in_nested_key_is_rejected(self) -> None:
        with self.assertRaises(SchemaValidationError):
            CreateRunRequest(
                run_type="text",
                idempotency_key="key_null_0002",
                input={"outer": {"bad\x00key": "value"}},
            )

    def test_literal_backslash_u0000_text_is_storable(self) -> None:
        store = InMemoryRunStore()
        api = RunAPI(store, FixedExecutor(), id_factory=lambda: "run_000000000001")
        literal = "保留 \\u0000 文本"
        response = api.create_run(
            CreateRunRequest(
                run_type="text",
                idempotency_key="key_literal_0001",
                input={"query": literal, "metadata": {"path": literal}},
            )
        )

        stored = api.query_run(response.run_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.input["query"], literal)
        self.assertEqual(stored.input["metadata"]["path"], literal)


class CLITests(unittest.TestCase):
    def test_cli_success_transcript(self) -> None:
        api, store = make_api([DEFAULT_RUN_ID])
        code, output = run_session(
            api,
            [
                "/run 给出一个 Python 冒泡排序示例",
                f"/get {DEFAULT_RUN_ID}",
                "/exit",
            ],
        )

        self.assertEqual(code, 0)
        self.assertEqual(store.count(), 1)
        self.assertEqual(
            output,
            (
                f"{BANNER}\n"
                f"codeagent> 已创建运行：{DEFAULT_RUN_ID}\n"
                "状态：执行成功\n"
                "结果：固定执行器完成：给出一个 Python 冒泡排序示例\n"
                f"codeagent> 运行：{DEFAULT_RUN_ID}\n"
                "状态：执行成功\n"
                "结果：固定执行器完成：给出一个 Python 冒泡排序示例\n"
                "codeagent> 再见。\n"
            ),
        )

    def test_cli_failure_transcript(self) -> None:
        api, store = make_api([DEFAULT_RUN_ID])
        code, output = run_session(
            api,
            [
                "/run [fail] 触发失败用例",
                f"/get {DEFAULT_RUN_ID}",
                "/exit",
            ],
        )

        self.assertEqual(code, 0)
        self.assertEqual(store.count(), 1)
        self.assertEqual(
            output,
            (
                f"{BANNER}\n"
                f"codeagent> 已创建运行：{DEFAULT_RUN_ID}\n"
                "状态：执行失败\n"
                "原因：固定执行器按要求返回失败\n"
                f"codeagent> 运行：{DEFAULT_RUN_ID}\n"
                "状态：执行失败\n"
                "原因：固定执行器按要求返回失败\n"
                "codeagent> 再见。\n"
            ),
        )

    def test_cli_invalid_and_unknown_transcript(self) -> None:
        api, store = make_api([DEFAULT_RUN_ID])
        code, output = run_session(
            api,
            [
                "/run",
                "/get run_000000000000",
                "/exit",
            ],
        )

        self.assertEqual(code, 0)
        self.assertEqual(store.count(), 0)
        self.assertEqual(
            output,
            (
                f"{BANNER}\n"
                "codeagent> 提交失败：request 不能为空（未创建运行记录）\n"
                "codeagent> 找不到这次运行：run_000000000000\n"
                "codeagent> 再见。\n"
            ),
        )

    def test_cli_overlong_request(self) -> None:
        api, store = make_api([DEFAULT_RUN_ID])
        code, output = run_session(
            api,
            [
                "/run " + "a" * (MAX_REQUEST_CHARS + 1),
                "/exit",
            ],
        )

        self.assertEqual(code, 0)
        self.assertEqual(store.count(), 0)
        self.assertIn(
            "提交失败：request 不能超过 2000 个字符（未创建运行记录）",
            output,
        )

    def test_cli_usage_and_unknown_command(self) -> None:
        api, _ = make_api()
        code, output = run_session(
            api,
            ["/get", "/unknown", "/help", "/exit"],
        )

        self.assertEqual(code, 0)
        self.assertIn("用法：/get <run_id>", output)
        self.assertIn("未知命令：/unknown。输入 /help 查看可用命令。", output)
        self.assertIn("/run <请求>", output)

    def test_cli_eof_exits(self) -> None:
        api, _ = make_api()
        stdout = io.StringIO()
        code = run_repl(api, io.StringIO(""), stdout)

        self.assertEqual(code, 0)
        self.assertTrue(stdout.getvalue().startswith(BANNER))
        self.assertTrue(stdout.getvalue().endswith("\n"))

    def test_cli_interrupt_while_reading_returns_130(self) -> None:
        class InterruptingInput(io.StringIO):
            def readline(self, size: int = -1) -> str:
                signal.raise_signal(signal.SIGINT)
                return ""

        api, _ = make_api()
        try:
            code = run_repl(api, InterruptingInput(), io.StringIO())
        except KeyboardInterrupt:
            self.fail("The REPL must handle SIGINT during input and return 130")
        self.assertEqual(code, 130)

    def test_cli_session_restart_loses_records(self) -> None:
        first_api, _ = make_api([DEFAULT_RUN_ID])
        first_api.submit_run("旧会话请求")

        second_api, second_store = make_api()
        code, output = run_session(second_api, [f"/get {DEFAULT_RUN_ID}", "/exit"])

        self.assertEqual(code, 0)
        self.assertEqual(second_store.count(), 0)
        self.assertIn(f"找不到这次运行：{DEFAULT_RUN_ID}", output)


class CLIEventsAndTraceTests(unittest.TestCase):
    def test_events_and_trace_render_empty_sections(self) -> None:
        api, _ = make_api([DEFAULT_RUN_ID])
        api.submit_run("轨迹请求", idempotency_key="key_trace_0001")

        code, output = run_session(
            api,
            [
                f"/events {DEFAULT_RUN_ID}",
                f"/trace {DEFAULT_RUN_ID}",
                "/exit",
            ],
        )

        self.assertEqual(code, 0)
        self.assertIn(f"事件：{DEFAULT_RUN_ID}", output)
        self.assertIn("1. run.created", output)
        self.assertIn("模型调用：\n  （空）", output)
        self.assertIn("工具调用：\n  （空）", output)
        self.assertIn("检查点：\n  （空）", output)

    def test_events_and_trace_share_the_not_found_message(self) -> None:
        api, _ = make_api()
        code, output = run_session(
            api,
            ["/events run_missing", "/trace run_missing", "/exit"],
        )

        self.assertEqual(code, 0)
        self.assertEqual(output.count("找不到这次运行：run_missing"), 2)

    def test_cli_reports_store_failures_and_keeps_running(self) -> None:
        class FailingStore(InMemoryRunStore):
            def add(self, record: RunRecord, *, idempotency_key: str) -> bool:
                raise RunStoreError("数据库连接中断")

            def get(self, run_id: str) -> RunRecord | None:
                raise RunStoreError("数据库连接中断")

            def list_events(self, run_id: str):
                raise RunStoreError("数据库连接中断")

            def get_trace(self, run_id: str):
                raise RunStoreError("数据库连接中断")

        api = RunAPI(FailingStore(), FixedExecutor(), id_factory=lambda: DEFAULT_RUN_ID)
        code, output = run_session(
            api,
            [
                "/run 触发写入失败",
                "/get run_missing",
                "/events run_missing",
                "/trace run_missing",
                "/help",
                "/exit",
            ],
        )

        self.assertEqual(code, 0)
        self.assertIn("保存未确认：数据库连接中断", output)
        self.assertEqual(output.count("查询失败：数据库连接中断"), 3)
        self.assertIn("/events <run_id>", output)
        self.assertIn("再见。", output)


class EntrypointStartupTests(unittest.TestCase):
    def run_cli(self, input_text: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env.pop("CODEAGENT_DATABASE_URL", None)
        return subprocess.run(
            [sys.executable, "-m", "codeagent_v2"],
            input=input_text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=30,
        )

    def test_missing_database_configuration_fails_before_repl(self) -> None:
        result = self.run_cli("/exit\n")

        self.assertEqual(result.returncode, 2)
        self.assertIn("数据库启动失败", result.stderr)
        self.assertIn("CODEAGENT_DATABASE_URL", result.stderr)
        self.assertNotIn(BANNER, result.stdout)

    def test_invalid_database_url_fails_before_repl(self) -> None:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["CODEAGENT_DATABASE_URL"] = "sqlite:///tmp.db"
        result = subprocess.run(
            [sys.executable, "-m", "codeagent_v2"],
            input="/exit\n",
            text=True,
            encoding="utf-8",
            capture_output=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=30,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("必须使用 PostgreSQL 连接串", result.stderr)


if __name__ == "__main__":
    unittest.main()
