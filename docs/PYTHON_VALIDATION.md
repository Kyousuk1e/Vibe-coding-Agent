# Python 验证记录

本文件记录目录拆分前 Python 版本的实际检查结果，与原 Node.js 的77项测试及其真实API证据分别统计。

当前 Python 文件位于 `python/`。复验时先执行 `cd python`，再运行语法、unittest 和真实 API 命令；`python scripts/check_compat.py` 是例外，必须在仓库根目录运行。下列历史通过结果不等同于本次目录拆分后的验证。

日期：2026-09-10。本地环境：Windows，Python 3.14.7。实现与运行方式见 [Python 说明](../python/README.md)。

| 检查 | 实际结果 |
| --- | --- |
| `py scripts/check.py` | 30个Python文件语法检查通过 |
| `py -m unittest discover -s tests -v` | **95项通过，0失败，0跳过** |
| `py scripts/check_compat.py` | **Node → Python、Python → Node均通过**：实际Runtime创建待办及请求结果，另一语言加载并重放，无新模型调用 |
| 原Node.js回归 | 22个JS文件语法检查、77项离线测试全部通过 |
| Python服务与CLI入口 | 实际子进程启动服务，CLI通过自定义端口新建/查询会话，退出后核验JSON文件；不调用模型 |
| 真实千问API | **qwen-plus验收通过**：8轮真实对话 + 1项持久化检查，15次模型调用、7次工具执行 |
| GitHub Actions | 配置了 Windows/Linux × Python 3.11/3.14 矩阵；首次 Windows 3.11 失败定位为测试假设正常线程能在 10ms 内完成，需修正测试的时间依赖，不将本地通过记录当作全矩阵已通过 |

本轮因审查增加并修复的兼容性场景：CLI读取`.env`及环境中的PORT并在续聊命令保留自定义URL；HTTP显式`requestId:null`拒绝，而省略字段时生成新ID。相关单元和真实子进程入口测试通过。

## 目录分类后的检查

源码及测试已分入 `javascript/` 和 `python/`。以下结果与上面的历史验收分别记录：

| 检查 | 分类后的实际状态 |
| --- | --- |
| Python 语法 | 在 `python/` 执行 `python scripts/check.py`，29 个文件通过；根目录跨语言脚本不计入该数量 |
| JavaScript 回归 | 在 `javascript/` 执行检查，22 个文件语法及 77 项离线测试通过 |
| 两种语言 Session 兼容 | 在仓库根目录执行 `python scripts/check_compat.py`，Node → Python 与 Python → Node 均通过 |
| Python 完整离线回归 | 在 `python/` 重新执行 unittest，95 项通过，0 失败，0 跳过；包含实际服务与 CLI 子进程测试 |
| 远程 CI | 分类后的代码提交 `5ec3634`：4 个 Node、4 个 Python、2 个跨语言检查任务全部通过，见 [GitHub Actions 实际执行](https://github.com/Kyousuk1e/Vibe-coding-Agent/actions/runs/34493933994) |

Windows 3.11 的首次失败来自 `test_llm` 将普通工作线程限定为 10ms 的不稳定时间假设。测试已改用事件屏障确认阻塞、超时和迟到返回的顺序，并给正常调用合理预算；本地回归通过，生产 LLM 客户端未修改。

修正后 Windows/Linux × Python 3.11/3.14 均通过完整测试；Node.js 22/24 的两平台矩阵及两平台跨语言兼容检查也通过。本段记录的是上面链接中的代码提交，后续纯文档提交不改变该验收对象。

## 真实API验收

实际开始时间：2026-09-10T14:53:05.471726+00:00；结束时间：2026-09-10T14:53:25.002640+00:00。脚本退出码0，passed=true。模型调用15次，工具执行7次；供应商报告total_tokens合计26620。

| 场景 | 模型调用 | 工具执行及断言 |
| --- | --- | --- |
| 记住蓝鲸七号项目代号 | 1 | 无工具调用 |
| 纯对话追问代号 | 1 | 无工具调用，回答包含原代号 |
| `(123+456)*7` | 2 | calculator工具结果为4053，回答一致 |
| 搜索上下文压缩资料 | 2 | search的mock标识、来源、非空结果正确 |
| 上海天气 + 明天带伞 | 3 | weather与todo实际执行，单条待办文字精确匹配 |
| 独立窗口周报 + 待办 | 2 | todo实际执行，第二个Session仅保存周报待办 |
| 完成之前带伞待办 | 3 | todo list/complete执行，原ID保持、done=true |
| 新Runtime续聊 | 1 | 恢复项目代号与待办状态，不修改两个Session的待办 |

另通过新Store读取原文件检查持久化和会话隔离。原始报告存于仓库根目录被Git忽略的`docs/python-live-result.json`；公开记录只包含合成测试数据与汇总，不包含API密钥。

这份记录证明这些真实输入通过；LLM自主决策仍有不确定性。边界场景由可控的离线测试覆盖。新Runtime续聊是在同一进程中重建组件，不能等同于操作系统级重启恢复；服务/CLI子进程测试也未声称覆盖进程崩溃或断电恢复。
