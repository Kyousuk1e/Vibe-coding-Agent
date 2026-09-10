# 本地 HTTP API

基础地址 `http://127.0.0.1:8787`。除 `/health` 外都需要 `X-User-Id`（1–64 位字母、数字、下划线或连字符）。服务用于本机演示，这个 header 不是认证凭证。POST 使用 `Content-Type: application/json`，请求体最多 32KB。

| 方法与路径 | 输入 | 返回 |
| --- | --- | --- |
| GET /health | 无 | `{ "ok": true }` |
| GET /tools | 用户 header | 工具名称、描述、Schema |
| POST /sessions | `{ "title": "天气和待办" }`，title 可省略 | 201 + 新 session，包含 id |
| GET /sessions | 用户 header | 当前用户 session 元数据列表 |
| GET /sessions/:id | 用户 header | 对话、摘要、待办，不含幂等缓存 |
| POST /sessions/:id/messages | `{ "input": "你好", "requestId": "req-001" }` | 本轮结果与 trace |

PowerShell 完整示例（先 `npm start`）：

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

响应结构示意（数值与内容随真实模型变化，不是实际执行证据）：

```json
{
  "requestId": "demo-001",
  "sessionId": "服务生成的UUID",
  "status": "ok",
  "answer": "结果是60。",
  "decisionSummary": "根据计算器结果回答",
  "steps": 2,
  "trace": [
    {"event":"tool.start","tool":"calculator","callId":"模型生成的ID"},
    {"event":"tool.end","tool":"calculator","ok":true,"durationMs":1}
  ],
  "replayed": false
}
```

`status` 为 `ok`、`max_steps` 或 `error`。已执行并保存的用户回合返回 HTTP 200，客户端仍必须检查 status；`error` 回合的本地待办修改已回滚。网络重试应使用相同 requestId；收到 error 后要重新执行，应使用新的 requestId。最近 20 个请求内可幂等重放。

无效参数返回 400；不属于当前用户或不存在的 session 返回 404；同一 requestId 用于不同输入返回 409；数据文件损坏/持久化故障返回 500。错误格式为 `{ "error": { "code": "...", "message": "..." } }`。不提供跨域浏览器调用支持。
