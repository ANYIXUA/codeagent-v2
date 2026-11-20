# codeagent-v2

第二章把 RunAPI 的运行记录持久化到 PostgreSQL 16：CLI 提交请求后，运行、结果和首条 `run.created` 事件在同一事务中提交。退出并重启 CLI 后，仍可用原来的 `run_id` 查询结果、事件和执行轨迹。

## 环境

- Python 3.12
- PostgreSQL 16
- Windows + PowerShell
- SQLAlchemy 2 Core、psycopg 3、Alembic、pytest、Pydantic 2

## 本章改动文件

| 文件 | 职责 |
|---|---|
| `requirements.txt` | 增加 SQLAlchemy、psycopg 3、Alembic 和 pytest |
| `codeagent_v2/database.py` | 从 `CODEAGENT_DATABASE_URL` 创建引擎与 `Session` 工厂，检查连接和 Alembic 版本，读取身份默认值 |
| `codeagent_v2/db_schema.py` | 用 SQLAlchemy Core 定义八张表、字段、约束和九条显式索引；Alembic 共用同一份 `metadata` |
| `alembic.ini`、`alembic/env.py`、`alembic/script.py.mako`、`alembic/versions/0001_initial_schema.py` | 空库初始化和后续版本迁移 |
| `codeagent_v2/repository.py` | `PostgresRunStore`：运行与创建事件的事务写入、按组织查询、事件追加和轨迹聚合 |
| `codeagent_v2/model.py` | `RunRecord`、`RunEvent`、`RunTrace`、`RunStoreError`、幂等键冲突错误 |
| `codeagent_v2/run_api.py` | RunAPI 存储协议、内存测试实现、主键冲突重试和幂等键冲突处理 |
| `codeagent_v2/cli.py` | 装配 PostgreSQL 存储，提供 `/run`、`/get`、`/events`、`/trace` 和数据库故障提示 |
| `tests/test_migrations.py` | 空库迁移、重复迁移、八表九索引、版本未就绪和迁移失败 |
| `tests/test_postgres_run_store.py` | 事务、跨进程、事件并发、轨迹、跨组织、U+0000 和 CLI 故障验收 |
| `tests/test_run_api.py`、`tests/test_run_contract.py` | 第一章契约和内存存储回归 |
| `spec/002-database/` | 本章规格、方案、任务和实际验收记录 |
| `.gitignore` | 排除虚拟环境、缓存和本地测试产物 |

## 安装

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:PYTHONUTF8 = '1'
```

以下命令均在已激活虚拟环境的 PowerShell 中执行。

## 数据库初始化

先创建 UTF-8 的 PostgreSQL 16 数据库。连接串只写占位符，不要把真实密码提交到仓库。

```powershell
$env:PGPASSWORD = '<密码>'
psql -h <主机> -p <端口> -U <用户> -d postgres -c "CREATE DATABASE <数据库> WITH ENCODING 'UTF8' TEMPLATE template0;"

$env:CODEAGENT_DATABASE_URL = 'postgresql+psycopg://<用户>:<密码>@<主机>:<端口>/<数据库>'
python -m alembic upgrade head
python -m alembic check
```

预期结果：

- `upgrade head` 从空库创建 `agent_runs`、`agent_steps`、`run_events`、`model_calls`、`tool_calls`、`checkpoints`、`usage_records`、`audit_logs` 八张表和九条显式索引。
- 再次执行 `upgrade head` 不会重复建表，已有运行仍可查询。
- `alembic check` 输出 `No new upgrade operations detected.`。

集成测试使用 `CODEAGENT_TEST_DATABASE_URL` 指向独立的 PostgreSQL 16 测试库，并在库内为每个测试创建独立 schema。手工验收让 CLI 和 Alembic 指向同一个测试库。仅在隔离测试 schema 时设置可选的 `CODEAGENT_DATABASE_SCHEMA`。

## CLI 启动与身份

```powershell
$env:CODEAGENT_DATABASE_URL = 'postgresql+psycopg://<用户>:<密码>@<主机>:<端口>/<数据库>'
python -m codeagent_v2
```

CLI 启动时先检查数据库连接和 Alembic 版本。连接失败或版本未就绪时，命令给出原因并以非零退出码结束。

`CODEAGENT_DATABASE_URL` 也接受 `postgresql://`，应用和 Alembic 都用 psycopg 3 连接。CLI 的单条 SQL 超时为 15 秒，锁等待超时为 3 秒。数据库操作失败时，终端显示简要错误。Alembic 迁移使用独立的连接设置。

身份来自环境变量：

```powershell
$env:CODEAGENT_ORG_ID = 'local-org'   # 可省略，默认 local-org
$env:CODEAGENT_USER_ID = 'local-user' # 可省略，默认 local-user
```

以下查询都用当前 CLI 进程的 `org_id` 限定范围。跨组织访问和未知 ID 使用同一条提示「找不到这次运行」。

## CLI 命令

