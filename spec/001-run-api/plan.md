# 001 RunAPI 技术方案

- 上位规格：`../../AGENT.md`
- 本章规格：`./spec.md`
- 交付形态：同一交互式 CLI 进程内的 RunAPI + 内存运行记录 + 固定执行器同步执行

## 1. 范围与结论

本章只实现 `spec.md` 定义的 API 契约，并把 CLI 接到这份契约上：

1. 用户在同一个交互式 CLI 会话里用 `/run <请求>` 提交，用 `/get <run_id>` 查询。
2. `RunAPI.submit_run()` 先校验请求；校验通过后生成 `run_id`，同步调用固定执行器，最后把最终记录写入内存 dict 并返回。
3. 合法请求一定返回 `run_id`。执行器返回失败，甚至执行器抛出异常，也先生成并保留运行记录，再返回 `run_id`。
4. 非法请求在生成 `run_id`、写记录之前被拒绝，终端说明哪个字段不合法。
5. 运行记录只存在于当前 CLI 进程。退出 CLI 后，新进程里查不到旧 `run_id`。

方案摘要：

- CLI 只调用 `RunAPI.submit_run()` 和 `RunAPI.get_run()`，不直接接触存储或执行器。
- 运行记录保存在 `cli.main()` 创建的 `InMemoryRunStore` 实例里，底层是进程内 `dict[str, RunRecord]`；不写文件、不连数据库。
- 固定执行器同步执行：以 `[fail]` 开头的请求返回固定失败，其余请求返回固定成功文本；同一输入结果稳定。

本章的 RunAPI 是进程内 Python 服务边界，不是 HTTP 服务。CLI 与 RunAPI 同进程，才能按规格同步执行并在执行结束后拿到 `run_id`。后续章节可以在同一语义上增加 HTTP、数据库或队列适配层，不改变 CLI 的提交/查询含义。

本章明确不做：

- 队列、Worker、异步提交
- 幂等键和重复请求去重
- 取消、暂停、恢复、重放
- 事件流、trace、检查点
- 数据库、文件持久化、跨进程查询
- 真实模型、工具调用、交付校验
- 权限、组织隔离、多租户

## 2. 工作区核对

| 项目 | 当前状态 | 对方案的影响 |
|---|---|---|
| 包入口 | `codeagent_v2/__main__.py` 已实现入口委托 | 从仓库根目录执行 `python -m codeagent_v2` 进入本章 CLI |
| 包初始化 | `codeagent_v2/__init__.py` 存在 | `python -m codeagent_v2` 可以按包方式加载 |
| 现有模块 | `model.py`、`runtime.py` 已按本章实现；`verifier.py` 未动 | 本章只使用 `model.py`、`runtime.py` |
| 测试文件 | `tests/test_run_api.py` 已实现 26 项 `unittest` | 覆盖四条验收场景和入口冒烟测试 |
| 构建配置 | 没有 `pyproject.toml`、`setup.py`、`setup.cfg` | 不存在 `codeagent-v2` 控制台命令，只能从仓库根目录用模块方式启动 |
| README | 已补启动、测试和限制说明 | 启动命令与入口文件一致 |
| Python 环境 | 已创建 conda 环境 `dev`（Python 3.13.15）；目标版本为 Python 3.12，CPython 3.12.14 也已通过同一套测试 | 日常验收使用 `conda activate dev`；干净环境按 README 准备 Python 3.12 |
| Git 工作区 | 存在与本章无关的删除和未跟踪改动 | 实施时只提交本章文件，不恢复或清理无关改动 |

启动命令与入口文件的对应关系：

- 仓库有 `codeagent_v2/__main__.py`，所以从仓库根目录执行 `python -m codeagent_v2` 会进入本章 CLI。
- 不能用 `python -m codeagent`：包名是 `codeagent_v2`。
- 不能用 `python codeagent_v2/__main__.py`：会绕过包上下文，相对导入不可靠。
- 当前没有安装入口，不能写成 `codeagent-v2` 或 `codeagent` 可执行命令。
- 测试从仓库根目录执行 `python -m unittest discover -s tests -v`；测试使用标准库 `unittest`，不新增第三方依赖。

