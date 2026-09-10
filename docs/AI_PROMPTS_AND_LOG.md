# AI Prompt 与问题解决记录

开发日期：2026-09-10。AI 辅助工具：Codex；通过并行子任务实现和独立检查，代码均为本仓库中的普通 JavaScript。这里记录用户要求、实际使用的关键指令和已发现的问题，不把未执行的实验写成结果。

## 用户任务与后续选择

用户要求从零完成最小可用 Agent：不得依赖 LangGraph/OpenHands/OpenClaw/PI 等框架；实现用户输入 → LLM 决策 → 工具 → 继续/结束循环；至少 calculator、search 和一个自选工具；工具有名称、描述、参数 Schema；解析模型输出；同一用户多窗口 session 独立且可续聊；支持纯对话和工具追问、最大轮次、基础上下文压缩、异常和 trace；构建测试；提交真实 LLM API 接入、代码链接、README、memory 召回及放置说明、AI Prompt 与问题解决记录。

开发中用户明确选择“千问”，指定仓库 `https://github.com/Kyousuk1e/Vibe-coding-Agent`。因此交付默认 provider 改为 qwen，真实验收使用千问，不要求用户改用其他服务。

## 运行时 Prompt

完整、实际执行的 system prompt 位于 [`src/prompt.js`](../src/prompt.js)，直接被 Runtime 导入，不存在与文档脱节的另一份 Prompt。

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

## 设计取舍

用 CLI 窗口配合一个本地 HTTP 服务展示多 session，集中管理锁与磁盘状态。文件存储适合此题目的单机 MVP；没有引入数据库、向量召回或复杂总结服务。摘要是可解释的摘录式压缩，避免额外调用费用，也明确承认信息损失。工具和上下文都有容量限制，不能以静默截断当前任务来伪装无限记忆。

## 查阅资料

- [千问 Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)：原生调用与结果回填。
- [百炼 OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)：Base URL 与地域。
- [千问 Chat Completions 参数](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)：千问请求字段。
- [OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)：兼容接口参考；没有使用其 Agent 框架。
