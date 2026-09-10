# 测试用例与要求映射

测试使用 Python 标准库 `unittest`。命令均在本项目根目录执行，Windows 可将 `python` 换成 `py`：

```powershell
python scripts/check.py
python -m unittest discover -s tests -v
```

目前定义 95 项离线测试。每项中的 subTest 参数场景不额外充当独立测试数量。离线测试控制模型返回或 HTTP 传输，但执行真实 Runtime、工具、Context、文件 Store 和本地服务，不要求真实 API Key。

## 用例分布

| 文件 | 数量 | 主要内容 |
| --- | --- | --- |
| [test_runtime.py](../tests/test_runtime.py) | 15 | 循环、追问、多工具、幂等、回滚、压缩集成 |
| [test_memory.py](../tests/test_memory.py) | 16 | Session、持久化、锁、损坏保护、上下文 |
| [test_tools.py](../tests/test_tools.py) | 20 | Schema、计算器、待办与工具隔离 |
| [test_parser.py](../tests/test_parser.py) | 13 | 原生工具调用、参数和最终答案解析 |
| [test_llm.py](../tests/test_llm.py) | 19 | 配置、模型请求协议、超时与重试 |
| [test_server.py](../tests/test_server.py) | 8 | HTTP 路由、错误和归属限制 |
| [test_cli.py](../tests/test_cli.py) | 3 | 请求重试与客户端连接配置 |
| [test_entrypoints.py](../tests/test_entrypoints.py) | 1 | 实际服务与 CLI 子进程入口 |

## 核心需求与断言

| 场景 | 构造方法 | 检查结果 |
| --- | --- | --- |
| 直接回复与纯对话追问 | 返回两次固定 final | 第二次发送的上下文包含之前用户和助手消息，无工具调用 |
| 工具循环 | 返回 tool call 后再 final | 工具确实执行，下一次模型输入含对应结果 |
| 多工具配对 | 同一轮天气与计算器调用 | 保留原 assistant tool_calls，结果的 tool_call_id 精确匹配 |
| 参数错误自修正 | 坏 JSON → 未知工具 → 错误类型 → 正确参数 | 前三次返回结构化错误，最后正常完成 |
| 工具追问 | 添加待办后读取真实 ID，再请求完成 | 上下文含 ID；原记录 done=true，数量不变 |
| 轮次上限 | max_steps=1 且模型只返回工具 | 恰好一次模型调用，status=max_steps，成功待办仍保存 |
| 同一请求重复提交 | 并发发送同一 ID、同一输入 | 只执行首个请求，第二个 replayed=true，待办不重复 |
| 同键异参 | 首次完成后换输入复用 ID | REQUEST_CONFLICT，不进入模型 |
| 新请求及缓存淘汰 | 使用新 ID；按顺序创建 21 个请求 | 新 ID 可执行；只保留最近 20 个，数字 ID 不影响淘汰顺序 |
| 失败/上限重放 | 首轮保存 error 或 max_steps 后重试 | 直接返回原状态，无新增模型调用 |
| 本轮回滚 | todo 成功后模拟 LLM_TIMEOUT | todos 恢复原值，本轮历史不保留虚假的工具成功记录 |
| 双 Session 并发 | 用 Event 屏障强制两次模型调用重叠 | 各自历史和待办独立，不是串行运行假装并发 |
| 同 Session 并发 | 20 次在锁内读改写 | 最终保留 20 条新增记录，锁表被清理 |
| 锁失败与取消 | 锁内抛错、取消正在等待的请求 | 后续请求可继续，等待者不泄漏锁 |
| 文件恢复 | 新建 SessionStore 加载相同数据目录 | 对话、摘要、待办和请求结果一致 |
| 状态损坏 | 写入坏 JSON、无效嵌套记录或悬空工具结果 | 明确 CORRUPT_SESSION，保存不会覆盖损坏文件 |
| 稳定触发压缩 | 小预算与固定长度多轮消息 | compactedTurns>0，摘要与整包大小有界 |
| 压缩后完成待办 | 早期项目代号和待办，加多轮长消息 | memory 仍含代号及完整 todo ID，完成后磁盘状态正确 |
| 不可截断部分超限 | 过大当前输入、Schema 或 todos | CONTEXT_LIMIT，原历史与当前输入不被修改 |
| trace 故障 | trace writer 抛出异常 | 会话仍已保存，响应含 traceWarning |

## 工具与模型边界

- 工具测试验证递归 Schema 校验、nullable、布尔与数字区分、枚举、额外字段、重复注册和未支持的关键字。
- 计算器测试覆盖优先级、幂、小数、科学计数、除零、溢出、恶意表达式与语法错误，不使用 eval。
- 待办测试覆盖四种 action、真实 ID、当前会话归属、无效参数不写入、20 项容量、Unicode 长度与大量转义字符。
- 工具异常测试覆盖抛错、输出过大、线程或协程超时后的继续执行；迟到修改始终隔离于原会话。
- 解析器测试覆盖普通文本、JSON envelope、代码块、空 content 工具调用、重复调用 ID、坏参数、截断和拒答，不读取隐藏推理。
- LLM 测试检查千问请求 URL 与字段、tools/auto 配置、认证与非重试错误、429/5xx、Retry-After 边界、网络故障、响应大小和错误脱敏。
- 线程超时测试使用事件屏障控制顺序，避免用极小固定时间猜测线程什么时候运行。

## 真实 API 验收

```powershell
python scripts/live_smoke.py
```

该脚本使用真实千问，会产生 API 用量；没有 Key 就失败，不会跳过。8 轮对话覆盖纯对话记忆、追问、计算、搜索、天气和待办、独立周报会话、完成待办以及新 Runtime 续聊，另检查磁盘恢复和会话隔离。

判断任务成功时，分别检查自然语言输出、工具 trace 和持久化业务状态。添加待办需要实际调用成功、数量精确增加、文字一致；完成操作需要原 ID 不变、done=true。对真实模型措辞不做整句固定匹配。

新 Store 或新 Runtime 的恢复检查在同一 Python 进程中完成。子进程测试实际启动服务和 CLI，但没有覆盖服务崩溃、机器断电或多实例写同一目录。

历史结果见 [验证记录](VALIDATION.md)和[真实千问证据](LIVE_API_EVIDENCE.md)。这些记录证明已列出的输入和故障场景，不能保证模型对任意输入始终正确。
