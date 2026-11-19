# 001 RunAPI 技术方案

上位规格见 `../../AGENT.md`，本章的 CLI 契约见 `spec.md`。

## 组件

| 文件 | 职责 |
|---|---|
| `codeagent_v2/schemas.py` | Run 状态枚举和四个请求、响应模型 |
| `codeagent_v2/run_api.py`、`model.py` | 提供类型化的创建和查询接口，保存运行记录和 UTC 时间戳 |
| `codeagent_v2/runtime.py` | 固定执行器 |
| `codeagent_v2/cli.py` | 解析命令，直接调用 RunAPI，显示终端结果 |
| `tests/test_run_contract.py`、`test_run_api.py` | 验证 schema、RunAPI 及实际 CLI 会话 |

## 数据流

```text
/run <请求> → 构造 CreateRunRequest → RunAPI.create_run
  → 固定执行器 → 内存存储 → RunResponse → 终端显示结果

/get <run_id> → RunAPI.query_run → 查同一个存储
  → RunResponse 或 None → 终端显示结果或提示未知 ID
```

每次 CLI 会话创建一个 RunAPI 实例和一个内存存储。合法提交同步执行，保存结果和时间戳。查询读取已保存的记录，再按同一输出模型返回。终端启动命令为 `python -m codeagent_v2`。

## 验证

1. 在同一个 CLI 会话提交普通请求和 `[fail]` 请求，分别按返回的 `run_id` 查询。
2. 提交空或超长请求，检查校验提示。查询未知 ID，检查提示并确认可以继续输入。
3. 验证状态枚举、字段长度、嵌套边界、恢复输入默认值及运行响应的字段。
