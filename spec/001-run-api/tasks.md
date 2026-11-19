# 001 RunAPI 实现任务

- 上位规格：`../../AGENT.md`
- 本章规格：`./spec.md`
- 技术方案：`./plan.md`
- 本章边界：只做 CLI 提交/查询、内存运行记录、固定执行器同步执行；排队、幂等、取消、恢复和事件追踪留给对应章节。

## 1. 任务总览

| 编号 | 任务 | 主要改动 | 对应规格编号 | 完成证据 |
|---|---|---|---|---|
| T01 | 入口与启动骨架 | `codeagent_v2/__main__.py`、`codeagent_v2/cli.py` | #1-#4（所有场景的启动载体） | `python -m codeagent_v2` 能进入提示符，`/exit` 返回 0 |
| T02 | 请求模型与校验 | `codeagent_v2/model.py` | #3，并为 #1/#2 提供合法输入判定 | 空、空白、超长被拒绝；合法请求通过 |
| T03 | 固定执行器 | `codeagent_v2/runtime.py` | #1、#2 | 同一输入稳定复现成功或失败，文本精确 |
| T04 | 内存存储与 RunAPI | `codeagent_v2/run_api.py`（新增） | #1、#2、#4（#3 由校验保护） | 成功/失败都返回 `run_id`；未知 ID 返回 `None`；非法输入不建记录 |
| T05 | CLI 命令与输出 | `codeagent_v2/cli.py` | #1-#4 | 四条场景的终端输出与 `plan.md` 完全一致 |
| T06 | 自动化测试与验收记录 | `tests/test_run_api.py`、`README.md` | #1-#4 | 测试通过；四条手工验收有实际输出和缺口记录 |

覆盖关系：

| 规格编号 | 覆盖任务 |
|---|---|
| #1 合法成功 | T02、T03、T04、T05、T06 |
| #2 合法失败仍返回 `run_id` | T02、T03、T04、T05、T06 |
| #3 非法输入不建记录 | T02、T04、T05、T06 |
| #4 未知 `run_id` | T04、T05、T06 |

## 2. 执行前检查

开始实现前先执行，不把结果算作验收任务：

1. `git status --short`。确认工作区里已有的无关删除（`spec/001-goal-and-delivery/*`、`tests/test_goal_and_delivery.py`）和未跟踪文件不会被恢复、清理或混入本章提交。
2. `python --version`。必须是 Python 3.12.x；当前代理 shell 的 `python`/`python3` 只是 WindowsApps 占位别名，不能执行，需先在 PowerShell 里提供真实 Python 3.12。
3. 确认入口文件：`codeagent_v2/__main__.py` 和 `codeagent_v2/__init__.py` 存在。
4. 确认启动命令：从仓库根目录使用 `python -m codeagent_v2`；不要使用 `python -m codeagent`、`python codeagent_v2/__main__.py` 或 `codeagent-v2`。
5. 确认测试命令：`python -m unittest discover -s tests -v`；仓库没有依赖清单，本章不引入第三方测试依赖。

如果第 2 步失败，后续所有需要运行 Python 的完成证据都标记为「未执行」，不能写成通过。

## 3. 任务明细

### T01 入口与启动骨架

- 改动边界：`codeagent_v2/__main__.py`、`codeagent_v2/cli.py`（新建最小骨架）。
- 对应规格：#1-#4（所有场景都从该入口进入）。
- 依赖：无。
- 实施要点：
  1. `__main__.py` 只做入口委托：`from .cli import main`，然后 `raise SystemExit(main())`。
  2. `cli.main()` 配置 UTF-8 标准输入输出，创建一次 store、executor、RunAPI，然后进入 `run_repl`。
  3. `run_repl(api, stdin, stdout)` 使用可注入流，打印 banner 和 `codeagent> ` 提示符，先支持 `/help`、`/exit`、`/quit` 和 EOF。
  4. 每次写输出后 flush，保证真实终端和管道测试都能及时读到。
