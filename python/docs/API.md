# 本地 HTTP API

本项目的 [HTTP 服务](../agent/server.py) 由 Python 标准库实现。下列命令在包含 `agent/` 的项目根目录执行：

```powershell
python -m agent serve
```

Windows 可使用 `py`。默认地址为 `http://127.0.0.1:8787`。除 `/health` 外，接口需要 `X-User-Id`，内容为 1–64 个字母、数字、下划线或连字符。这个 header 用于本地演示归属，不是身份认证凭证。

POST 使用 `Content-Type: application/json` 和 Content-Length，请求体不超过 32KB；不支持 chunked 请求体，响应后关闭连接。携带 Origin 的浏览器请求返回 403，不提供 CORS 或流式响应。

## 接口

| 方法与路径 | 输入 | 返回 |
| --- | --- | --- |
| `GET /health` | 无 | `200` 与 `{"ok":true}` |
| `GET /tools` | 用户 header | `200` 与工具名称、描述和参数 Schema |
| `POST /sessions` | `{"title":"天气和待办"}`，title 可省略 | `201` 与新会话，包括 `id` |
| `GET /sessions` | 用户 header | `200` 与该用户的会话元数据列表 |
| `GET /sessions/:id` | 用户 header | `200` 与历史、摘要、待办；不返回请求结果缓存 |
| `POST /sessions/:id/messages` | `{"input":"你好","requestId":"req-001"}` | `200` 与本轮结果、状态和 trace |

标题最长 200 字符。input 必须为非空字符串，最长 4000 个 Unicode 字符。requestId 可省略，此时后端生成 UUID；显式提供时必须为 1–100 个字母、数字、下划线或连字符，不能为 null 或保留名称。

## 完整调用示例

先启动本地服务，再在 PowerShell 执行：

```powershell
$agentHeaders = @{ 'X-User-Id' = 'A' }
$agentSession = Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8787/sessions' -Headers $agentHeaders `
  -ContentType 'application/json; charset=utf-8' -Body '{"title":"计算演示"}'
$agentMessage = @{ input = '请用计算器算 (12+8)*3'; requestId = 'demo-001' } | ConvertTo-Json
$agentResult = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8787/sessions/$($agentSession.id)/messages" `
  -Headers $agentHeaders -ContentType 'application/json; charset=utf-8' -Body $agentMessage
$agentResult.answer
$agentResult.trace | ConvertTo-Json -Depth 10
```

响应结构示意，下面是协议示例而非实测日志：

```json
{
  "requestId": "demo-001",
  "sessionId": "会话UUID",
  "status": "ok",
  "answer": "结果是60。",
  "decisionSummary": "根据计算器结果回答",
  "steps": 2,
  "trace": [
    {"event":"tool.start","tool":"calculator","callId":"call_01"},
    {"event":"tool.end","tool":"calculator","callId":"call_01","ok":true}
  ],
  "replayed": false
}
```

## 状态与幂等

| status | 含义与保存行为 |
| --- | --- |
| `ok` | 模型已产生本轮最终回答，状态和对话已保存 |
| `max_steps` | 达到模型决策轮数上限；保留已经成功的本地工具修改 |
| `error` | 本轮执行失败，todos 回滚到回合开始前；保存用户输入和失败说明 |

这三类已完成保存的用户回合均返回 HTTP 200，客户端仍需检查业务 status。模型的自然语言承诺不能替代工具执行记录。

未收到网络响应时，在同一 Session 内复用原 input 和 requestId。服务端命中缓存后直接返回原结果，`replayed=true`，不再次调用模型或工具。缓存只保留最近 20 个请求，相同 ID 配不同输入会冲突；已保存的 error 与 max_steps 也会重放。确认原请求已经失败后，需要重新执行时使用新的请求 ID。

## HTTP 错误

| HTTP 状态码 | 示例 |
| --- | --- |
| 400 | JSON 无效、字段类型错误、无效标题或 ID、请求体超限 |
| 403 | 请求携带浏览器 Origin |
| 404 | 当前用户没有该 Session，或接口不存在 |
| 409 | 相同 requestId 对应不同输入 |
| 500 | 损坏会话、文件保存故障等服务端错误 |

错误结构为 `{"error":{"code":"错误码","message":"说明"}}`。服务端内部错误不直接暴露底层响应或凭据。运行与设计见 [README](../README.md)，对应接口测试见 [test_server.py](../tests/test_server.py)。
