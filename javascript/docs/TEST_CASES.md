# 测试用例与要求映射

测试使用 Node.js 内置 `node:test` 和 `assert`。在本项目根目录执行 `npm test`，无需 API Key、网络、数据库或第三方测试框架。已有验证为 77 项通过，实际结果及时间见 [VALIDATION.md](VALIDATION.md)。

## 构建方法

先把题目要求拆成可验证行为，再为每种行为设计正常输入、边界输入和故障场景。Runtime 测试注入固定的 LLM 输出序列，真实执行 Runtime、工具注册、context 与文件存储；模型客户端测试注入 fetch 来控制 HTTP 响应。每个持久化测试使用临时目录，避免污染日常会话。

例：为测试循环上限，让模拟 LLM 持续返回合法工具调用，设置较小 maxSteps，断言调用次数恰好到达上限、返回 `max_steps`、请求终止。为测试 Session 隔离，用 Promise 屏障令两个窗口的模型请求真正重叠，再分别核对上下文和磁盘状态。

不把自然语言答案的逐字一致当作业务成功。工具调用记录、结果字段、待办 ID/数量/状态和重新读取后的文件数据是主要断言；真实模型测试仅对必须出现的事实和来源标识做文本检查。

## 自动化覆盖

| 要求 / 场景 | 输入或故障 | 可验证结果 | 文件 |
| --- | --- | --- | --- |
| 直接回答、纯对话追问 | 记住名字，随后追问 | 后一次模型请求包含原用户输入和回答 | [runtime.test.js](../test/runtime.test.js) |
| 基本循环 | calculator → tool result → final | 多轮模型决策、结果以正确 tool_call_id 回填 | [runtime.test.js](../test/runtime.test.js) |
| 多工具结果 | 一次返回 calculator + todo | 两条结果与调用逐一配对并进入下一次请求 | [acceptance.test.js](../test/acceptance.test.js) |
| 工具追问 | 完成刚才添加的待办 | 使用真实 UUID，原项 done 变为 true | [acceptance.test.js](../test/acceptance.test.js) |
| 工具 Schema | 注册 4 个工具 | 发布名称、描述、Schema；返回定义与内部状态分离 | [tools.test.js](../test/tools.test.js)、[llm.test.js](../test/llm.test.js) |
| 参数校验 | 错类型、缺字段、额外字段、嵌套数组错误 | 执行前拒绝、无状态变更 | [tools.test.js](../test/tools.test.js) |
| 算术安全 | 优先级、幂、科学计数、注入、除零、溢出 | 合法表达式正确；非法及过深表达式拒绝 | [tools.test.js](../test/tools.test.js) |
| 模拟数据 | search/weather | 来源明确、结果有界、城市和相关资料正确 | [tools.test.js](../test/tools.test.js) |
| 输出解析 | 文本、JSON envelope、代码块、原生调用 | 提取 answer / decisionSummary / calls | [parser.test.js](../test/parser.test.js) |
| 隐藏推理 | 响应带 reasoning_content | 隐藏思维链不进入解析结果与规范化消息 | [parser.test.js](../test/parser.test.js) |
| 可恢复工具错误 | 坏参数 JSON、未知工具 | 结构化观察回填，模型可修正后继续 | [runtime.test.js](../test/runtime.test.js) |
| 整体协议错误 | 重复 ID、缺字段、截断、空响应 | 区分错误并停止，不伪造工具成功 | [parser.test.js](../test/parser.test.js) |
| Session 隔离 | 用户 A 的天气窗口与周报窗口 | 历史、摘要、todos 独立 | [memory.test.js](../test/memory.test.js)、[runtime.test.js](../test/runtime.test.js) |
| 不同窗口并发 | 强制两个 LLM 请求重叠 | 每个请求只看到自己的历史并独立保存 | [acceptance.test.js](../test/acceptance.test.js) |
| 相同窗口并发 | 20 个读改写及失败后的下一请求 | 串行执行、无丢失更新、锁释放 | [memory.test.js](../test/memory.test.js) |
| 持久化恢复 | 创建新的 Store/Runtime 读取原文件 | 历史和待办状态一致并支持续聊 | [memory.test.js](../test/memory.test.js)、[runtime.test.js](../test/runtime.test.js) |
| 访问归属 | B 读取 A 的会话 | 返回 404，不混入列表 | [server.test.js](../test/server.test.js)、[memory.test.js](../test/memory.test.js) |
| 循环上限 | 模型持续返回工具调用 | 在配置上限停止，工具配对完整，返回部分完成说明 | [runtime.test.js](../test/runtime.test.js) |
| Context 压缩 | 多轮长对话 | 摘要有界、按完整回合压缩、无孤立 tool 消息 | [memory.test.js](../test/memory.test.js) |
| 压缩后追问 | 早期代号、多轮消息、结构化待办 | 摘要保留受测线索，当前 todos 完整召回 | [acceptance.test.js](../test/acceptance.test.js) |
| Context 硬预算 | 超大 Schema 或当前回合 | 必需内容过大时明确失败，不静默丢用户输入 | [memory.test.js](../test/memory.test.js)、[runtime.test.js](../test/runtime.test.js) |
| 待办容量 | 20 条长标题、大量引号转义 | list/remove 仍可用，过大新增不改状态 | [tools.test.js](../test/tools.test.js) |
| HTTP 临时故障 | 429、5xx、网络失败、超时 | 有限重试、退避、超时中止 | [llm.test.js](../test/llm.test.js) |
| 不可重试错误 | 400、401、403、404 | 不重试，不泄露密钥或响应体 | [llm.test.js](../test/llm.test.js) |
| HTTP 响应保护 | 错 JSON、过大响应、危险 URL | 协议错误或配置拒绝，无原始敏感内容泄露 | [llm.test.js](../test/llm.test.js) |
| 状态损坏 | 坏 JSON、嵌套消息/todo/cache 损坏 | 明确报错，原损坏文件不覆盖 | [memory.test.js](../test/memory.test.js) |
| 工具隔离 | 先修改后抛错、过大结果、超时后写入 | 失败或迟到状态不提交，成功只复制 todos | [tools.test.js](../test/tools.test.js) |
| 本轮回滚 | todo 成功后 LLM 最终失败 | todos 回滚，历史不保留虚假成功结果 | [runtime.test.js](../test/runtime.test.js) |
| 请求幂等 | 相同 ID/输入顺序和并发重发 | 不新增模型调用、不重复创建待办 | [runtime.test.js](../test/runtime.test.js) |
| 幂等键边界 | 同键异输入、保留字、数字 ID 淘汰 | 冲突或拒绝；按完成顺序保留 20 条 | [runtime.test.js](../test/runtime.test.js) |
| HTTP 入口 | 会话创建、消息、查询、非法参数 | 实际启动本地 HTTP 服务，核对状态码与数据 | [server.test.js](../test/server.test.js) |
| trace | 工具执行和终止 | 事件、名称、参数、结果、耗时和调用 ID 可检查 | [runtime.test.js](../test/runtime.test.js)、[server.test.js](../test/server.test.js) |

