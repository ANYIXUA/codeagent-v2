# 002 数据库持久化技术方案

上位规格见 `../../AGENT.md`，本章的 CLI 契约与验收条件见 `spec.md`，实现顺序见 `tasks.md`。

## 组件

沿用 Python 3.12、Pydantic 和 `pytest`。数据库采用 PostgreSQL 16、SQLAlchemy 2 Core 和 psycopg 3，版本迁移使用 Alembic。运行记录继续使用 `RunRecord`，查询通过既有的 `RunResponse` 返回。

| 文件 | 职责 |
|---|---|
| `requirements.txt` | 增加 SQLAlchemy、`psycopg[binary]`、Alembic 和 pytest |
| `codeagent_v2/database.py` | 从 `CODEAGENT_DATABASE_URL` 加载连接配置，创建同步引擎与 `Session` 工厂，检查数据库迁移版本，读取 `CODEAGENT_ORG_ID` / `CODEAGENT_USER_ID` 默认身份 |
| `codeagent_v2/db_schema.py` | 用 SQLAlchemy Core 的 `MetaData`、`Table`、`Column` 和 `Index` 定义表、字段、约束和索引，供存储层和 Alembic 共用 |
| `alembic.ini`、`alembic/env.py`、`alembic/script.py.mako`、`alembic/versions/` | 复用连接配置，将 `db_schema.metadata` 接到 `target_metadata`，保存和执行版本迁移 |
| `codeagent_v2/model.py` | 定义 `RunStoreError`，保留 `RunRecord` 和请求校验 |
| `codeagent_v2/run_api.py` | 定义 RunAPI 使用的 `RunStore` 协议，处理 ID 冲突，保留内存实现供测试注入 |
| `codeagent_v2/repository.py` | 实现 `PostgresRunStore`，通过 `Session` 执行 SQLAlchemy Core 语句，负责运行与事件的事务写入、运行查询和轨迹聚合 |
| `codeagent_v2/cli.py` | 在 `main()` 装配存储，通过 RunAPI 处理 `/run`、`/get`、`/events` 和 `/trace`，展示数据库故障，退出时关闭引擎 |
| `tests/test_postgres_run_store.py`、`tests/test_run_api.py`、`README.md` | 覆盖存储和 CLI，记录启动命令及验收方法 |

## 数据结构

首个 Alembic 迁移创建以下八张表。DDL 用于核对 SQLAlchemy Core 的表定义和迁移结果。本章写入 `agent_runs` 和 `run_events`，并查询事件和轨迹。步骤、调用和检查点可用测试数据验收轨迹查询，实际写入由对应章节接入。

字段验收使用现有列名。`tool_calls.idempotency_key` 保存工具幂等键，`checkpoints.state_json` 保存恢复状态，`usage_records` 用 `prompt_tokens`、`completion_tokens`、`total_tokens` 和 `estimated_cost_cents` 记录用量及以分为单位的估算成本。`audit_logs` 用 `user_id` 记录操作者，并保存对应的 `org_id`、`action` 和 `target_id`。

### `agent_runs`

每次 Agent 执行的主记录。查询按 `org_id` 限定组织，同组织的 `idempotency_key` 唯一。

```sql
CREATE TABLE agent_runs (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    input_json JSONB NOT NULL,
    output_json JSONB,
    error_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, idempotency_key)
);
```

### `agent_steps`

保存一次运行中的执行步骤，`step_index` 确定同一运行内的顺序。

```sql
CREATE TABLE agent_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    step_index INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json JSONB,
    output_json JSONB,
    error_json JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    UNIQUE (run_id, step_index)
);
```

### `run_events`

保存追加式运行事件，按同一次运行内的 `sequence` 回放。

```sql
CREATE TABLE run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence)
);
```

### `model_calls`

逐次记录模型调用的请求、响应、token 用量、延迟和结果。

