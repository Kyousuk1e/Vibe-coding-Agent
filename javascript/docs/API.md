# 本地 HTTP API

在本项目根目录运行 `npm start`。默认地址为 `http://127.0.0.1:8787`，可通过 `PORT` 修改；实现见 [server.js](../src/server.js)。HTTP 服务使用 Node.js 标准库，CLI 通过这些接口访问同一个服务进程。

## 通用约定

- 除 `/health` 外，需要 `X-User-Id`：1–64 位 ASCII 字母、数字、下划线或连字符。它用于演示数据归属，不是登录认证。
- POST 使用 `Content-Type: application/json`，请求体必须是 JSON 对象，最多 32768 字节。
- 带 `Origin` 头的请求会返回 403 `ORIGIN_DENIED`，包括健康检查。接口面向本地 CLI，不提供跨域浏览器调用支持。
- 响应是 UTF-8 JSON，带 `Cache-Control: no-store`。没有 SSE 或流式回答。

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| `GET /health` | 无 | 200，`{"ok":true}` |
| `GET /tools` | 用户头 | 200，`{"tools":[...]}`，内容为工具定义和参数 Schema |
| `POST /sessions` | `{"title":"天气和待办"}`，title 可省略 | 201，新会话对象，包含 `id` |
| `GET /sessions` | 用户头 | 200，`{"sessions":[{"id","title","createdAt","updatedAt"},...]}` |
| `GET /sessions/:id` | 用户头 | 200，当前用户的完整会话，移除内部 `completedRequests` 缓存 |
| `POST /sessions/:id/messages` | `{"input":"你好","requestId":"req-001"}` | 200，本轮结果、终止状态和 trace |

`input` 必须是非空字符串，最多 4000 个 JavaScript 字符单位；`requestId` 可省略，由 Runtime 生成 UUID。手动提供时须为 1–100 位字母、数字、下划线或连字符，保留的原型属性名称也会被拒绝。需要网络重试时，客户端应在第一次发送前生成 ID 并保存它。

## 请求示例

先在一个终端执行 `npm start`，再在 PowerShell 中运行：

```powershell
$agentHeaders = @{ 'X-User-Id' = 'A' }
$agentSession = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8787/sessions' `
  -Headers $agentHeaders -ContentType 'application/json; charset=utf-8' `
  -Body '{"title":"计算演示"}'
$agentMessage = @{ input = '请用计算器算 (12+8)*3'; requestId = 'demo-001' } | ConvertTo-Json
$agentResult = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8787/sessions/$($agentSession.id)/messages" `
  -Headers $agentHeaders -ContentType 'application/json; charset=utf-8' -Body $agentMessage
$agentResult.answer
$agentResult.trace | ConvertTo-Json -Depth 10
```

响应结构示意，内容与数值用于说明格式，不是一次实际调用的证据：

```json
{
  "requestId": "demo-001",
  "sessionId": "服务生成的UUID",
  "status": "ok",
  "answer": "结果是60。",
  "decisionSummary": "根据计算器结果回答",
  "steps": 2,
  "trace": [
    {"event":"tool.start","tool":"calculator","callId":"call_01"},
    {"event":"tool.end","tool":"calculator","ok":true,"durationMs":1}
  ],
  "replayed": false
}
```

`steps` 统计本轮逻辑 LLM 调用数，不是工具执行次数，也不包含单次调用内部的 HTTP 重试。trace 中的原生调用 ID 用于诊断；工具观察在模型上下文中通过 `tool_call_id` 与对应 assistant 调用配对。

## 状态与错误

| 业务 `status` | 含义 | 本地待办状态 |
| --- | --- | --- |
| `ok` | 模型返回本轮最终回答，也可以是澄清问题 | 保存本轮成功工具修改 |
| `max_steps` | 达到逻辑模型调用上限 | 保存已成功操作，回答说明部分完成 |
| `error` | 模型、解析、上下文等使本轮最终失败 | 回滚本轮 todos 修改，保存用户输入和失败说明 |

已经形成并保存的用户回合返回 HTTP 200，客户端仍必须检查业务 `status`。HTTP 200 和模型说“已添加”都不足以单独证明业务操作成功，应结合工具结果和实际待办状态。

| HTTP 状态 | 常见错误码与情况 |
| --- | --- |
| 400 | `BAD_INPUT`、`INVALID_ID`、`INVALID_TITLE`：输入、身份头、JSON 或标识格式错误 |
| 403 | `ORIGIN_DENIED`：请求带浏览器 Origin |
| 404 | `SESSION_NOT_FOUND`、`NOT_FOUND`：会话不存在、不属于当前用户，或接口不存在 |
| 409 | `REQUEST_CONFLICT`：同一 Session 的 requestId 被用于不同输入 |
| 500 | 持久化故障、损坏状态等未能形成正常回合响应的服务端错误；消息会脱敏 |

HTTP 错误格式为 `{"error":{"code":"...","message":"..."}}`。工具层的 `{ok:false,error:{...}}` 通常是一次可恢复的模型观察，不一定立刻导致 HTTP 错误或结束回合。

## 幂等与并发

当前 Session 最近 20 个请求结果持久化保存。网络结果未知时复用原 `requestId` 和输入；命中结果返回 `replayed: true`，不再调用 LLM 或执行工具。同 ID 异输入返回 409。已经保存的失败或部分完成结果也会被重放；确认要重新执行业务时发起新请求。

同一 Session 的锁覆盖读取、缓存检查、执行与保存；不同 Session 可并发。锁仅适用于同一 Store 实例所在的单服务进程，不是数据库锁或分布式锁。缓存被淘汰、同一轮内重复工具调用、外部副作用都不在该请求级保证范围内。

数据与 trace 默认在本项目的 `data/` 下。`GET /sessions/:id` 隐藏请求缓存，但返回当前历史与待办，因此部署为多人服务之前需要真实身份认证与授权机制。