| 命令 | 说明 |
|---|---|
| `/run <请求>` | 校验输入，同步执行，确认运行和 `run.created` 提交后显示 `run_id`、状态和结果 |
| `/get <run_id>` | 从当前组织的 `agent_runs` 读取运行记录 |
| `/events <run_id>` | 按 `sequence` 升序显示事件；首条为序号 1 的 `run.created` |
| `/trace <run_id>` | 显示运行、步骤、事件、模型调用、工具调用和检查点；无记录的区块显示为空 |
| `/help` | 查看命令帮助 |
| `/exit`、`/quit` | 退出 CLI |

成功示例：

```text
codeagent> /run 解释任务队列
已创建运行：run_9f3a1c7b2d4e
状态：执行成功
结果：固定执行器完成：解释任务队列
codeagent> /get run_9f3a1c7b2d4e
运行：run_9f3a1c7b2d4e
状态：执行成功
结果：固定执行器完成：解释任务队列
codeagent> /events run_9f3a1c7b2d4e
事件：run_9f3a1c7b2d4e
1. run.created {"run_id": "run_9f3a1c7b2d4e", ...}
```

固定执行器约定：请求以 `[fail]` 开头（大小写不敏感）时返回固定失败，其余请求返回固定成功文本。请求不能为空，最多 2000 个字符。非法请求在执行前被拒绝，数据库记录数不变。

### 事务与事件

- `/run` 把运行记录和序号 1 的 `run.created` 放在同一事务；事件插入失败时运行记录一起回滚，终端提示「保存未确认」。
- 追加事件时先用 `SELECT ... FOR UPDATE` 锁定当前组织的运行，再取当前最大 `sequence` 加 1；`UNIQUE (run_id, sequence)` 兜底。
- 同一组织的相同 `idempotency_key` 在调用固定执行器前加锁检查。运行提交成功后，后续同键提交返回冲突。事务回滚后允许重试同一个键。`agent_runs.id` 主键冲突时重新生成 `run_id`，复用已经完成的执行结果。
- 数据库保留 JSONB。持久化入口在执行前检查嵌套 JSON 的键和值是否包含实际的 U+0000 字符；普通文本中的 `\u0000` 六个字符可以正常保存。

## 测试

```powershell
$env:PYTHONUTF8 = '1'
$env:CODEAGENT_TEST_DATABASE_URL = 'postgresql+psycopg://<用户>:<密码>@<主机>:<端口>/<测试库>'
python -m pytest tests -v
```

未配置 `CODEAGENT_TEST_DATABASE_URL` 时，PostgreSQL 集成测试会明确标记为跳过。本章验收要求配置 PostgreSQL 16 测试库并实际运行。

## PowerShell 验收示例

以下示例使用同一个测试库。把占位符替换为本地配置。

```powershell
$env:CODEAGENT_DATABASE_URL = 'postgresql+psycopg://<用户>:<密码>@<主机>:<端口>/<测试库>'
python -m alembic upgrade head
python -m alembic check

# AC-01：空库迁移后创建成功运行
"/run 解释任务队列`n/exit" | python -m codeagent_v2

# AC-02、AC-03：记下上一条输出的 run_id，重启 CLI 查询
"/get <run_id>`n/exit" | python -m codeagent_v2
"/run [fail] 数据库验收`n/exit" | python -m codeagent_v2
"/get <失败运行的 run_id>`n/exit" | python -m codeagent_v2

# AC-04：非法输入和未知 ID
"/run`n/run $('a' * 2001)`n/get run_missing`n/exit" | python -m codeagent_v2

# AC-05：重复迁移后按原 ID 查询
python -m alembic upgrade head
"/get <run_id>`n/exit" | python -m codeagent_v2

# AC-09、AC-10：事件与轨迹（测试数据由集成测试写入）
"/events <run_id>`n/trace <run_id>`n/exit" | python -m codeagent_v2

# AC-11：跨组织查询
$env:CODEAGENT_ORG_ID = 'other-org'
"/get <run_id>`n/events <run_id>`n/trace <run_id>`n/exit" | python -m codeagent_v2
```

`python -m pytest tests -v` 覆盖空库迁移、重复迁移、两个独立 CLI 进程、事件写入失败回滚、并发追加、轨迹聚合、跨组织查询、身份默认值和 U+0000 校验。事务断言使用独立数据库连接读取实际落库结果。

## SQLite 与 PostgreSQL 的差异

- PostgreSQL 使用原生 JSONB 和 TIMESTAMPTZ；SQLite 的 JSONB 是自有 BLOB 格式，日期时间通常保存为文本、实数或整数。本章保留 JSONB 设计。
- 本章用 PostgreSQL 行级锁 `SELECT ... FOR UPDATE` 协调同一运行的并发事件序号；SQLite 通过单写者机制串行化写入，不能按同样的行锁语义验收。
- 本章的迁移、事务回滚、并发事件和跨进程验收只针对 PostgreSQL 16。SQLite 没有作为本章的验收数据库。

## 本章限制

- 固定执行器仍是同步执行，只写入运行和 `run.created`。步骤、模型调用、工具调用和检查点由后续章节写入，本章用测试数据验收 `/trace` 聚合。
- `run_events` 的追加接口已在存储层和 RunAPI 提供，CLI 只读展示。
- 取消、恢复、队列和 Worker 留在对应章节。