## 3. 组件与数据流

| 文件 | 职责 |
|---|---|
| `codeagent_v2/model.py` | `RunRecord`、`ValidationError`、请求校验常量和函数 |
| `codeagent_v2/runtime.py` | `ExecutionOutcome`、`FixedExecutor` |
| `codeagent_v2/run_api.py`（新增） | `InMemoryRunStore`、`RunAPI`、`run_id` 生成 |
| `codeagent_v2/cli.py`（新增） | 交互循环、命令解析、终端渲染、UTF-8 配置 |
| `codeagent_v2/__main__.py` | 进程入口，调用 `cli.main()` |
| `tests/test_run_api.py` | 单元测试和 CLI 会话测试 |

依赖方向：

```text
cli.py ──> run_api.py ──> model.py
                    └──> runtime.py（Executor 接口）
```

数据流：

```text
用户输入 /run <请求>
  -> cli 解析出请求文本
  -> RunAPI.submit_run(request)
       -> 校验
       -> 生成 run_id
       -> FixedExecutor.execute(request)   # 同步
       -> 构造最终 RunRecord
       -> InMemoryRunStore.put(record)
       -> 返回 RunRecord
  -> cli 打印 run_id、状态、结果/原因

用户输入 /get <run_id>
  -> cli 解析出 run_id
  -> RunAPI.get_run(run_id)
       -> InMemoryRunStore.get(run_id)
       -> 返回 RunRecord 或 None
  -> cli 打印记录或「找不到这次运行」
```

查询不会再次执行请求，只读取已有记录。

## 4. 数据结构和 API 契约

### 4.1 运行记录

```python
@dataclass(frozen=True)
class RunRecord:
    run_id: str
    request: str                 # 校验后保存的文本，已去掉首尾空白
    status: Literal["succeeded", "failed"]
    result: str | None           # 成功时的固定执行器结果
    error: str | None            # 失败时的原因
```

约束：

- `status == "succeeded"` 时，`result` 非空，`error is None`。
- `status == "failed"` 时，`error` 非空，`result is None`。
- `succeeded` 只表示本章固定执行器正常返回，不表示用户交付物符合目标；交付校验留给后续章节。

### 4.2 校验错误

```python
class ValidationError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
```

本章只有 `field="request"`。`validate_request(request) -> str` 先判断类型，再去掉首尾空白，再判断空和长度，校验通过后返回规范化文本。校验规则：

| 条件 | 结果 |
|---|---|
| 不是字符串 | 抛出 `ValidationError("request", "必须是字符串")` |
| 去掉首尾空白后为空 | 抛出 `ValidationError("request", "不能为空")` |
| 去掉首尾空白后超过 2000 个字符 | 抛出 `ValidationError("request", "不能超过 2000 个字符")` |
| 1 到 2000 个字符 | 通过，返回去掉首尾空白后的文本 |

长度按 Python 字符串的字符数计算。一次请求只支持一行文本。校验发生在生成 `run_id` 之前，所以校验失败不会留下记录。

### 4.3 执行结果

```python
@dataclass(frozen=True)
class ExecutionOutcome:
    ok: bool
    result: str | None
    error: str | None
```

`FixedExecutor` 实现统一的执行器接口：

```python
class Executor(Protocol):
    def execute(self, request: str) -> ExecutionOutcome: ...
```

### 4.4 RunAPI

```python
class RunAPI:
    def __init__(
        self,
        store: InMemoryRunStore,
        executor: Executor,
        id_factory: Callable[[], str] | None = None,
    ) -> None: ...

    def submit_run(self, request: str) -> RunRecord: ...
    def get_run(self, run_id: str) -> RunRecord | None: ...
```

