# codeagent-v2

第一章实现一个在终端中运行的 RunAPI：用户在同一个交互式 CLI 会话里提交请求，并用返回的 `run_id` 查询结果。

CLI 直接调用进程内的 RunAPI，由固定执行器同步完成。运行记录保存在当前会话的内存中，退出后失效。

## 环境

- Python 3.12
- Windows + PowerShell
- 运行依赖是 Pydantic，测试运行器为 `unittest`

## 启动

在仓库根目录执行：

```powershell
python -m pip install -r requirements.txt
$env:PYTHONUTF8 = '1'
python -m codeagent_v2
```

CLI 入口是 `codeagent_v2/__main__.py`。每个 CLI 会话创建一次 RunAPI 和内存存储。

## CLI 命令

| 命令 | 说明 |
|---|---|
| `/run <请求>` | 提交一次运行，同步执行后返回 `run_id`、状态和结果 |
| `/get <run_id>` | 查询本次 CLI 会话中的运行记录 |
| `/help` | 查看命令帮助 |
| `/exit`、`/quit` | 退出 CLI |

示例：

```text
codeagent> /run 给出一个 Python 冒泡排序示例
已创建运行：run_9f3a1c7b2d4e
状态：执行成功
结果：固定执行器完成：给出一个 Python 冒泡排序示例
codeagent> /get run_9f3a1c7b2d4e
运行：run_9f3a1c7b2d4e
状态：执行成功
结果：固定执行器完成：给出一个 Python 冒泡排序示例
```

固定执行器约定：请求以 `[fail]` 开头（大小写不敏感）时返回固定失败，其余请求返回固定成功文本。该约定只用于本章验收。

请求不能为空，最多 2000 个字符。非法请求不会创建运行记录。

## 测试

在仓库根目录执行：

```powershell
python -m unittest discover -s tests -v
```

## 本章限制

- 当前通过固定执行器同步完成成功和失败路径。
- CLI 自动生成 `idempotency_key`，本章只校验该字段。取消和恢复的请求模型已定义，行为留在对应章节。
