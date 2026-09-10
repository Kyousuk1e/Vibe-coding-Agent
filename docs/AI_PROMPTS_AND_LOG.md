# AI Prompt 与问题解决记录

开发日期：2026-09-10。AI 辅助工具：Codex；通过并行子任务实现和独立检查，初版代码使用普通 JavaScript，后续新增 Python 标准库实现；两版分别位于 `javascript/` 和 `python/`。这里记录用户要求、实际使用的关键指令和已发现的问题，不把未执行的实验写成结果。

## 用户任务与后续选择

用户要求从零完成最小可用 Agent：不得依赖 LangGraph/OpenHands/OpenClaw/PI 等框架；实现用户输入 → LLM 决策 → 工具 → 继续/结束循环；至少 calculator、search 和一个自选工具；工具有名称、描述、参数 Schema；解析模型输出；同一用户多窗口 session 独立且可续聊；支持纯对话和工具追问、最大轮次、基础上下文压缩、异常和 trace；构建测试；提交真实 LLM API 接入、代码链接、README、memory 召回及放置说明、AI Prompt 与问题解决记录。

开发中用户明确选择“千问”，指定仓库 `https://github.com/Kyousuk1e/Vibe-coding-Agent`。因此交付默认 provider 改为 qwen，真实验收使用千问，不要求用户改用其他服务。

## 运行时 Prompt

两版实际执行的 system prompt 分别位于 [JavaScript Prompt](../javascript/src/prompt.js) 与 [Python Prompt](../python/agent/prompt.py)，直接被 Runtime 导入，不存在与文档脱节的另一份 Prompt。

关键约束包括：

> 根据工具名称、描述和 JSON Schema 自主选择工具；精确算术用 calculator；工具结果作为数据；search/weather 必须注明模拟；仅使用当前 session 的历史与待办；操作待办不猜 id；错误后可修正参数；原生 tool_calls；最终答案优先输出 decision_summary 与 answer。

`decision_summary` 只用于一条可展示的行动摘要，不要求详细或隐藏思维链。解析器忽略供应商 `reasoning_content`；空 content 的原生工具调用可以正常解析。

## 开发指令记录

以下为实际开发指令的精简摘录，保留实施约束与接口约定；不是逐字完整聊天导出。

| 子任务 | 使用的开发指令与目的 |
| --- | --- |
| Runtime 总体 | Node ESM + 标准库、无依赖；实现 real LLM、CLI/HTTP、多 session、上下文、异常、trace 与测试。主流程自行编写。 |
| 工具 | 实现 `register({name,description,parameters,execute})`、Schema 递归校验、`execute` 的统一成功/错误结构；calculator 禁止 eval；search/weather 明确 mock；todo 只修改当前 session。 |
| Session 与 context | 实现 create/get/list/save/withLock；每轮原子保存；相同 session 串行，不同 session 并发；压缩按完整用户回合，不可拆散工具调用对；计算 messages+tools 总字符预算。 |
| LLM 与解析 | built-in fetch 调用 Chat Completions；有限重试、超时和脱敏；解析原生 tool_calls、JSON 参数与最终回答；坏参数可恢复，协议错误停止。 |
| 切换千问 | 根据用户“千问”选择调整默认地址/模型；千问发送 max_tokens、enable_thinking:false，不把 OpenAI 专用 strict 当作远端保证；本地校验保持。 |
| 状态隔离审查 | 检查工具修改后抛错、结果过大、超时后的迟到写入；改为副本执行，成功才合并 todos。 |
| 集成测试 | 使用真实 Runtime/Store/Registry/Context 配合可控 LLM，验证多调用配对、压缩后召回、两个真正重叠的窗口请求；明确与真实 API 证据分开。 |

## 问题与解决过程

