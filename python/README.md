# Python 版本

Python 版本位于仓库的 `python/` 独立目录，源码在 `python/agent/`；原有 JavaScript / Node.js 版本位于 `javascript/`。目标是保留业务流程、四个工具、HTTP 协议及会话持久化格式，提供一套适合阅读、演示和面试讨论的原生 Python 实现。

## 运行

需要 Python 3.11 或以上，无第三方运行依赖，无需 pip install。除明确说明的跨语言检查外，以下命令均在仓库的 `python/` 目录运行。每个新终端先从仓库根目录执行 `cd python`；Windows 可将命令中的 `python` 替换为 `py`。

先进入 Python 目录并复制配置模板：

```powershell
cd python
Copy-Item .env.example .env
```

编辑本目录的 `.env`，配置真实 `DASHSCOPE_API_KEY`，模型默认 `qwen-plus`。已有可用的千问配置可以复用；本版本默认读取 `python/.env`，数据保存在 `python/data/`。

```powershell
python -m agent serve
```

另开两个终端，各自先进入 `python/` 目录，再分别运行：

```powershell
python -m agent chat --user A --title 天气和待办
python -m agent chat --user A --title 周报和待办
```

每个窗口默认建立自己的 Session，显示续聊命令。使用相同用户和 Session ID 恢复：

```powershell
python -m agent chat --user A --session <sessionId>
```

支持 `/sessions`、`/use ID`、`/new 标题`、`/todos`、`/trace`、`/retry`、`/help`、`/exit`。`/retry` 重发网络失败的原请求，复用 requestId；普通消息创建新的请求。聊天客户端不需要读取 API Key，只有服务端使用密钥。

默认服务地址为 `http://127.0.0.1:8787`。本地演示 API 不提供真实身份认证；X-User-Id 只演示会话归属。请求体限 32KB，仅支持带 Content-Length 的 JSON 请求，响应后关闭连接，不支持流式输出。

## 模块与主循环

| Node.js 文件 | Python 文件 | 职责 |
| --- | --- | --- |
| [javascript/src/runtime.js](../javascript/src/runtime.js) | [python/agent/runtime.py](agent/runtime.py) | 自写带 await 的 for 循环、工具回填、轮数上限、回滚和幂等 |
| [javascript/src/llm.js](../javascript/src/llm.js) | [python/agent/llm.py](agent/llm.py) | urllib + asyncio 调用真实 API、有限重试、超时和脱敏 |
| [javascript/src/parser.js](../javascript/src/parser.js) | [python/agent/parser.py](agent/parser.py) | 原生 tool_calls、JSON 参数、简短决策摘要、最终答案 |
| [javascript/src/registry.js](../javascript/src/registry.js) | [python/agent/registry.py](agent/registry.py) | 工具注册、参数 Schema 校验、执行隔离和超时 |
| [javascript/src/tools.js](../javascript/src/tools.js) | [python/agent/tools.py](agent/tools.py) | calculator/search/todo/weather |
| [javascript/src/store.js](../javascript/src/store.js) | [python/agent/store.py](agent/store.py) | JSON 状态、原子保存、同 Session 排队和损坏检测 |
| [javascript/src/context.js](../javascript/src/context.js) | [python/agent/context.py](agent/context.py) | 每次模型调用前召回记忆，按完整回合压缩 |
| [javascript/src/trace.js](../javascript/src/trace.js) | [python/agent/trace.py](agent/trace.py) | 按 Session 记录 JSONL trace |
| [javascript/src/app.js](../javascript/src/app.js) | [python/agent/app.py](agent/app.py) | 依赖注入及生产模块组装 |
| [javascript/src/server.js](../javascript/src/server.js)、[javascript/src/cli.js](../javascript/src/cli.js) | [python/agent/server.py](agent/server.py)、[python/agent/cli.py](agent/cli.py) | 单 asyncio 服务循环及多终端入口 |

`AgentRuntime.run(user_id=..., session_id=..., input=..., request_id=...)` 先取得会话锁，再读文件与请求缓存。每次循环构建上下文并调用模型；解析到工具调用后，注册表验证参数并执行函数，原 assistant 工具调用及带 `tool_call_id` 的结果一起写入当前上下文，再调用模型。最终回答或达到上限后保存；本轮模型或上下文错误会回滚本轮 todos。