- 完成证据：
  - `python --version` 输出 Python 3.12.x。
  - 在仓库根目录执行 `$env:PYTHONUTF8='1'; python -m codeagent_v2`，显示 `RunAPI CLI（内存模式）。输入 /help 查看命令。` 和 `codeagent> `。
  - 输入 `/exit` 后显示 `再见。`，`$LASTEXITCODE` 为 0。
  - 确认仓库没有 `pyproject.toml`/`setup.py`/`setup.cfg`，文档中没有出现不存在的控制台命令。
- 当前状态：已完成。在 conda 环境 `dev`（Python 3.13.15）验证：启动显示 banner 和提示符，`/exit` 退出码 0；同一套测试在 CPython 3.12.14 下也通过。

### T02 请求模型与校验

- 改动边界：`codeagent_v2/model.py`。
- 对应规格：#3；同时保证 #1/#2 的合法输入能进入执行。
- 依赖：无。
- 实施要点：
  1. 定义不可变 `RunRecord`，字段为 `run_id`、`request`、`status`、`result`、`error`。
  2. 定义 `ValidationError(field, reason)`。
  3. 定义 `MAX_REQUEST_CHARS = 2000` 和 `validate_request(request) -> str`。
  4. 先判断类型，再 `strip()`，再判断空和长度；校验通过后返回去掉首尾空白的文本。
  5. 错误文本固定为 `request` + `不能为空` / `不能超过 2000 个字符`；CLI 负责拼接终端文案。
- 完成证据：
  - 空串、纯空白、2001 字符都抛出 `ValidationError`，`field == "request"`。
  - 1 字符和 2000 字符通过，返回的文本不含首尾空白。
  - 非字符串输入抛出 `ValidationError("request", "必须是字符串")`。
  - 校验函数本身不生成 ID、不写 store。
- 自动测试建议：`test_validate_rejects_empty`、`test_validate_rejects_whitespace`、`test_validate_accepts_2000_chars`、`test_validate_rejects_2001_chars`。

### T03 固定执行器

- 改动边界：`codeagent_v2/runtime.py`。
- 对应规格：#1、#2。
- 依赖：无（`ExecutionOutcome` 和 `FixedExecutor` 都在本任务定义）。
- 实施要点：
  1. 定义 `Executor` 协议和 `ExecutionOutcome(ok, result, error)`。
  2. 定义 `FixedExecutor.execute(request)`。
  3. 请求以 `[fail]` 开头（大小写不敏感）时返回失败：`ok=False`、`result=None`、`error="固定执行器按要求返回失败"`。
  4. 其他请求返回成功：`ok=True`、`result=f"固定执行器完成：{request}"`、`error=None`。
  5. 不调用模型、不访问网络、不读写文件、不 sleep、不使用随机数。
- 完成证据：
  - `给出一个 Python 冒泡排序示例` 返回 `固定执行器完成：给出一个 Python 冒泡排序示例`。
  - `[fail] 触发失败用例`、`[FAIL] 任意内容`、单独的 `[fail]` 都返回固定失败原因。
  - 同一输入连续执行多次结果完全一致。
- 自动测试建议：`test_fixed_executor_success`、`test_fixed_executor_failure_marker`、`test_fixed_executor_is_deterministic`。

### T04 内存存储与 RunAPI

- 改动边界：`codeagent_v2/run_api.py`（新增）。
- 对应规格：#1、#2、#4；非法输入路径对应 #3。
- 依赖：T02、T03。
- 实施要点：
  1. 实现 `InMemoryRunStore`：内部 `dict[str, RunRecord]`，提供 `put`、`get`、`count`。
  2. store 由 `cli.main()` 创建一次，不使用模块级全局变量；每个测试创建新 store。
  3. 实现 `RunAPI(store, executor, id_factory=None)`，默认 ID 为 `run_` + `secrets.token_hex(6)`，碰撞时重试。
  4. `submit_run` 顺序固定：`validate_request`（含去空白）-> 生成 ID -> 同步执行 -> 构造最终记录 -> `put` -> 返回记录。
  5. 执行器抛异常时捕获并转成 `failed` 记录，`error` 以 `执行器异常：<类型>: <消息>` 开头，仍然返回 `run_id`。
  6. `get_run` 只查 store；未知 ID 返回 `None`，不抛异常、不重新执行。
  7. 重复提交同一请求产生不同 `run_id`；本章不做幂等。
