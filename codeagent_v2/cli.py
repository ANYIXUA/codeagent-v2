from __future__ import annotations

import json
import sys
from typing import Any, TextIO
from uuid import uuid4

from pydantic import ValidationError as SchemaValidationError

from .database import Database, DatabaseError, get_identity
from .model import (
    DuplicateIdempotencyKeyError,
    RunEvent,
    RunRecord,
    RunStoreError,
    RunTrace,
    ValidationError,
)
from .repository import PostgresRunStore
from .run_api import InMemoryRunStore, RunAPI
from .runtime import FixedExecutor
from .schemas import CreateRunRequest, RunResponse, RunStatus

BANNER = "RunAPI CLI（PostgreSQL 持久化）。输入 /help 查看命令。"
PROMPT = "codeagent> "
HELP_TEXT = (
    "可用命令：\n"
    "  /run <请求>    提交一次运行（同步执行并持久化）\n"
    "  /get <run_id>  查询运行结果\n"
    "  /events <run_id> 按 sequence 显示运行事件\n"
    "  /trace <run_id>  显示运行、步骤、事件、模型调用、工具调用和检查点\n"
    "  /help          显示本帮助\n"
    "  /exit          退出（/quit 同义）\n"
)
NOT_FOUND = "找不到这次运行"


def _write(stream: TextIO, text: str) -> None:
    stream.write(text)
    stream.flush()


def _render_record(record: RunResponse, stdout: TextIO, *, created: bool) -> None:
    heading = "已创建运行" if created else "运行"
    _write(stdout, f"{heading}：{record.run_id}\n")
    if record.status == RunStatus.SUCCEEDED:
        output = record.output or {}
        _write(stdout, "状态：执行成功\n")
        _write(stdout, f"结果：{output.get('text')}\n")
    else:
        error = record.error or {}
        _write(stdout, "状态：执行失败\n")
        _write(stdout, f"原因：{error.get('message')}\n")


def _render_trace_run(record: RunRecord, stdout: TextIO) -> None:
    _write(stdout, f"运行：{record.run_id}\n")
    if record.status == RunStatus.SUCCEEDED:
        _write(stdout, "状态：执行成功\n")
        _write(stdout, f"结果：{record.result}\n")
    else:
        _write(stdout, "状态：执行失败\n")
        _write(stdout, f"原因：{record.error}\n")


def _event_line(event: RunEvent) -> str:
    payload = json.dumps(event.payload, ensure_ascii=False, default=str)
    return f"{event.sequence}. {event.event_type} {payload}\n"


def _render_events(events: list[RunEvent], stdout: TextIO) -> None:
    if not events:
        _write(stdout, "（空）\n")
        return
    for event in events:
        _write(stdout, _event_line(event))


def _render_trace(trace: RunTrace, stdout: TextIO) -> None:
    _render_trace_run(trace.run, stdout)

    _write(stdout, "步骤：\n")
    if not trace.steps:
        _write(stdout, "  （空）\n")
    for step in trace.steps:
        _write(
            stdout,
            f"  {step['step_index']}. {step['step_type']} {step['name']} [{step['status']}]\n",
        )

    _write(stdout, "事件：\n")
    if not trace.events:
        _write(stdout, "  （空）\n")
    for event in trace.events:
        _write(stdout, f"  {_event_line(event)}")

    _write(stdout, "模型调用：\n")
    if not trace.model_calls:
        _write(stdout, "  （空）\n")
    for call in trace.model_calls:
        _write(
            stdout,
            f"  {call['id']} {call['provider']}/{call['model']} [{call['status']}]\n",
        )

    _write(stdout, "工具调用：\n")
    if not trace.tool_calls:
        _write(stdout, "  （空）\n")
    for call in trace.tool_calls:
        _write(stdout, f"  {call['id']} {call['tool_name']} [{call['status']}]\n")

    _write(stdout, "检查点：\n")
    if not trace.checkpoints:
        _write(stdout, "  （空）\n")
    for checkpoint in trace.checkpoints:
        _write(stdout, f"  {checkpoint['checkpoint_index']}\n")


