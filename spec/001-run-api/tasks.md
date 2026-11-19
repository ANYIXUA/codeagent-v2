# 001 RunAPI 实现任务

验收编号见 `spec.md`，组件与数据流见 `plan.md`。

| 任务 | 改动边界 | 对应验收 | 完成证据 |
|---|---|---|---|
| T01 Run schema | `codeagent_v2/schemas.py` | #5 | 六种状态可序列化，字段边界、可空字段和默认值通过验证 |
| T02 运行记录 | `codeagent_v2/model.py`、`run_api.py` | #1–#4 | 记录保留完整输入和 UTC 时间戳，复用固定执行器及内存存储 |
| T03 类型化接口 | `codeagent_v2/run_api.py` | #1–#5 | 创建和查询返回一致的 `RunResponse` |
| T04 CLI 接入 | `codeagent_v2/cli.py` | #1–#4 | CLI 直接调用 RunAPI 并显示文本，退出后旧记录失效 |
| T05 测试和启动文档 | `tests/`、`README.md`、`requirements.txt` | #1–#5 | 通过自动化测试和真实终端会话验收 |

## 验收命令

```powershell
python -m pip install -r requirements.txt
$env:PYTHONUTF8 = '1'
python -m unittest discover -s tests -v
```

## 验收输入与结果

| 规格编号 | 输入 | 预期结果 |
|---|---|---|
| #1 | `/run 解释任务队列`，再查询返回的 ID | 提交和查询都显示「执行成功」和一致的结果 |
| #2 | `/run [fail] 验收失败`，再查询返回的 ID | 提交仍返回 ID，查询显示「执行失败」和一致的原因 |
| #3 | `/run`，省略请求正文 | 提示「提交失败」和字段原因，运行记录数不变 |
| #4 | `/get run_missing` | 提示「找不到这次运行」，随后继续接收命令 |
| #5 | 输入超出 schema 字段边界，或省略可选字段 | 校验失败或按模型填充默认值 |

## 本次验证记录

- Python 3.12.14 干净环境只安装 Pydantic 及其依赖，36 项测试通过。Python 3.13.15 的 `dev` 环境也通过了同一套测试。
- 在同一个 CLI 进程先后提交和查询成功、失败两条运行，得到一致的文本结果。空请求和未知 ID 均显示预期提示，`/exit` 正常结束会话。