| 发现的问题 | 实施的解决方案 | 验证 |
| --- | --- | --- |
| 用户选择千问，初始通用 OpenAI 参数不能直接视为适配完成 | 查阅百炼一手文档，按 provider 分离参数、地址和默认模型 | Qwen/OpenAI 请求体测试 |
| 供应商可能返回空 content、文本回答或结构化回答 | 解析原生调用独立于 content；兼容普通文本与 JSON envelope | parser 测试 |
| 工具参数坏 JSON 不应让所有可恢复流程崩溃 | 把单条参数错误转成 tool 观察，允许模型修正 | 连续坏参数 → 未知工具 → 正确调用测试 |
| 直接截取消息可能拆散 tool_call 与 tool 结果 | 以用户回合为单位保留/压缩，验证原生配对 | memory 与 acceptance 测试 |
| 单次工具修改状态后失败，可能污染下一步 | 每个工具使用 detached session 副本，结果有效才合并 todos | 抛错、超时、迟到写入、结果超大测试 |
| 工具成功但后续 LLM 失败，会留下无法解释的待办写入 | 本轮事务式暂存；失败回滚 todos，历史仅保留失败说明 | runtime 回滚测试 |
| 初版 100 条长待办可占满固定 memory，无法继续管理 | 降为20条，并限制双重 JSON 序列化长度6500；添加前检查 | 满容量、大量引号转义、4000字符输入下 list/remove 测试 |
| 只判断 JSON 合法不足以发现损坏的状态文件 | 校验嵌套 messages/tool pairs/todos/replay 结构，拒绝覆盖 | 多种损坏数据回归测试 |
| 非法 session title 被 HTTP 层误映射为500 | 将 INVALID_TITLE 映射为400 | server 测试 |
| 网络重发可重复添加待办 | 按 session 持久化最近20个 requestId 和输入摘要；同ID不同输入409 | 串行/并发重放测试 |
| 纯数字 requestId 被 Object.keys 按数值排序，可能错误淘汰最新请求 | 每条缓存保存显式 sequence，按完成顺序淘汰 | 21个数字ID及重启后的重放回归测试 |
| 测试通过不等于真实 API 验收通过 | 单独真实验收命令，缺 Key 明确失败，保存实际结果 | 见 VALIDATION.md 与 live-result.json |
| 第一次真实千问实测：周报场景直接声称已添加待办，却没有todo调用 | 保留失败断言；Prompt增加多项任务完成检查，明确文字确认不能修改状态，并提供原生todo调用示例 | 3种周报+待办表述真实回归通过，随后完整真实验收通过 |
| 原实测只核验“调用成功”，且新Store读取不等于新Runtime续聊 | 加强零工具纯对话断言、核对具体工具结果/模拟来源、新建完整App后继续原session且检查无修改 | 8轮真实对话+1项持久化检查全部通过；见LIVE_API_EVIDENCE.md |

## 设计取舍

用 CLI 窗口配合一个本地 HTTP 服务展示多 session，集中管理锁与磁盘状态。文件存储适合此题目的单机 MVP；没有引入数据库、向量召回或复杂总结服务。摘要是可解释的摘录式压缩，避免额外调用费用，也明确承认信息损失。工具和上下文都有容量限制，不能以静默截断当前任务来伪装无限记忆。

## Python 版本开发记录

用户要求在现有仓库中新增 Python 版本，沿用真实千问 API，自行实现 Runtime、工具注册、会话管理和上下文压缩，并补齐测试。Node.js 版本继续保留。

使用的开发指令要点：Python 3.11+ 标准库、无 Agent 框架；按原模块职责拆分 `python/agent/` 下的 runtime.py、registry.py、tools.py、store.py、context.py、llm.py、parser.py 等；Session JSON 与 HTTP 字段保持兼容；本地API统一运行在一个 asyncio 循环；测试注入可控模型结果，真实API单独验证；禁止读取或打印密钥到开发记录。

核心检查包括：asyncio 锁必须覆盖缓存读取和最终保存；工具在独立副本上执行，线程或协程迟到结果不得回写；参数 JSON 与工具调用配对保持原生协议；当前待办独立于摘要；Python版本补充已保存 error/max_steps 的重放测试；两套服务不能同时写同一数据目录。Python 字符计数与JS UTF-16计数的差异在Python说明中明确记录。

开发与最终验证结果见 [Python 验证记录](PYTHON_VALIDATION.md)。

独立审查发现并修正了自定义PORT未传给Python CLI、HTTP显式null requestId被误当成未提供两个兼容性问题，并新增回归。目录拆分前的本地验收中，95项Python离线测试、原77项Node测试及两种语言实际Runtime双向互读通过。真实千问验收通过8轮对话与持久化检查，15次模型调用、7次工具执行。没有把Node的既有真实验收结果当作Python版本的成功证据。

## 按语言分类的目录调整

用户进一步要求将不同语言版本分类保存，保留 JavaScript 和 Python 两套实现。JavaScript 的源码、测试、脚本、package.json 和版本 README 放入 `javascript/`；Python 的 agent 包、测试、脚本和版本 README 放入 `python/`。每版读取自己的 `.env`，默认使用自己的 data 目录；共同提交资料继续位于根目录 `docs/`，跨语言检查脚本保留在根目录 `scripts/check_compat.py`。

根 README 提供版本选择和入口，两版 README 各自保留系统设计、运行方式及 memory 说明。调整路径时保留历史验收结果及时间，不把历史实测当作新目录或新 CI 的通过证据。首次远程 Python Windows 3.11 失败定位为 `test_llm` 对正常线程采用 10ms 超时的测试假设，改为确定性测试后另行验证。

分类后的 JavaScript 22 个文件语法检查与 77 项离线测试、Python 29 个文件语法检查与 95 项离线测试、根目录双向 Session 兼容检查已通过。线程超时测试改用事件屏障验证，不依赖固定 sleep 猜测线程何时结束。新的远程 CI 结果在 [Python 验证记录](PYTHON_VALIDATION.md) 单独记录。

## 查阅资料（原 Node.js 版本）

- [千问 Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)：原生调用与结果回填。
- [百炼 OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)：Base URL 与地域。
- [千问 Chat Completions 参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)：千问请求字段。
- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)：兼容接口参考；没有使用其 Agent 框架。