`max_steps=8` 限制逻辑模型调用轮数，每轮最多四个工具；单次 HTTP 请求的有限重试在该轮内部完成。达到上限保留已成功操作；保存下来的 error 和 max_steps 结果也可以被相同 requestId 重放。

## 工具与状态

工具名称、描述、参数 Schema 来自注册表，LLM 自主选择工具。calculator 不使用 eval；search/weather 明确返回 mock 标识；todo 仅操作当前会话，支持 add/list/complete/remove，unused 参数为 null。待办最多20项、标题200字及整体序列化容量限制沿用原设计。

同步工具在工作线程中执行，异步工具使用协程。每次工具执行使用独立的会话副本；只有成功且输出合法时才合并 todos。超时后的返回不再提交状态。Python 不能强制终止已经运行的线程或不合作的代码，工具应遵守超时和取消协议；有限的内置工具没有不受控执行入口。

Session 文件字段保留 `userId`、`createdAt`、`updatedAt`、`messages`、`summary`、`todos`、`completedRequests`。哈希文件名规则与 Node.js 相同。单进程内相同 Session 串行、不同 Session 并发；不同进程或 Node/Python 两个服务同时写同一目录不受该内存锁保护。

本版本保留最近20个 requestId 的结果，重复请求在同一 Session 中返回原结果；同键不同输入报409。这个机制属于用户请求级幂等，未增加架构讨论中的外部订单 operationId、数据库事务或工具幂等元数据。

## Memory 召回时机与放置位置

每次 LLM 调用前依次发送：系统提示与信任规则、包含摘要及当前完整 todos 的 memory 数据消息、保留的完整历史回合、当前回合全部消息。工具 Schema 在 tools 独立字段中，但计入预算。当前 todos 是权威业务状态，历史摘要或旧工具结果不覆盖它；不存储隐藏思维链。

默认预算为序列化 messages+tools 的24000字符，优先保留最近4个完整回合，摘录摘要最多4000字符。这是程序实现的确定性有损压缩，不另调用 LLM。完整工具配对一起保留或压缩；当前回合、系统规则、Schema 和 todos 不能静默截断，放不下时返回 CONTEXT_LIMIT。

Python 使用 Unicode 字符计数，Node.js 字符串长度使用 UTF-16 单元计数，包含 emoji 等补充平面字符时压缩触发点可能略有差异。两者都是字符预算，不是精确 token 计数。

## 测试

以下测试命令均在 `python/` 目录执行，测试源码见 [tests/](tests/)。

```powershell
python scripts/check.py
python -m unittest discover -s tests -v
python scripts/live_smoke.py
```

跨语言检查需回到**仓库根目录**运行 `python scripts/check_compat.py`（如果当前在 `python/`，先执行 `cd ..`）。它同时需要 Node.js 和 Python，用两个实际 Runtime 验证会话文件及幂等结果的双向互读，不调用模型 API。

离线 unittest 使用真实 Runtime、注册表、工具、文件 Store、Context 和 HTTP 服务，仅控制模型返回或网络传输。测试涵盖参数/解析错误修复、多工具配对、真实并发重叠、幂等重放、缓存淘汰、失败结果重放、上限结果重放、回滚、压缩后追问、损坏文件保护及网络错误。

真实 API 脚本单独运行，需要有效密钥并产生实际用量。它验证纯对话记忆、计算、搜索、天气+待办、独立周报会话、待办追问、新建完整 Runtime 后从原文件续聊。断言结合工具 trace 和精确落盘状态，不仅检查自然语言“已完成”。原始结果保存在**仓库根目录**被 Git 忽略的 `docs/python-live-result.json`。持久化恢复检查是在同一 Python 进程里重建组件，未冒充完整服务进程重启测试。

GitHub Actions 配置 Windows/Linux × Python 3.11/3.14 的语法和离线测试，保留原 Node.js 检查，并在 Windows/Linux 各增加一个跨语言 Session 兼容任务。配置不代表每项已执行通过；实际状态见 [Python 验证记录](../docs/PYTHON_VALIDATION.md)。真实 API 验收不放入 CI，避免在 PR 中引入密钥和调用费用。

共同提交资料见仓库根目录的 [docs/](../docs/)，返回 [仓库首页](../README.md)。