- 完成证据：
  - 成功请求返回正则匹配 `^run_[0-9a-f]{12}$` 的 ID，记录为 `succeeded`，查询到同一对象内容，store 计数为 1。
  - `[fail]` 请求仍返回 `run_id`，记录为 `failed`，原因正确，查询一致，store 计数为 1。
  - 注入一个抛异常的 executor，仍返回 `run_id` 和 `failed` 记录。
  - 空请求/超长请求抛出 `ValidationError`，store 计数为 0，`id_factory` 未被调用。
  - 查询未知 ID 返回 `None`；新建一个 `RunAPI` 用新 store 查旧 ID 仍返回 `None`。
- 自动测试建议：`test_submit_success_returns_run_id`、`test_submit_failure_still_returns_run_id`、`test_executor_exception_still_returns_run_id`、`test_invalid_submission_creates_no_record`、`test_get_unknown_returns_none`、`test_records_are_session_local`。

### T05 CLI 命令与输出

- 改动边界：`codeagent_v2/cli.py`。
- 对应规格：#1-#4。
- 依赖：T01、T02、T03、T04。
- 实施要点：
  1. 解析一行输入；空行忽略；命令为 `/run`、`/get`、`/help`、`/exit`、`/quit`。
  2. `/run` 只把参数交给 `RunAPI.submit_run()`，CLI 不直接调用执行器或 store。
  3. `/get` 只把 ID 交给 `RunAPI.get_run()`，CLI 不直接访问 store。
  4. 捕获 `ValidationError`，输出 `提交失败：request <原因>（未创建运行记录）`。
  5. 按 `plan.md` 的格式渲染提交结果、查询结果、未知 ID、未知命令和用法提示。
  6. `/run` 无参数时传空串给 RunAPI，走统一校验；`/get` 无参数时只打印用法，不查询。
  7. 处理 EOF 和 KeyboardInterrupt，保证终端能正常退出。
- 完成证据：
  - 用 `StringIO` 驱动 CLI，并向 RunAPI 注入固定 `id_factory`（如 `run_000000000001`），四条场景输出与第 4 节逐字一致。
  - 执行失败时提交输出仍包含 `已创建运行：<run_id>`。
  - 非法提交输出不含 `已创建运行`，store 计数保持 0。
  - 未知 ID 输出 `找不到这次运行：<run_id>`，随后仍能继续输入命令。
  - 重新创建 RunAPI/store 后查询旧 ID 输出 `找不到这次运行：<run_id>`。
- 自动测试建议：`test_cli_success_transcript`、`test_cli_failure_transcript`、`test_cli_invalid_transcript`、`test_cli_unknown_run_transcript`、`test_cli_session_restart_loses_records`。

### T06 自动化测试与验收记录

- 改动边界：`tests/test_run_api.py`、`README.md`。
- 对应规格：#1-#4。
- 依赖：T01-T05。
- 实施要点：
  1. 用标准库 `unittest` 写测试，不新增第三方依赖。
  2. 单元测试覆盖 T02-T04 的完成证据。
  3. CLI 测试用 `StringIO` + 固定 ID 验证四条终端文本；另加一个真实入口冒烟测试：`python -m codeagent_v2`、`/exit`、退出码 0。
  4. 在 README 写清安装前提（Python 3.12）、启动命令 `python -m codeagent_v2`、测试命令 `python -m unittest discover -s tests -v`、本章限制（内存记录、退出即失效、固定执行器）。
  5. 按第 4 节在 PowerShell 手工跑四条场景，保留实际输出。
  6. 验收记录写清 commit、环境、Python 版本、命令、实际输出、结论和已知缺口；没有实际运行的项标记「未执行」。
- 完成证据：
  - `python -m unittest discover -s tests -v` 全部通过。
  - 真实入口冒烟测试通过，中文 UTF-8 输出正常。
  - 四条场景有实际终端输出，且与第 4 节预期一致。
  - README 的启动命令与 `codeagent_v2/__main__.py` 对应，没有出现 `python -m codeagent` 或 `codeagent-v2`。
- 当前状态：已完成。在 conda 环境 `dev` 下 `python -m unittest discover -s tests -v` 共 26 项通过；四场景已在同一 CLI 会话验收通过。

