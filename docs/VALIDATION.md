# JavaScript / Node.js 历史验证记录

以下为目录拆分前的实际验收记录，保留原结果。当前源码在 `javascript/`；复验时先从仓库根目录执行 `cd javascript`，再运行下表 npm 命令。目录拆分后的新增验证由实际检查另行记录。

日期：2026-09-10。环境：Windows，Node.js v24.18.0。

| 检查 | 实际结果 |
| --- | --- |
| `npm run check` | 22 个 JavaScript 文件语法检查通过 |
| `npm test` | 77 项通过，0 失败，0 跳过 |
| Runtime/HTTP/文件持久化集成 | 已包含在离线测试中，使用真实模块与独立临时目录 |
| 真实千问 API 验收 | **已通过**：qwen-plus，8轮真实对话 + 1项持久化检查，15次模型调用、7次工具执行，退出码0；见 [真实API证据](LIVE_API_EVIDENCE.md) |
| 多项任务真实回归 | 周报 + 待办的3种独立表述均产生成功工具 trace 并正确持久化，3/3通过 |
| GitHub Actions 多平台验证 | Windows/Linux × Node 22/24 的4个任务全部通过，代码提交 `874614c`，[运行记录](https://github.com/Kyousuk1e/Vibe-coding-Agent/actions/runs/34436306389) |

没有把离线可控 LLM 响应计作真实 API 成功证据。首次缺少密钥的检查在发起请求前失败；配置后曾发现模型“声称添加待办却未调用工具”的真实失败。加强多项任务完成检查 Prompt 后，保留并加强原断言重新验收通过，未把失败样本计为成功。详见 [AI开发记录](AI_PROMPTS_AND_LOG.md)。

通过时间：2026-09-10 12:19:25–12:19:43（Asia/Shanghai）。原始回答与工具 trace 保存在仓库根目录被 Git 忽略的 `docs/live-result.json`；公开证据仅包含合成测试数据、结果和用量，不含认证凭据。新 Runtime 续聊通过创建新的 client/runtime/context/store 并加载相同磁盘文件验证；没有声称这是完整操作系统进程重启测试。上下文压缩的边界验证由77项离线测试中的集成用例覆盖。

本次独立审查发现并修复了：待办固定 memory 容量、超时工具迟到写入、嵌套状态损坏、非法标题 HTTP 状态码、数字 requestId 淘汰顺序。对应回归测试已包含在通过结果中。