| 方法 | 输入 | 返回 | 异常 |
|---|---|---|---|
| `submit_run` | 原始请求文本 | 合法的成功或失败记录，必含 `run_id` | 仅校验失败时抛出 `ValidationError` |
| `get_run` | `run_id` | 对应记录；不存在时返回 `None` | 不因未知 ID 抛异常 |

`submit_run` 的关键顺序：

```python
def submit_run(self, request: str) -> RunRecord:
    text = validate_request(request)  # 类型、空白、长度；返回规范化文本
    run_id = self._new_run_id()
    try:
        outcome = self._executor.execute(text)   # 同步
    except Exception as exc:
        outcome = ExecutionOutcome(
            ok=False,
            result=None,
            error=f"执行器异常：{type(exc).__name__}: {exc}",
        )
    record = RunRecord(
        run_id=run_id,
        request=text,
        status="succeeded" if outcome.ok else "failed",
        result=outcome.result if outcome.ok else None,
        error=outcome.error if not outcome.ok else None,
    )
    self._store.put(record)
    return record
```

行为说明：

- 同一条请求重复提交会产生新的 `run_id`，本章不做幂等。
- 执行器异常被转换成失败记录，仍然返回 `run_id`，守住「合法请求即使执行失败也要返回 `run_id`」。
- 记录只在执行结束后写入，本章不暴露 `running` 状态，也不做排队。
- 查询返回同一条不可变记录，不重新执行。

### 4.5 run_id

- 默认格式：`run_` + 12 位小写十六进制字符，例如 `run_9f3a1c7b2d4e`。
- 默认由 `secrets.token_hex(6)` 生成。
- 写入前检查存储中是否已存在；碰撞则重新生成。
- 测试可以注入 `id_factory`，用固定值获得完全确定的 CLI 输出。

## 5. 内存运行记录

`InMemoryRunStore` 是本章唯一的「数据库」：

```python
class InMemoryRunStore:
    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}

    def put(self, record: RunRecord) -> None: ...
    def get(self, run_id: str) -> RunRecord | None: ...
    def count(self) -> int: ...
```

- 存储位置：CLI 进程内存中的 `dict[str, RunRecord]`，不写文件、不连数据库。
- 生命周期：`cli.main()` 启动时创建一个 store，整个交互会话共用；进程退出即丢弃。
- 作用域：每个 CLI 进程独立。重新启动 CLI 后，旧 `run_id` 一定查不到。
- 并发：本章单线程同步执行，不加锁。
- 测试：每个用例创建新的 store，避免用例之间互相污染。
- 不使用模块级全局 store，避免多次启动或测试之间共享状态。

## 6. 固定执行器

`FixedExecutor` 只用于本章复现成功和失败，不调用模型、不访问网络、不读写文件、不等待。

| 输入（去掉首尾空白后） | `ok` | `result` | `error` |
|---|---|---|---|
| 以 `[fail]` 开头，大小写不敏感 | `False` | `None` | `固定执行器按要求返回失败` |
| 其他合法请求 | `True` | `固定执行器完成：<请求文本>` | `None` |

示例：

- `给出一个 Python 冒泡排序示例` -> `固定执行器完成：给出一个 Python 冒泡排序示例`
- `[fail] 触发失败用例` -> 失败，原因 `固定执行器按要求返回失败`
- `[FAIL] 任意内容` -> 同样失败
- 只有 `[fail]` 本身也是合法请求，会得到失败记录和 `run_id`

`[fail]` 是本章固定执行器的测试约定，不是面向用户的正式功能。固定执行器无随机、无延时，同一输入必得同一结果。

## 7. CLI 接入

### 7.1 启动和提示符

从仓库根目录执行：

```powershell
$env:PYTHONUTF8 = '1'
python -m codeagent_v2
```

启动后显示：

```text
RunAPI CLI（内存模式）。输入 /help 查看命令。
codeagent> 
```

`cli.main()` 负责：