## 4. 四条验收场景

说明：

- `codeagent> ` 行表示用户输入，其余行是预期终端输出。
- `run_xxxxxxxxxxxx` 是占位符，实际为 `run_` + 12 位小写十六进制字符。
- 自动化 CLI 测试注入固定 `id_factory`，可用 `run_000000000001` 得到完全确定的文本；手工验收使用实际生成的 ID。
- 所有命令都在同一个 CLI 会话内执行。

### 场景 1：合法输入，执行成功（规格 #1）

输入与预期输出：

```text
codeagent> /run 给出一个 Python 冒泡排序示例
已创建运行：run_xxxxxxxxxxxx
状态：执行成功
结果：固定执行器完成：给出一个 Python 冒泡排序示例
codeagent> /get run_xxxxxxxxxxxx
运行：run_xxxxxxxxxxxx
状态：执行成功
结果：固定执行器完成：给出一个 Python 冒泡排序示例
```

判定：

- 提交输出包含可复制的 `run_id`。
- 查询使用同一 `run_id`，状态为 `执行成功`，结果与提交时一致。
- 该次会话 store 中记录数为 1。

### 场景 2：合法输入，执行失败（规格 #2）

输入与预期输出：

```text
codeagent> /run [fail] 触发失败用例
已创建运行：run_xxxxxxxxxxxx
状态：执行失败
原因：固定执行器按要求返回失败
codeagent> /get run_xxxxxxxxxxxx
运行：run_xxxxxxxxxxxx
状态：执行失败
原因：固定执行器按要求返回失败
```

判定：

- 执行失败不影响提交：第一行仍有 `已创建运行：<run_id>`。
- 查询显示 `执行失败` 和固定原因，不显示成功结果。
- 该次会话 store 中记录数为 1。

### 场景 3：非法输入，不创建运行记录（规格 #3）

主用例：

```text
codeagent> /run
提交失败：request 不能为空（未创建运行记录）
```

超长补充用例：

```text
codeagent> /run <2001 个字符的请求>
提交失败：request 不能超过 2000 个字符（未创建运行记录）
```

判定：

- 输出中不出现 `已创建运行`，也不出现 `run_id`。
- store 记录数保持 0，`id_factory` 没有被调用。
- 输入纯空白（例如 `/run    `）与空请求结果相同。
- 失败后 CLI 不退出，仍可继续输入命令。

### 场景 4：查询未知 run_id（规格 #4）

输入与预期输出：

```text
codeagent> /get run_000000000000
找不到这次运行：run_000000000000
```

会话失效补充验证：

```text
codeagent> /exit
再见。
PS> python -m codeagent_v2
RunAPI CLI（内存模式）。输入 /help 查看命令。
codeagent> /get run_xxxxxxxxxxxx
找不到这次运行：run_xxxxxxxxxxxx
```

`PS>` 行表示退出后在 PowerShell 重新启动 CLI。

判定：

- 未知 ID 输出固定短语 `找不到这次运行`，并带上查询的 ID。
- 查询失败后 CLI 不退出，仍可继续输入命令。
- 退出并重启 CLI 后，旧会话的 `run_id` 查不到，符合本章「记录只在当前进程内存」的范围。

## 5. 启动命令与入口文件核对

| 核对项 | 结果 |
|---|---|
| 入口文件 | `codeagent_v2/__main__.py` 存在；实现后委托给 `cli.main()` |
| 包文件 | `codeagent_v2/__init__.py` 存在 |
| 构建配置 | 没有 `pyproject.toml`、`setup.py`、`setup.cfg` |
| 标准启动命令 | 在仓库根目录执行 `python -m codeagent_v2` |
| 禁止写法 | `python -m codeagent`（包名错误）、`python codeagent_v2/__main__.py`（绕过包上下文）、`codeagent-v2`（没有安装入口） |
| 测试命令 | `python -m unittest discover -s tests -v` |
| UTF-8 设置 | PowerShell 中设置 `$env:PYTHONUTF8='1'`，代码同时重配置标准流 |
| 当前环境 | 已创建 conda 环境 `dev`（Python 3.13.15）；本次用 `conda activate dev` 完成测试和验收。目标版本仍为 Python 3.12，CPython 3.12.14 也已通过同一套测试 |

