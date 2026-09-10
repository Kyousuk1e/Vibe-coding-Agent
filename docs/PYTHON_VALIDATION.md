# Python 验证记录

本文件记录 Python 版本的实际检查结果，与原 Node.js 的77项测试及其真实API证据分别统计。

日期：2026-09-10。本地环境：Windows，Python 3.14.7。实现与运行方式见 [PYTHON.md](PYTHON.md)。

| 检查 | 实际结果 |
| --- | --- |
| `py scripts/check.py` | 30个Python文件语法检查通过 |
| `py -m unittest discover -s tests -v` | **95项通过，0失败，0跳过** |
| `py scripts/check_compat.py` | **Node → Python、Python → Node均通过**：实际Runtime创建待办及请求结果，另一语言加载并重放，无新模型调用 |
| 原Node.js回归 | 22个JS文件语法检查、77项离线测试全部通过 |
| Python服务与CLI入口 | 实际子进程启动服务，CLI通过自定义端口新建/查询会话，退出后核验JSON文件；不调用模型 |
| 真实千问API | **qwen-plus验收通过**：8轮真实对话 + 1项持久化检查，15次模型调用、7次工具执行 |
| GitHub Actions | 保留Node矩阵，新增Windows/Linux × Python 3.11/3.14矩阵；实际远程状态见仓库Actions |

本轮因审查增加并修复的兼容性场景：CLI读取`.env`及环境中的PORT并在续聊命令保留自定义URL；HTTP显式`requestId:null`拒绝，而省略字段时生成新ID。相关单元和真实子进程入口测试通过。

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

另通过新Store读取原文件检查持久化和会话隔离。原始报告存于被Git忽略的`docs/python-live-result.json`；公开记录只包含合成测试数据与汇总，不包含API密钥。

这份记录证明这些真实输入通过；LLM自主决策仍有不确定性。边界场景由可控的离线测试覆盖。新Runtime续聊是在同一进程中重建组件，不能等同于操作系统级重启恢复；服务/CLI子进程测试也未声称覆盖进程崩溃或断电恢复。
