# Python 验证记录

本文件区分历史验证与当前完全独立项目的复制验证。所有命令在本项目根目录执行，Windows 可使用 `py`。

## 已执行的历史验证

日期：2026-09-10。本地环境：Windows、Python 3.14.7。最初 Python 实现及真实 API 记录保存在提交 `04a530c`；后续目录分类与确定性超时测试对应提交 `5ec3634`，验证记录提交为 `aed5a96`。这三个提交早于当前独立文档与独立打包调整。

| 检查 | 已记录的实际结果 |
| --- | --- |
| 语法检查 | 在目录分类后扫描 29 个项目 Python 文件，全部通过 |
| `py -m unittest discover -s tests -v` | 95 项通过，0 失败，0 跳过 |
| 服务与 CLI 子进程 | 实际启动服务，在自定义端口创建/读取 Session，退出后检查 JSON；不调用模型 |
| 真实千问 API | qwen-plus，8 轮真实对话和 1 项持久化检查通过；15 次模型调用、7 次工具执行 |
| 远程 Python 矩阵 | 提交 `5ec3634` 的 Windows/Linux × Python 3.11/3.14 检查全部通过 |

历史远程执行记录见 [GitHub Actions](https://github.com/Kyousuk1e/Vibe-coding-Agent/actions/runs/34493933994)。这里只引用其中 Python 的实际检查结果，不将旧提交的成功当作当前独立复制结果。

首次 Windows 3.11 失败来自测试假设普通线程应在 10ms 内完成。相关测试已使用事件屏障确定阻塞、超时和迟到返回的顺序，正常调用也使用合理预算；修正后上面四种 Python 环境均通过。生产 LLM 客户端不因这次测试修正而改变。

## 当前独立项目验证

日期：2026-09-10。本次把项目目录单独复制到一个不包含父仓库源码的临时目录，并排除实际 `.env`，再进行检查。以下是这次独立复制的实际结果，执行时尚未生成本轮最终提交 ID：

| 检查 | 实际结果 |
| --- | --- |
| `py scripts/check.py` | 29 个 Python 文件语法检查通过 |
| `py -m unittest discover -s tests -v` | 95 项通过，0 失败，0 跳过 |
| 独立服务、CLI 与本地配置入口 | 包含在上述测试中，实际启动 serve/chat 子进程通过 |
| 独立 Git 忽略规则 | 临时目录单独 git init；`.env`、data 和 docs/live-result.json 被忽略，`.env.example` 可正常纳入版本控制 |
| 本次调整后的真实 API 重跑 | 未以本次独立目录为对象重新执行；下面保留既有真实证据 |
| 独立工作流远程运行 | 见 [Python CI 执行记录](https://github.com/Kyousuk1e/Vibe-coding-Agent/actions/workflows/python.yml)，以对应提交的实际任务结果为准 |

项目自带 [.github/workflows/test.yml](../.github/workflows/test.yml)，可在独立仓库执行 Python 矩阵。后续实际远程结果应记录检查时间、提交及通过数量。

## 真实 API 历史结果与边界

实际开始：2026-09-10T14:53:05.471726+00:00；结束：2026-09-10T14:53:25.002640+00:00。脚本退出码 0，passed=true。供应商报告 total_tokens 合计 **26620**。

完整场景和计数见 [LIVE_API_EVIDENCE.md](LIVE_API_EVIDENCE.md)。当前脚本的原始报告路径为本项目 `docs/live-result.json`，默认不提交到 Git；路径调整不代表重新进行了模型验收。

真实成功只说明列出的输入在那次执行中通过。LLM 决策存在不确定性，超时、轮数、压缩和状态损坏等边界主要由离线测试验证。新 Runtime 恢复发生于同一进程内，不能等同于操作系统崩溃或断电恢复。