## 真实千问验收

显式执行 `npm run test:live`，实现见 [live-smoke.js](../scripts/live-smoke.js)。脚本会产生真实 API 用量；缺少密钥直接失败，不跳过、不替换成 fake LLM。原始报告写在本项目 `docs/live-result.json`。

| 阶段 | 主要断言 |
| --- | --- |
| 纯对话记住“蓝鲸七号” | 无工具调用 |
| 纯对话追问代号 | 答案包含正确代号，无工具调用 |
| 计算 `(123+456)*7` | calculator 的真实工具结果和最终答案为 4053 |
| 搜索上下文压缩 | 非空结果来自 local-demo-corpus，回答披露模拟来源 |
| 上海天气与带伞待办 | weather、todo 均执行成功，城市/来源/精确待办文字正确 |
| 另一个窗口周报与待办 | todo 成功，只包含自己的“周五提交周报” |
| 完成刚才带伞的待办 | 原记录 done 为 true |
| 新 Store 读取文件 | 两个窗口各有一条待办、状态互不干扰 |
| 新 Runtime 续聊 | 记得项目代号与完成状态，查询前后待办不变 |

这里有 8 轮自然语言对话，另有 1 项直接持久化检查。模型改写指定待办文本或没有调用必要工具都会失败。首次真实验收确实捕获了“说已添加、实际没调用 todo”的问题，修复过程见 [LIVE_API_EVIDENCE.md](LIVE_API_EVIDENCE.md)。

## 测试边界与手工演示

可控响应验证已覆盖的程序分支，真实 API 验证受测输入的模型行为。二者都不能证明任何输入下永远正确。压缩测试断言受测线索与结构化状态保留，不意味着所有旧细节无损；恢复测试创建新实例读磁盘，没有假称操作系统进程重启。

手工补充演示：

1. `我的项目叫星河，请记住` → `项目叫什么？`
2. `计算 12*13` → `把刚才结果乘以2`。
3. `查上海天气并记待办带伞` → `查看所有待办` → `把带伞那项标记完成`。
4. 切换周报窗口执行 `/todos`，确认没有天气窗口的待办。
5. 停止并重新启动服务，用原 userId 和 sessionId 续聊，核对状态恢复。

服务进程完整重启、跨进程写入、高并发压测、真实下游订单幂等和生产认证不属于当前自动化结果。
