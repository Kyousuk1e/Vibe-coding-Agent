# 测试用例与要求映射

以下离线测试通过注入可控 LLM 响应来验证 Runtime；它们不证明真实模型一定做出同样决策。真实 LLM 决策由 `npm run test:live` 另行验证。

| 要求 / 场景 | 输入或故障 | 可验证结果 | 自动化文件 |
| --- | --- | --- | --- |
| 直接回答 | 记住名字、再次问名字 | 第二次请求包含原用户输入和回答，不强制调用工具 | runtime.test.js |
| 基本循环 | calculator → tool result → final | 至少两次 LLM 调用，结果通过正确 tool_call_id 回填 | runtime.test.js |
| 多工具 | 一次返回 calculator + todo | 两个结果逐一配对，下一次 LLM 可同时看到 | acceptance.test.js |
| 工具追问 | 完成刚才添加的待办 | 使用工具返回的真实 UUID，正确修改状态 | acceptance.test.js |
| Schema 决策输入 | 注册 4 个工具 | 发布名称、描述、严格参数 Schema | tools.test.js、llm.test.js |
| 无效参数 | 错误类型、缺字段、额外字段、数组嵌套错误 | 执行前拒绝且没有副作用 | tools.test.js |
| 输出解析 | 普通文本、JSON 最终回答、原生 tool_calls | 正确提取 answer / decisionSummary / calls | parser.test.js |
| 输出错误 | 坏 JSON 参数、未知工具 | 返回结构化观察，LLM 能修正后继续 | runtime.test.js |
| 协议异常 | 重复调用 ID、缺失字段、截断、空响应 | 可区分错误并停止本轮，不制造伪结果 | parser.test.js |
| Session 隔离 | 同一 A 的窗口1天气待办，窗口2周报待办 | messages、summary、todos 互不混入 | memory.test.js、runtime.test.js |
| 并发窗口 | 强制两个 LLM 请求重叠 | 每个请求只见自己的历史，分别提交 | acceptance.test.js |
| 同一窗口并发 | 并发读改写与失败后的下一请求 | 串行执行，无丢更新，锁被释放 | memory.test.js |
| 持续状态 | 换新 Store 实例续聊 | 数据从磁盘恢复，待办状态一致 | memory.test.js、runtime.test.js |
| 访问归属 | B 读取 A 的 session | 404，无跨用户列表 | server.test.js、memory.test.js |
| 最大轮次 | 模型持续调工具 | 精确在配置上限停止并给出部分完成说明 | runtime.test.js |
| Context 压缩 | 多轮长对话 | 摘要有界，完整回合压缩，没有孤立 tool 消息 | memory.test.js |
| 压缩后追问 | 早期代号 + 多轮长消息 + 待办 | 早期线索进入摘要，结构化 todos 完整召回 | acceptance.test.js |
| 硬预算 | 超大 Schema / 当前回合 | 超限前停止，不静默丢当前用户请求 | memory.test.js、runtime.test.js |
| 容量边界 | 最大待办数量、长标题、大量转义字符 | 满容量仍能 list/remove，超限添加不修改状态 | tools.test.js |
| 异常处理 | 429 / 5xx / 网络故障 / 超时 | 有限重试、超时中止 | llm.test.js |
| 不可重试错误 | 400 / 401 / 403 / 404 | 不重试，不泄露密钥或响应体 | llm.test.js |
| 数据损坏 | 无效 JSON 或嵌套记录损坏 | 明确报错，保存时不覆盖损坏文件 | memory.test.js |
| 工具隔离 | 自定义工具修改后抛错或超时后继续修改 | 不提交失败或迟到的修改 | tools.test.js |
| 用户回合回滚 | todo 成功后 LLM 失败 | todos 回滚，不留虚假成功历史 | runtime.test.js |
| 幂等 | 相同请求串行/并发重发 | 无额外 LLM 调用、无重复待办 | runtime.test.js |
| trace | 普通工具回合 | 名称、参数、结果、耗时、调用 ID、终止状态可检查 | runtime.test.js、server.test.js |
| 真实千问协议 | 注入 fetch 检查请求体 | 正确地址/参数、tools auto、千问非思考模式 | llm.test.js |

真实 API 验收脚本有八个自然语言交互阶段：纯对话记忆写入、纯对话追问、计算、搜索、天气与待办、另一窗口周报与待办、工具追问、新Runtime续聊；另行读取持久化状态验证恢复和隔离。断言检查纯对话零工具调用、具体计算数值、搜索/天气模拟来源、实际工具trace、精确待办状态以及续聊无意外修改。模型改写指定待办文本或不选择要求的工具会使验收失败，方便发现Prompt/模型差异，不会宽松地把任意答案当成功。首次真实验收确实抓到一次未执行todo却声称添加的失败，记录见LIVE_API_EVIDENCE.md。

手工演示可增加以下追问：

1. `我的项目叫星河，请记住` → `项目叫什么？`
2. `计算 12*13` → `把刚才结果乘以2`
3. `查上海天气并记待办带伞` → `查看所有待办` → `把带伞那项标记完成`
4. 切换周报窗口并运行 `/todos`，确认天气窗口待办没有出现。
5. 停止服务、重新启动，并用先前 sessionId 续聊。