```sql
CREATE TABLE model_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    step_id TEXT REFERENCES agent_steps(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    request_json JSONB NOT NULL,
    response_json JSONB,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL,
    error_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `tool_calls`

记录工具调用的参数、结果、状态和幂等键。有副作用的工具操作使用稳定的幂等键。

```sql
CREATE TABLE tool_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    step_id TEXT REFERENCES agent_steps(id),
    tool_name TEXT NOT NULL,
    args_json JSONB NOT NULL,
    result_json JSONB,
    status TEXT NOT NULL,
    idempotency_key TEXT,
    latency_ms INTEGER,
    error_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    UNIQUE (tool_name, idempotency_key)
);
```

### `checkpoints`

保存恢复运行所需的最小状态，`checkpoint_index` 标识同一运行内第几次保存恢复点。

```sql
CREATE TABLE checkpoints (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    checkpoint_index INTEGER NOT NULL,
    state_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, checkpoint_index)
);
```

### `usage_records`

保存组织、用户和运行维度的用量与估算成本，供账单、预算、限流和成本告警使用。

```sql
CREATE TABLE usage_records (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost_cents INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `audit_logs`

记录用户对哪个资源执行了什么高风险操作。`run_id` 可空，以便记录独立于某次运行的操作。

```sql
CREATE TABLE audit_logs (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_id TEXT REFERENCES agent_runs(id),
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    metadata_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 新增表的索引

在 SQLAlchemy 的表定义中声明以下索引。Alembic 迁移先创建对应表，再建立索引。下方 SQL 用于核对索引名、列顺序和排序方向。

```sql
CREATE INDEX idx_agent_runs_org_created
    ON agent_runs (org_id, created_at DESC);

CREATE INDEX idx_agent_runs_status_created
    ON agent_runs (status, created_at);

CREATE INDEX idx_agent_steps_run_index
    ON agent_steps (run_id, step_index);

CREATE INDEX idx_run_events_run_sequence
    ON run_events (run_id, sequence);

CREATE INDEX idx_model_calls_run_created
    ON model_calls (run_id, created_at);

CREATE INDEX idx_tool_calls_run_created
    ON tool_calls (run_id, created_at);

CREATE INDEX idx_tool_calls_tool_status
    ON tool_calls (tool_name, status, created_at);

CREATE INDEX idx_usage_records_org_created
    ON usage_records (org_id, created_at);

CREATE INDEX idx_audit_logs_org_created
    ON audit_logs (org_id, created_at);
```

## 数据流

```text
启动 → 读取数据库配置 → 检查连接及 Alembic 版本 → 创建 RunAPI → 进入 CLI
/run → RunAPI → 校验 → 固定执行器 → repository 同事务写入 run 与 run.created → 显示结果
/get → RunAPI → repository 按 org_id 和 id 查运行 → 显示结果
/events → RunAPI → repository 按组织查运行及事件 → 按 sequence 升序显示
/trace → RunAPI → repository 按组织查运行及关联表 → 显示执行轨迹
```

SQLAlchemy 查询和写入集中在 `repository.py`。CLI 通过 RunAPI 调用存储层，数据库连接与 `Session` 工厂由 `database.py` 提供，每次存储操作负责结束自己的会话。

`agent_runs.id` 对应 `RunResponse.run_id`，三个 JSONB 列对应 `input`、`output` 和 `error`。读回的时间戳统一为 UTC。当前组织和用户随存储层装配传入，`CreateRunRequest.idempotency_key` 随运行记录写入。

`RunStore` 提供 `add(record: RunRecord, *, idempotency_key: str) -> bool`、`get(run_id: str) -> RunRecord | None`、`list_events(run_id: str) -> list[RunEvent] | None`、`append_event(...)` 和 `get_trace(run_id: str) -> RunTrace | None`。`add` 先插入运行记录，再用同一会话和事务插入 `sequence = 1` 的 `run.created`。仅在两条记录提交后返回 `True`。事件插入失败时回滚事务。`INSERT … ON CONFLICT (id) DO NOTHING RETURNING id` 未返回行时，直接返回 `False`，跳过事件写入。RunAPI 此时只生成新 ID 并重试写入，固定执行器只运行一次。`(org_id, idempotency_key)` 冲突单独处理：存储层抛出 `DuplicateIdempotencyKeyError`，RunAPI 将其作为明确冲突返回，已有运行和事件保留。 [SQLAlchemy PostgreSQL 插入冲突处理](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert)

追加事件时，在同一事务中用 `SELECT … FOR UPDATE` 锁定当前组织的运行记录，读取该运行当前最大 `sequence` 后加 1，再插入事件。`UNIQUE (run_id, sequence)` 保证同一运行的序号唯一。所有读取入口先以 `org_id` 和 `id` 定位运行，再读取其关联记录。 [PostgreSQL 行级锁](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS)

存储层将数据库读写异常转换为 `RunStoreError`，由 CLI 根据命令显示「保存未确认」或「查询失败」。`get`、`list_events` 和 `get_trace` 仅在查询成功且无行时返回 `None`。数据库连接和事务按操作管理，写入提交异常交由 CLI 报告。`run_repl` 保留注入 RunAPI 的入口，内存测试可通过该入口继续验证终端交互。

### 已确认约定

- 本地 CLI 从 `CODEAGENT_ORG_ID` 和 `CODEAGENT_USER_ID` 读取身份，缺省值分别是 `local-org` 和 `local-user`。当前组织和用户随 `PostgresRunStore` 装配传入，所有查询条件都包含 `org_id`。
- 同一组织重复提交 `idempotency_key` 时返回明确冲突错误，不返回已有运行，也不覆盖已有运行或事件。只有 `agent_runs.id` 主键冲突才由 RunAPI 重新生成 `run_id` 并重试 `add`；执行器结果和事件内容保持不变，执行器只调用一次。
- 保留 PostgreSQL `JSONB`。RunAPI 在执行前遍历 `input` 的嵌套对象和数组，检查实际的 U+0000 字符，包括 JSON 键和值；实际空字符按非法输入拒绝。文本中的反斜杠加 `u0000` 不是 U+0000，可正常保存和读回。 [PostgreSQL 16 JSON 类型](https://www.postgresql.org/docs/16/datatype-json.html)
- 集成测试使用 `CODEAGENT_TEST_DATABASE_URL`，并在测试库内创建独立 schema；可选的 `CODEAGENT_DATABASE_SCHEMA` 让 CLI、Alembic 和存储层在同一 search path 下工作。生产验收仍使用测试库的默认 schema。

## 初始化、升级与启动

修改数据库结构时，先更新 SQLAlchemy 的 `metadata`，再运行 `python -m alembic revision --autogenerate -m "describe schema change"` 生成迁移草稿。核对表、约束、索引排序和数据变更后，将迁移文件纳入版本管理。 [Alembic 自动生成迁移](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)

先创建 UTF-8 的 PostgreSQL 16 数据库。应用和 Alembic 都读取同一个 `CODEAGENT_DATABASE_URL`，格式为 `postgresql+psycopg://用户:密码@主机:端口/数据库`，仓库只记录占位符。用 `python -m alembic upgrade head` 初始化或升级，再用 `python -m codeagent_v2` 启动。启动检查数据库当前迁移版本是否与仓库的 Alembic `head` 一致，版本未就绪时提示升级命令。 [Alembic 版本升级与当前版本](https://alembic.sqlalchemy.org/en/latest/tutorial.html#running-our-first-migration)

## PostgreSQL 与 SQLite 的差异

- PostgreSQL 采用原生 `JSONB` 和 `TIMESTAMPTZ`。SQLite 的 JSONB 使用自己的 BLOB 格式，日期时间通常存为文本、实数或整数。 [PostgreSQL JSON 类型](https://www.postgresql.org/docs/16/datatype-json.html) · [PostgreSQL 日期时间类型](https://www.postgresql.org/docs/16/datatype-datetime.html) · [SQLite JSONB 格式](https://www.sqlite.org/json1.html#the_jsonb_format) · [SQLite 日期时间存储](https://www.sqlite.org/datatype3.html#date_and_time_datatype)
- 本章用 PostgreSQL 行级锁协调同一运行的并发事件写入。SQLite 通过单写者机制串行化写入。本章的迁移、事务回滚与事件序号验收使用 PostgreSQL 16。 [PostgreSQL 行级锁](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS) · [SQLite 事务隔离](https://www.sqlite.org/isolation.html)

## 验证

1. 在独立的 PostgreSQL 16 测试数据库执行首次迁移，核对八张表的字段、约束和九条显式索引，再通过 `agent_runs` 跑通成功和失败两种终态，读回完整输入、状态、结果和 UTC 时间戳。
2. 让两个 CLI 进程依次连接同一数据库，第一进程提交成功与失败请求，第二进程按第一进程返回的 ID 查询。再次迁移后比较两条记录。
3. 验证非法输入、未知 ID、迁移版本未就绪、数据库不可用和 ID 冲突。内存单元测试继续覆盖第一章契约，跨进程持久化由真实 PostgreSQL 集成测试覆盖。
4. 分别验证事件插入成功和失败。用独立连接核对运行和首条事件一起提交或一起回滚，再验证并发追加、`/events` 排序、`/trace` 聚合及跨组织查询。
