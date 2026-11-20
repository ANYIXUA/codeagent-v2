# 002 数据库持久化实现任务

验收编号见 `spec.md`，组件与数据流见 `plan.md`。按 T01 到 T05 推进，每项先写对应的可重复验证，再实现并记录结果。

| 任务 | 改动边界 | 对应验收 | 完成证据 |
|---|---|---|---|
| T01 SQLAlchemy 表定义与 Alembic 首个迁移 | `requirements.txt`、`codeagent_v2/database.py`、`codeagent_v2/db_schema.py`、`alembic.ini`、`alembic/` | AC-01、AC-05、AC-06 | 空库经 Alembic 升级到 `head` 后，核对 `plan.md` 中八张表的字段、默认值、主键、外键、唯一约束和九条显式索引。`alembic check` 通过，再次升级后版本一致。数据库不可用时迁移命令报错并返回非零退出码 |
| T02 Repository 与事务 | `codeagent_v2/database.py`、`codeagent_v2/model.py`、`codeagent_v2/repository.py`、`tests/test_postgres_run_store.py` | AC-02、AC-03、AC-06 至 AC-12 | 成功和失败记录完整往返。用独立连接检查 run 与 `run.created` 同时提交，以及事件插入失败时的回滚结果。并发追加事件的序号严格递增。运行、事件与轨迹按组织查询 |
| T03 RunAPI 接入存储协议 | `codeagent_v2/run_api.py`、`tests/test_run_api.py`、`tests/test_run_contract.py` | AC-02、AC-03、AC-04、AC-07 至 AC-12 | RunAPI 提供提交、运行查询、事件列表和轨迹查询。主键冲突时分配新 ID，执行器只调用一次，首条记录和关联事件完整保留 |
| T04 CLI 查询与故障提示 | `codeagent_v2/cli.py`、`tests/test_run_api.py`、`tests/test_postgres_run_store.py` | AC-01 至 AC-07、AC-09 至 AC-11 | 两个独立 CLI 进程用同一库完成提交和查询，`/events` 与 `/trace` 输出符合规格。库未就绪时启动失败，运行中故障后仍能输入 `/help` 和 `/exit` |
| T05 补齐启动文档与验收记录 | `README.md`、本文件 | AC-01 至 AC-12 | 按交付清单记录改动文件、初始化、CLI 启动、验收命令、实际输出与数据库差异 |

## 验收命令

在 PowerShell 下从仓库根目录执行。先准备独立的 PostgreSQL 16 测试数据库，将连接串中的占位符替换为该库的配置。集成测试使用 `CODEAGENT_TEST_DATABASE_URL`，手工终端验收时让 CLI 和 Alembic 指向同一个测试库。

```powershell
python -m pip install -r requirements.txt
$env:PYTHONUTF8 = '1'
$env:CODEAGENT_TEST_DATABASE_URL = 'postgresql+psycopg://<用户>:<密码>@<主机>:<端口>/<测试库>'
python -m pytest tests -v
$env:CODEAGENT_DATABASE_URL = $env:CODEAGENT_TEST_DATABASE_URL
python -m alembic upgrade head
python -m alembic check
python -m codeagent_v2
```

未配置 `CODEAGENT_TEST_DATABASE_URL` 时，数据库集成测试应明确标记为跳过。本章验收需配置测试库并实际运行这些测试。集成测试使用在测试库内创建的独立 schema，手工验收使用测试库的默认 schema。

## 验收输入与结果

