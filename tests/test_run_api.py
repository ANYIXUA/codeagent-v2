from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from codeagent_v2.cli import BANNER, run_repl
from codeagent_v2.model import (
    MAX_REQUEST_CHARS,
    RunRecord,
    ValidationError,
    validate_request,
)
from codeagent_v2.run_api import InMemoryRunStore, RunAPI
from codeagent_v2.runtime import ExecutionOutcome, FixedExecutor

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
        self.assertEqual(record.error, "执行器异常：RuntimeError: boom")
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

    def test_cli_session_restart_loses_records(self) -> None:
        first_api, _ = make_api([DEFAULT_RUN_ID])
        first_api.submit_run("旧会话请求")

        second_api, second_store = make_api()
        code, output = run_session(second_api, [f"/get {DEFAULT_RUN_ID}", "/exit"])

        self.assertEqual(code, 0)
        self.assertEqual(second_store.count(), 0)
        self.assertIn(f"找不到这次运行：{DEFAULT_RUN_ID}", output)


class EntrypointTests(unittest.TestCase):
    def run_cli(self, input_text: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
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

    def test_entrypoint_starts_and_exits(self) -> None:
        result = self.run_cli("/exit\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(BANNER, result.stdout)
        self.assertIn("再见。", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_entrypoint_handles_chinese_request(self) -> None:
        result = self.run_cli("/run 给出一个 Python 冒泡排序示例\n/exit\n")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, re.compile(r"已创建运行：run_[0-9a-f]{12}"))
        self.assertIn("状态：执行成功", result.stdout)
        self.assertIn("结果：固定执行器完成：给出一个 Python 冒泡排序示例", result.stdout)


if __name__ == "__main__":
    unittest.main()
