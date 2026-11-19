# 001 RunAPI 规格

上位规格见 `../../AGENT.md`。

## 目标

用户能从 CLI 提交一次运行，并用返回的 `run_id` 查询执行结果。CLI 直接调用当前进程中的 RunAPI。

## 本阶段范围

固定执行器同步执行。用户在同一个交互式 CLI 会话内提交和查询，CLI 在执行结束后返回 `run_id`。运行记录保存在当前进程的内存中，退出 CLI 后，本次会话的记录失效。

## CLI 契约

| 命令 | 内部接口 | 终端输出 |
|---|---|---|
| `/run <请求>` | `RunAPI.create_run(CreateRunRequest)` | `run_id`、执行状态、结果或失败原因 |
| `/get <run_id>` | `RunAPI.query_run(run_id)` | 对应运行的状态、结果或失败原因 |

CLI 将文本封装为 `CreateRunRequest`，使用 `run_type="text"`、自动生成的 `idempotency_key` 和 `input.query`。RunAPI 返回 `RunResponse`，CLI 从中读取并展示结果。非法请求提示原因并继续接收命令，未知 ID 提示「找不到这次运行」。

固定执行器使用 `input.query` 作为本章的文本输入。去掉首尾空白后，文本须有 1 到 2000 个字符。以 `[fail]` 开头的输入产生失败记录。合法输入即使执行失败也返回 `run_id`，失败原因保存在 `RunResponse.error`。成功结果保存在 `RunResponse.output.text`。

创建请求的 `input` 使用可编码为 UTF-8 的 JSON 值，数值须为有限值。对象和数组合计最多嵌套 64 层，从 `input` 根对象算起。数据校验在创建运行记录之前完成。

## Schema

| 名称 | 字段和约束 |
|---|---|
| `RunStatus` | 字符串枚举，值为 `queued`、`running`、`waiting_for_user`、`succeeded`、`failed`、`cancelled` |
| `CreateRunRequest` | `run_type` 为 1 到 100 字符的字符串，`idempotency_key` 为 8 到 200 字符的字符串，`input` 为必填 JSON 对象 |
| `RunResponse` | `run_id`、`run_type` 为字符串，`status` 为 `RunStatus`，`input`、`output`、`error` 为可空 JSON 对象，`created_at` 为必填时间，`started_at`、`ended_at` 为可空时间 |
| `CancelRunRequest` | `reason` 为 1 到 300 字符的字符串 |
| `ResumeRunRequest` | `resume_token` 为 1 到 200 字符的字符串，`input` 为 JSON 对象，默认空对象 |

本章的同步执行对外返回 `succeeded` 或 `failed`，时间戳使用 UTC。成功时 `error` 为空，失败时 `output` 为空，`error` 包含 `code` 和 `message`。本章校验 `idempotency_key` 的形状，每次合法提交创建一次运行。取消和恢复的行为在对应阶段接入。

## 验收条件

| 编号 | 场景 | 预期结果 |
|---|---|---|
| 1 | 合法输入，执行成功 | 提交及查询返回一致的运行记录，CLI 显示「执行成功」和结果 |
| 2 | 合法输入，执行失败 | 提交及查询返回一致的失败记录，CLI 显示「执行失败」和原因 |
| 3 | 非法输入 | CLI 指出错误，运行记录数不变 |
| 4 | 查询未知 run_id | CLI 提示「找不到这次运行」 |
| 5 | 校验 schema | 验证六种状态、字段长度、可空字段及恢复输入的默认值 |

`succeeded` 表示本阶段的执行器正常返回。用户交付物是否符合本轮目标，由后续执行 Loop 阶段验收。