| 规格编号 | 输入 | 预期结果 |
|---|---|---|
| AC-01 | 对空库执行 `python -m alembic upgrade head`，启动 CLI，输入 `/run 解释任务队列` | 返回 `run_id`，显示「执行成功」和「固定执行器完成：解释任务队列」 |
| AC-02 | `/exit` 后重新启动 CLI，输入 `/get <成功运行的 run_id>` | 显示原 ID 和原结果，集成测试比较全部 `RunResponse` 字段 |
| AC-03 | 输入 `/run [fail] 数据库验收`，退出并重启 CLI，输入 `/get <失败运行的 run_id>` | 提交返回 ID，查询显示「执行失败」和「固定执行器按要求返回失败」 |
| AC-04 | 输入 `/run`、超过 2000 字符的请求和 `/get run_missing` | 两次提交均被拒绝，记录数不变。未知 ID 显示「找不到这次运行」 |
| AC-05 | 退出 CLI，在同一个库再次执行 `python -m alembic upgrade head`，重启后查询两个 ID | 迁移成功，两条记录的 ID 和内容一致 |
| AC-06 | 验证连接或迁移失败，再模拟 CLI 运行中的读写错误和锁等待超时 | 迁移或启动返回非零退出码。运行中显示简要错误，保留 `/help` 和 `/exit` 的交互能力 |
| AC-07 | 通过真实 CLI 运行 `/run`，从数据库读取同一 ID，再启动另一 CLI 运行 `/get <run_id>` | 数据库行和两个 CLI 进程展示的执行结果一致 |
| AC-08 | 检查 `database.py`、`repository.py`、RunAPI 和 CLI 的调用关系 | 引擎与会话由 `database.py` 管理，数据库读写经 `repository.py` 执行 |
| AC-09 | 用并发连接给同一个 run 追加事件，再输入 `/events <run_id>` | `run.created` 的 `sequence` 为 1，追加事件的 `sequence` 唯一且递增，终端按 `sequence` 升序显示 |
| AC-10 | 给两个 run 分别预置关联记录，输入其中一个的 `/trace <run_id>` | 显示该 run 的记录、步骤、事件、模型调用、工具调用和检查点 |
| AC-11 | 用不同组织上下文执行 `/get <run_id>`、`/events <run_id>` 和 `/trace <run_id>` | 三条命令均提示「找不到这次运行」，查询条件均包含 `org_id` |
| AC-12 | 正常提交一次，再注入事件插入错误提交一次 | 独立连接可查到正常运行及其 `run.created`。注入错误的运行和事件均未落库 |
| AC-13 | 不设置身份变量启动 CLI 提交一次，再设置 `CODEAGENT_ORG_ID=org-manual`、`CODEAGENT_USER_ID=user-manual` 提交一次 | 默认行保存为 `local-org` / `local-user`；覆盖行保存为指定身份 |
| AC-14 | 用同一 `idempotency_key` 并发提交两次，再单独制造运行主键冲突 | 并发提交只调用一次固定执行器，保留一条运行和一条创建事件。主键冲突换新 ID 后保存已有执行结果 |
| AC-15 | 提交嵌套键和值含实际 U+0000 的输入，再提交包含普通文本 `\u0000` 的输入 | 前者被拒绝且运行数不变；后者保存并原样读回 |

## 交付清单

1. 列出修改的文件和各自的职责。
2. 给出 PostgreSQL 16 的连接配置与空库初始化命令。
3. 给出通过 `python -m codeagent_v2` 启动 CLI 的步骤。
4. 给出 PowerShell 下用 `/run`、`/get`、`/events` 和 `/trace` 验收的输入、实际输出和集成测试结果。
5. 说明 SQLite 和 PostgreSQL 在 JSONB、时间字段、并发写入及本章验收环境上的差异。

## 本次验证记录

审查修复复验使用 Python 3.12.14 和独立 PostgreSQL 16.15。`python -m pytest -p no:cacheprovider tests -q --tb=short` 完整运行，74 项通过。五项回归覆盖数据库错误输出、并发幂等、JSONB 数值往返、锁超时后继续交互和普通 PostgreSQL 连接串。另用普通连接串生成 Alembic 离线 SQL，核对八张应用表均出现在输出中。

验证日期：2026-11-20。环境：Windows + PowerShell、Python 3.12.14、PostgreSQL 16.16、SQLAlchemy 2.0.58、psycopg 3.3.9、Alembic 1.20.1、pytest 8.4.2。连接串使用占位符形式 `postgresql+psycopg://<用户>@<主机>:<端口>/<数据库>`，验收使用本机独立 PostgreSQL 16 测试实例；未记录真实凭证。

### 自动化测试

```powershell
$env:PYTHONUTF8 = '1'
$env:CODEAGENT_TEST_DATABASE_URL = 'postgresql+psycopg://<用户>@<主机>:<端口>/<测试库>'
python -m pytest tests -v
```

实际结果：`70 passed in 48.53s`（重复运行时为 48–63 秒）。PostgreSQL 集成测试在测试库内为每个测试创建独立 schema；未配置测试库时这些用例会明确跳过，本次没有跳过项。

### 迁移与启动

```powershell
$env:CODEAGENT_DATABASE_URL = 'postgresql+psycopg://<用户>@<主机>:<端口>/<空库>'
python -m alembic upgrade head
python -m alembic check
python -m alembic current
```

实际结果：空库从 ` -> 0001_initial_schema` 升级成功；`alembic check` 输出 `No new upgrade operations detected.`；`current` 输出 `0001_initial_schema (head)`。重复执行 `upgrade head` 后版本不变。八张表、字段、主键、外键、唯一约束和九条显式索引由 `test_migrations.py` 逐项核对，`idx_agent_runs_org_created` 的 `created_at DESC` 已确认。

