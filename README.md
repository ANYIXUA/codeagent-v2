# codeagent-v2

第一章实现一个在终端中运行的 RunAPI：用户在同一个交互式 CLI 会话里提交请求，并用返回的 `run_id` 查询结果。

本章运行记录只保存在 CLI 进程内存中，退出 CLI 后失效；执行由固定执行器同步完成，不连接数据库、队列或真实模型。

## 环境

- Python 3.12
- Windows + PowerShell
- 无第三方运行依赖，测试使用 Python 标准库 `unittest`
- 本次验收使用 conda 环境 `dev`（Python 3.13.15）；同一套测试也在 CPython 3.12.14 下通过

## 启动

在仓库根目录执行：

```powershell
$env:PYTHONUTF8 = '1'
python -m codeagent_v2
```

入口文件为 `codeagent_v2/__main__.py`。不要使用 `python -m codeagent`（包名错误）或 `python codeagent_v2/__main__.py`（会绕过包上下文）。

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

- 运行记录只在当前 CLI 进程内存中，退出即丢失。
- 固定执行器同步执行，不调用真实模型或工具。
- 不包含排队、Worker、幂等、取消、恢复、事件流和持久化；这些留给后续章节。