def _schema_error_message(exc: SchemaValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "请求格式无效"
    message = str(errors[0].get("msg", "请求格式无效"))
    if message.startswith("Value error, "):
        message = message[len("Value error, ") :]
    if "U+0000" in message:
        return "输入不能包含 U+0000 字符（普通文本中的 \\u0000 可以保存）"
    if "nested JSON containers" in message:
        return "输入最多嵌套 64 层 JSON 容器"
    return f"请求格式无效：{message}"


def _handle_events(api: RunAPI, run_id: str, stdout: TextIO) -> None:
    try:
        events = api.list_events(run_id)
    except RunStoreError as exc:
        _write(stdout, f"查询失败：{exc}\n")
        return
    if events is None:
        _write(stdout, f"{NOT_FOUND}：{run_id}\n")
        return
    _write(stdout, f"事件：{run_id}\n")
    _render_events(events, stdout)


def _handle_trace(api: RunAPI, run_id: str, stdout: TextIO) -> None:
    try:
        trace = api.get_trace(run_id)
    except RunStoreError as exc:
        _write(stdout, f"查询失败：{exc}\n")
        return
    if trace is None:
        _write(stdout, f"{NOT_FOUND}：{run_id}\n")
        return
    _render_trace(trace, stdout)


def run_repl(api: RunAPI | None, stdin: TextIO, stdout: TextIO) -> int:
    if api is None:
        api = RunAPI(InMemoryRunStore(), FixedExecutor())
    _write(stdout, f"{BANNER}\n")

    while True:
        _write(stdout, PROMPT)
        try:
            raw = stdin.readline()
        except KeyboardInterrupt:
            _write(stdout, "\n")
            return 130

        if raw == "":
            _write(stdout, "\n")
            return 0

        line = raw.strip()
        if not line:
            continue

        parts = line.split(maxsplit=1)
        command = parts[0]
        payload = parts[1] if len(parts) == 2 else ""

        if command == "/run":
            try:
                request = CreateRunRequest(
                    run_type="text",
                    idempotency_key=uuid4().hex,
                    input={"query": payload},
                )
                record = api.create_run(request)
            except ValidationError as exc:
                _write(stdout, f"提交失败：{exc.field} {exc.reason}（未创建运行记录）\n")
            except DuplicateIdempotencyKeyError as exc:
                _write(stdout, f"提交冲突：{exc}（未创建新运行记录）\n")
            except SchemaValidationError as exc:
                _write(stdout, f"提交失败：{_schema_error_message(exc)}（未创建运行记录）\n")
            except RunStoreError as exc:
                _write(stdout, f"保存未确认：{exc}\n")
            else:
                _render_record(record, stdout, created=True)
        elif command == "/get":
            run_id = payload.strip()
            if not run_id:
                _write(stdout, "用法：/get <run_id>\n")
                continue
            try:
                record = api.query_run(run_id)
            except RunStoreError as exc:
                _write(stdout, f"查询失败：{exc}\n")
                continue
            if record is None:
                _write(stdout, f"{NOT_FOUND}：{run_id}\n")
            else:
                _render_record(record, stdout, created=False)
        elif command == "/events":
            run_id = payload.strip()
            if not run_id:
                _write(stdout, "用法：/events <run_id>\n")
                continue
            _handle_events(api, run_id, stdout)
        elif command == "/trace":
            run_id = payload.strip()
            if not run_id:
                _write(stdout, "用法：/trace <run_id>\n")
                continue
            _handle_trace(api, run_id, stdout)
        elif command == "/help":
            _write(stdout, HELP_TEXT)
        elif command in ("/exit", "/quit"):
            _write(stdout, "再见。\n")
            return 0
        else:
            _write(stdout, f"未知命令：{command}。输入 /help 查看可用命令。\n")


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main() -> int:
    _configure_utf8_stdio()
    try:
        database = Database.from_env()
        database.ensure_ready()
    except DatabaseError as exc:
        _write(sys.stderr, f"数据库启动失败：{exc}\n")
        return 2

    org_id, user_id = get_identity()
    store = PostgresRunStore(
        database.session_factory,
        org_id=org_id,
        user_id=user_id,
    )
    api = RunAPI(store, FixedExecutor())
    try:
        return run_repl(api, sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        _write(sys.stdout, "\n")
        return 130
    finally:
        database.close()