1. 把 `sys.stdin`、`sys.stdout`、`sys.stderr` 配置为 UTF-8；不支持重配置的流跳过。
2. 创建 `InMemoryRunStore`、`FixedExecutor`、`RunAPI` 各一次。
3. 调用 `run_repl(api, stdin=sys.stdin, stdout=sys.stdout)`。
4. 输出后立即 flush，保证交互终端和管道测试都能及时看到结果。

`run_repl` 接受可注入的输入/输出流，测试可以用 `io.StringIO` 驱动完整会话，验证确定的终端文本。

### 7.2 命令

| 命令 | 行为 |
|---|---|
| `/run <请求>` | 把 `<请求>` 原样（仅去掉首尾空白）提交给 `RunAPI.submit_run()` |
| `/get <run_id>` | 调用 `RunAPI.get_run()` 并打印结果 |
| `/help` | 显示命令帮助 |
| `/exit`、`/quit` | 退出 CLI |
| 空行 | 忽略，不提交、不创建记录 |
| 其他 | 提示未知命令，不调用 RunAPI |

`/run` 后面没有内容时，把空字符串交给 RunAPI，由统一校验返回「不能为空」，这样非法输入场景只有一条校验路径。

### 7.3 输出格式

提交合法请求（无论执行成功还是失败）都先打印运行 ID：

```text
已创建运行：<run_id>
状态：执行成功
结果：<result>
```

```text
已创建运行：<run_id>
状态：执行失败
原因：<error>
```

查询已有记录：

```text
运行：<run_id>
状态：执行成功
结果：<result>
```

```text
运行：<run_id>
状态：执行失败
原因：<error>
```

非法提交：

```text
提交失败：request 不能为空（未创建运行记录）
提交失败：request 不能超过 2000 个字符（未创建运行记录）
```

查询不存在的 ID：

```text
找不到这次运行：<run_id>
```

其他终端行为：

- `/get` 缺少参数：`用法：/get <run_id>`
- `/run` 缺少参数：仍交给 RunAPI 校验，输出 `提交失败：request 不能为空（未创建运行记录）`
- 未知命令：`未知命令：<命令>。输入 /help 查看可用命令。`
- 退出：`再见。`，进程退出码 0
- 读到 EOF（例如管道结束）：正常退出

### 7.4 CLI 与 RunAPI 的调用边界

- CLI 不直接访问 `InMemoryRunStore`，也不直接调用 `FixedExecutor`。
- 提交只经过 `RunAPI.submit_run()`，查询只经过 `RunAPI.get_run()`。
- 终端文案由 CLI 统一渲染，RunAPI 只返回领域对象和异常，不拼终端字符串。

核心调用只有两处：

```python
# /run
try:
    record = api.submit_run(payload)
except ValidationError as exc:
    stdout.write(f"提交失败：{exc.field} {exc.reason}（未创建运行记录）\n")
else:
    render_created(record, stdout)

# /get
run_id = payload.strip()
if not run_id:
    stdout.write("用法：/get <run_id>\n")
else:
    record = api.get_run(run_id)
    if record is None:
        stdout.write(f"找不到这次运行：{run_id}\n")
    else:
        render_existing(record, stdout)
```

`render_created` 打印 `已创建运行：<run_id>`，`render_existing` 打印 `运行：<run_id>`；两者随后都按状态打印结果或原因。

## 8. 错误和失败矩阵

