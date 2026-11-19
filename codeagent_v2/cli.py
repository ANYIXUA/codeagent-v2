from __future__ import annotations

import sys
from typing import TextIO

from .model import RunRecord, ValidationError
from .run_api import InMemoryRunStore, RunAPI
from .runtime import FixedExecutor

BANNER = "RunAPI CLI（内存模式）。输入 /help 查看命令。"
PROMPT = "codeagent> "
HELP_TEXT = (
    "可用命令：\n"
    "  /run <请求>    提交一次运行（同步执行）\n"
    "  /get <run_id>  查询运行结果\n"
    "  /help          显示本帮助\n"
    "  /exit          退出（/quit 同义）\n"
)


def _write(stream: TextIO, text: str) -> None:
    stream.write(text)
    stream.flush()


def _render_record(record: RunRecord, stdout: TextIO, *, created: bool) -> None:
    heading = "已创建运行" if created else "运行"
    _write(stdout, f"{heading}：{record.run_id}\n")
    if record.status == "succeeded":
        _write(stdout, "状态：执行成功\n")
        _write(stdout, f"结果：{record.result}\n")
    else:
        _write(stdout, "状态：执行失败\n")
        _write(stdout, f"原因：{record.error}\n")


def run_repl(api: RunAPI, stdin: TextIO, stdout: TextIO) -> int:
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
                record = api.submit_run(payload)
            except ValidationError as exc:
                _write(
                    stdout,
                    f"提交失败：{exc.field} {exc.reason}（未创建运行记录）\n",
                )
            else:
                _render_record(record, stdout, created=True)
        elif command == "/get":
            run_id = payload.strip()
            if not run_id:
                _write(stdout, "用法：/get <run_id>\n")
                continue
            record = api.get_run(run_id)
            if record is None:
                _write(stdout, f"找不到这次运行：{run_id}\n")
            else:
                _render_record(record, stdout, created=False)
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
    api = RunAPI(InMemoryRunStore(), FixedExecutor())
    try:
        return run_repl(api, sys.stdin, sys.stdout)
    except KeyboardInterrupt:
        _write(sys.stdout, "\n")
        return 130
