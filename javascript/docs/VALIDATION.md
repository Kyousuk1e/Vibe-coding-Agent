# JavaScript 验证记录

本文区分已有可核验的历史结果与独立项目整理后的验证。历史结果不会被当作新目录、独立复制或新 CI 已通过的证据。所有复验命令均在本项目根目录执行。

## 已有本地验证

日期：2026-09-10。环境：Windows，Node.js v24.18.0。

| 检查 | 已记录结果 |
| --- | --- |
| `npm run check` | 22 个 JavaScript 文件语法检查通过 |
| `npm test` | 77 项通过，0 失败，0 跳过 |
| Runtime / HTTP / 文件持久化集成 | 包含在离线测试中，使用真实模块及临时目录 |
| 真实千问 API | qwen-plus，8 轮真实对话 + 1 项持久化检查通过；15 次模型调用，7 次工具执行；退出码 0 |
| 多意图真实回归 | 周报 + 待办的 3 种独立表述均正确执行 todo 并持久化，3/3 通过 |

在先前将代码移动到 `javascript/` 的目录分类阶段，已经重新运行 `npm run check` 与 `npm test`，结果仍为 22 个文件及 77 项通过。

## 本次独立复制验收

2026-09-10 将本项目单独复制到临时目录，排除实际 `.env`；该目录没有父仓库源码或其他项目可供引用。以复制后的目录作为当前工作目录执行：

| 检查 | 实际结果 |
| --- | --- |
| `npm run check` | 22 个 JavaScript 文件通过 |
| `npm test` | 77 项通过，0 失败，0 跳过 |
| 独立 Git 忽略规则 | 临时 `git init` 后确认 `.env`、`data/`、`docs/live-result.json` 被忽略，`.env.example` 可提交 |
| 独立 CI 配置 | 本项目自带工作流；父仓库仅复制本项目进行远程检查，实际执行状态见 [独立项目 CI](https://github.com/Kyousuk1e/Vibe-coding-Agent/actions/workflows/javascript.yml) |

这次验收验证了独立目录的离线运行与配置完整性，没有重新发起真实 LLM 请求。下列真实调用数据仍保留原始时间。

真实模型通过时间为 2026-09-10 12:19:25–12:19:43（Asia/Shanghai），详见 [真实 API 证据](LIVE_API_EVIDENCE.md)。没有把可控 LLM 响应算成真实调用成功，也没有把第一次失败算成通过。原始报告由本项目脚本生成至 `docs/live-result.json`，默认被 Git 忽略。

## 历史远程 CI

本次独立项目的远程检查已通过：2026-09-10，提交 `5e853d5`，Windows/Linux × Node.js 22/24 共 4 个任务全部成功。[本次独立项目执行记录](https://github.com/Kyousuk1e/Vibe-coding-Agent/actions/runs/34496604216)。每个任务只把本项目复制到独立临时目录，再运行语法检查和 77 项离线测试；执行不依赖父目录共享源码。该记录对应这个代码与工作流提交，后续纯文档更新不改变验证对象。

- 提交 `874614c`：Windows/Linux × Node.js 22/24 的 4 个任务全部通过。[历史运行](https://github.com/Kyousuk1e/Vibe-coding-Agent/actions/runs/34436306389)
- 提交 `5ec3634`：目录分类后，上述 Node.js 4 个组合在综合 CI 中通过。[历史运行](https://github.com/Kyousuk1e/Vibe-coding-Agent/actions/runs/34493933994)

本项目自带 [.github/workflows/test.yml](../.github/workflows/test.yml)，将本目录单独作为 GitHub 仓库根目录时可以运行。上面的实际记录来自父仓库的单项目复制检查；本项目尚未被发布为另一个独立 GitHub 仓库。

## 如何复验

```powershell
npm run check
npm test
# 本地 .env 配好真实千问密钥后显式运行，产生 API 用量：
npm run test:live
```

前两项无网络和密钥要求。真实验收使用独立临时数据目录，不修改日常使用的会话；报告中只包含合成输入、输出、工具 trace、状态和模型用量，不包含认证凭据。

恢复验证通过同一 Node.js 进程中创建新的 client/runtime/context/store，再读取原磁盘会话继续对话完成。它没有模拟操作系统崩溃、跨机器恢复或完整服务进程重启。上下文压缩的硬边界通过离线测试验证，没有在真实验收中强制模型上下文填满。

## 覆盖与限制

回归覆盖了待办固定 memory 容量、超时工具迟到写入、嵌套状态损坏、非法标题 HTTP 状态码、数字 requestId 淘汰顺序等实际问题。测试设计和业务断言见 [TEST_CASES.md](TEST_CASES.md)，问题修复过程见 [AI_PROMPTS_AND_LOG.md](AI_PROMPTS_AND_LOG.md)。

模型输出不确定，当前真实样例通过不能证明任意输入都不漏调用。文件原子替换与内存锁也不代表跨服务进程、外部订单系统或所有崩溃时机都有事务保证。