结论：启动命令与仓库入口文件匹配；已在 conda 环境 `dev`（Python 3.13.15）实际启动，并在同一 CLI 会话完成四场景验收。目标版本 Python 3.12 也已用 CPython 3.12.14 通过同一套测试。

## 6. 验收记录模板

实现完成后按以下格式记录，实际输出要粘贴原文，不能只写「符合预期」：

```text
验收日期：
commit：
环境：Windows + PowerShell，Python 版本：
启动命令：python -m codeagent_v2
自动化：python -m unittest discover -s tests -v
  结果：
场景 1 输入 / 实际输出 / 结论：
场景 2 输入 / 实际输出 / 结论：
场景 3 输入 / 实际输出 / 结论：
场景 4 输入 / 实际输出 / 结论：
已知缺口：
```

如果 Python 3.12 仍不可用，记录为：

```text
状态：未执行
原因：当前环境没有可用的 Python 3.12
已完成的静态检查：
待补证据：
```

## 7. 实际验收记录（2026-11-19）

- 基线 commit：`244258c`（本轮实现改动尚未提交）
- 环境：Windows + PowerShell；conda 环境 `dev`，Python 3.13.15；`PYTHONUTF8=1`
- 启动命令：`conda activate dev` 后执行 `python -m codeagent_v2`
- 自动化命令：`python -m unittest discover -s tests -v`
  - 结果：`Ran 26 tests in 0.366s`，`OK`
- 手工验收：同一个 CLI 进程内依次执行四场景，退出码 0，stderr 为空。终端转录中 `codeagent> ` 后的命令是实际发送的输入。

```text
RunAPI CLI（内存模式）。输入 /help 查看命令。
codeagent> /run 给出一个 Python 冒泡排序示例
已创建运行：run_58ab0866903f
状态：执行成功
结果：固定执行器完成：给出一个 Python 冒泡排序示例
codeagent> /get run_58ab0866903f
运行：run_58ab0866903f
状态：执行成功
结果：固定执行器完成：给出一个 Python 冒泡排序示例
codeagent> /run [fail] 触发失败用例
已创建运行：run_943d7d2b5e82
状态：执行失败
原因：固定执行器按要求返回失败
codeagent> /get run_943d7d2b5e82
运行：run_943d7d2b5e82
状态：执行失败
原因：固定执行器按要求返回失败
codeagent> /run
提交失败：request 不能为空（未创建运行记录）
codeagent> /get run_000000000000
找不到这次运行：run_000000000000
codeagent> /exit
再见。
```

会话元数据：

```text
success_run_id=run_58ab0866903f
failure_run_id=run_943d7d2b5e82
exit_code=0
stderr=''
```

重启 CLI 后查询旧 ID：

```text
RunAPI CLI（内存模式）。输入 /help 查看命令。
codeagent> 找不到这次运行：run_58ab0866903f
codeagent> 再见。
```

补充：自动化测试 `test_cli_overlong_request` 验证 2001 字符请求输出 `提交失败：request 不能超过 2000 个字符（未创建运行记录）`，且 store 记录数为 0。

结论：#1-#4 全部通过。已知缺口：本章范围内无；内存记录、退出失效、固定执行器均为本章设计行为。

## 8. 提交边界

- 本章实现只改 `codeagent_v2/__main__.py`、`codeagent_v2/cli.py`、`codeagent_v2/model.py`、`codeagent_v2/runtime.py`、`codeagent_v2/run_api.py`、`tests/test_run_api.py` 和 `README.md`。
- `codeagent_v2/verifier.py` 不属于本章，除非出现明确的导入需要，否则不动。
- 不恢复、不清理工作区中已有的 `spec/001-goal-and-delivery/*` 和 `tests/test_goal_and_delivery.py` 删除。
- 不把固定执行器输出冒充真实模型联调结果。
- 提交前执行 `git diff --check` 和测试命令；提交说明关联规格编号，例如：
  - `feat(run-api): add in-memory submit and query API (#1,#2,#4)`
  - `test(run-api): cover four acceptance scenarios (#1-#4)`
  - `docs(run-api): record startup and acceptance evidence (#1-#4)`