| 情况 | RunAPI 行为 | 终端表现 | 是否创建记录 |
|---|---|---|---|
| 合法请求，执行成功 | 返回 `succeeded` 记录 | `已创建运行`、`执行成功`、结果 | 是 |
| 合法请求，执行器返回失败 | 返回 `failed` 记录 | `已创建运行`、`执行失败`、原因 | 是 |
| 合法请求，执行器抛异常 | 转成 `failed` 记录并返回 | `已创建运行`、`执行失败`、异常原因 | 是 |
| 请求为空或只有空白 | 抛出 `ValidationError` | `提交失败：request 不能为空（未创建运行记录）` | 否 |
| 请求超过 2000 字符 | 抛出 `ValidationError` | `提交失败：request 不能超过 2000 个字符（未创建运行记录）` | 否 |
| 查询未知 `run_id` | 返回 `None` | `找不到这次运行：<run_id>` | 否 |
| 未知命令 / `/get` 缺参数 | 不调用 RunAPI | 帮助或用法提示 | 否 |
| CLI 退出后查询旧 ID | 新进程 store 为空，返回 `None` | `找不到这次运行：<run_id>` | 否 |

关键验收点：执行失败不是提交失败；失败场景的提交输出里必须出现 `已创建运行：<run_id>`。

## 9. 验证方法

### 9.1 自动化测试

单元测试覆盖：

| 规格编号 | 测试内容 |
|---|---|
| #1 | 合法请求成功：返回 `run_id`，记录为 `succeeded`，结果文本正确，查询到同一条记录 |
| #2 | `[fail]` 请求：仍返回 `run_id`，记录为 `failed`，原因正确，查询到同一条记录 |
| #2 | 执行器抛异常：仍返回 `run_id`，记录为 `failed` |
| #3 | 空串、纯空白、2001 字符被拒绝；store 计数保持 0；`id_factory` 未被调用 |
| #4 | 未知 ID 返回 `None`；新 store 查询旧 ID 返回 `None` |
| #1-#4 | CLI 用 `StringIO` + 固定 `id_factory` 复现四条终端输出 |

CLI 集成测试使用真实的 `python -m codeagent_v2` 入口，至少验证：

- 启动后出现提示符和 banner
- 中文输入输出按 UTF-8 正常显示
- `/exit` 能结束进程并返回 0

### 9.2 手工验收

按 `tasks.md` 的四条场景逐条执行，保留实际终端输出。启动命令和自动化命令：

```powershell
$env:PYTHONUTF8 = '1'
python -m unittest discover -s tests -v
python -m codeagent_v2
```

本章已在 conda 环境 `dev`（Python 3.13.15）执行上述命令：26 项测试通过；四场景在同一 CLI 会话验收通过。同一套测试也已在 CPython 3.12.14 下通过。实际输出见 `tasks.md` 第 7 节。

## 10. 设计决定与风险

| 决定 | 理由 | 需要确认 |
|---|---|---|
| RunAPI 采用进程内 Python 接口，不开 HTTP | 规格要求同一交互式 CLI 会话、同步执行、退出即失效；HTTP 和持久化留给后续章节 | 是 |
| 命令使用 `/run`、`/get` | 与普通请求文本区分，便于解析和验收 | 是 |
| 固定失败约定为 `[fail]` 前缀 | 成功/失败可确定复现，不依赖模型或随机数 | 是 |
| 请求上限 2000 字符 | 终端单行输入有界，校验可观察 | 是 |
| 记录执行结束后写入内存 | 本章不引入排队和状态机，查询只看到终态 | 是 |
| 测试使用标准库 `unittest` | 仓库没有依赖清单，干净环境不需要额外安装 | 可选 |

规格同步：CLI 命令名、错误文案、2000 字上限和 `[fail]` 约定属于本章新增的可观察契约。这些契约已按确认后的 `plan.md` 和 `tasks.md` 落地；建议在后续规格维护时同步回 `spec.md`，避免文档分叉。

主要风险：

- 目标版本是 Python 3.12；当前 conda 环境 `dev` 为 Python 3.13.15，已通过同一套测试。干净环境仍需按 README 准备 Python 3.12。
- README 已补启动/测试/限制说明；后续章节修改命令时需同步更新。
- 记录只在内存中，退出即丢失是本章预期行为，不应被误判为缺陷。
- 固定执行器的 `[fail]` 约定仅在测试中有意义，不能当作真实执行器的失败策略。