数据库不可用时迁移退出码为 1；CLI 连接不可用时退出码为 2；未迁移数据库启动时退出码为 2，并提示 `数据库迁移版本未就绪` 和 `python -m alembic upgrade head`。

### AC-01 至 AC-15 实际结果

| 编号 | 实际命令或测试 | 实际结果 | 结论 |
|---|---|---|---|
| AC-01 | 空库迁移后 `/run 解释任务队列` | 返回 `run_0cf70b0ba092`，显示执行成功和 `固定执行器完成：解释任务队列` | 通过 |
| AC-02 | 退出后新进程 `/get run_0cf70b0ba092` | 显示同一 ID、成功状态和原结果；`test_success_round_trip_matches_response` 比较输入、状态、输出和三个时间戳 | 通过 |
| AC-03 | `/run [fail] 数据库验收` 后重启 `/get run_32fee56815ea` | 返回 ID，重启后显示执行失败和 `固定执行器按要求返回失败`；失败记录字段一致 | 通过 |
| AC-04 | `/run`、2001 字符请求、`/get run_missing` | 两次提交提示输入错误；运行数保持 2；未知 ID 提示「找不到这次运行」 | 通过 |
| AC-05 | 已有成功和失败记录时再次 `upgrade head` | 迁移成功且版本仍为 head；两个原 ID 均可查询，字段一致 | 通过 |
| AC-06 | 不可用库迁移/启动、未迁移库启动、CLI 运行中触发事件写入失败、运行中删除 `agent_runs` | 迁移退出码 1，启动退出码 2；写入显示「保存未确认」，读取显示「查询失败」，随后 `/help` 和 `/exit` 可用 | 通过 |
| AC-07 | 第一个 CLI 进程提交，第二个 CLI 进程 `/get`，独立连接查询 `agent_runs` | 两个进程显示一致；数据库行和 `run.created` 存在 | 通过 |
| AC-08 | 检查 `database.py`、`repository.py`、`run_api.py`、`cli.py` | 引擎与 Session 工厂在 `database.py`；SQLAlchemy 读写集中在 `repository.py`；CLI 只经 RunAPI | 通过 |
| AC-09 | 追加两条事件后 `/events`；12 个并发连接追加事件 | 输出序号 1、2、3，首条 `run.created`；并发测试序号为 2 至 13，唯一且递增 | 通过 |
| AC-10 | 为 `run_0cf70b0ba092` 预置步骤、模型调用、工具调用和检查点后 `/trace` | 显示该运行的步骤、3 条事件、模型调用、工具调用和检查点；另一运行不混入 | 通过 |
| AC-11 | `CODEAGENT_ORG_ID=other-org` 执行 `/get`、`/events`、`/trace` | 三条命令均显示「找不到这次运行」 | 通过 |
| AC-12 | 正常提交后独立连接查询；触发器强制 `run_events` 插入失败 | 正常运行与序号 1 的 `run.created` 同时存在；失败运行 `run_3181b9190763` 的运行数和事件数均为 0，总数保持 3/3 | 通过 |
| AC-13 | 默认启动提交；设置 `CODEAGENT_ORG_ID=org-manual`、`CODEAGENT_USER_ID=user-manual` 再提交 | 默认行保存为 `local-org` / `local-user`；覆盖行保存为 `org-manual` / `user-manual` | 通过 |
| AC-14 | 相同 `idempotency_key` 提交两次；固定 ID 冲突后重试 | 冲突后运行和事件数保持 `(1, 1)`，原结果保留；重试得到 `run_accept_idem_2`，执行器调用 1 次 | 通过 |
| AC-15 | 嵌套键含实际 U+0000 的请求；含普通文本 `\u0000` 的请求 | 前者被拒绝且运行数不变；后者 `literal_roundtrip=True` | 通过 |

### 未验证或不在本章范围

- 未使用真实模型或生产数据库凭证；模型联调和真实凭证验收不在本章范围。
- 队列、Worker、状态机、取消和恢复不在本章范围；步骤、模型调用、工具调用和检查点的实际写入由后续章节接入，本章用测试数据验收 `/trace`。
- SQLite 未作为本章验收数据库；JSONB、TIMESTAMPTZ、行级锁和并发事件只在 PostgreSQL 16 上验收。
- 手工验收通过 PowerShell 管道输入完成，未额外做人工逐键交互录屏。
