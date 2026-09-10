# 从零实现一个最小可用 Agent

使用真实千问 API，自行实现 Agent Runtime、工具调用、Session 管理、上下文压缩、异常处理和 trace。两种语言分别放在独立目录，均无需第三方运行依赖，也没有使用现成 Agent 框架。

原有版本是 **JavaScript / Node.js**，不是 Java；新增版本是 **Python**。两版提供相同的核心业务：日常安排、工作整理、信息查询和计算。

| 内容 | JavaScript / Node.js | Python |
| --- | --- | --- |
| 独立目录与说明 | [javascript/README.md](javascript/README.md) | [python/README.md](python/README.md) |
| 运行环境 | Node.js 22.13+ | Python 3.11+ |
| 核心 Runtime | [javascript/src/runtime.js](javascript/src/runtime.js) | [python/agent/runtime.py](python/agent/runtime.py) |
| 源码 | [javascript/src/](javascript/src/) | [python/agent/](python/agent/) |
| 测试 | [javascript/test/](javascript/test/) | [python/tests/](python/tests/) |
| 本地配置 | `javascript/.env` | `python/.env` |
| 默认数据目录 | `javascript/data/` | `python/data/` |

两版均使用真实 LLM 自主决策，提供 `calculator`、`search`、`todo`、`weather` 四个工具。search 和 weather 明确使用 mock 数据；计算及当前 Session 的待办修改真实执行。默认模型为千问 `qwen-plus`，生产入口不会回退到模拟 LLM。

## 运行 JavaScript 版

从仓库根目录进入该版本，并配置本地密钥：

```powershell
cd javascript
Copy-Item .env.example .env
# 编辑本目录 .env，填写 DASHSCOPE_API_KEY
npm start
```

另开终端，同样先进入 `javascript/`：

```powershell
npm run chat -- --user A
```

## 运行 Python 版

从仓库根目录进入该版本，并配置本地密钥：

```powershell
cd python
Copy-Item .env.example .env
# 编辑本目录 .env，填写 DASHSCOPE_API_KEY
python -m agent serve
```

另开终端，同样先进入 `python/`：

```powershell
python -m agent chat --user A
```

Windows 上可用 `py` 替代 Python 命令。已经配置过本版本的 `.env` 时可直接启动，无需重新复制模板。两个服务默认都使用端口 8787；同时运行时为其中一个配置不同的 `PORT`，并让客户端连接对应地址。

每个聊天窗口默认建立独立 Session，使用 `--session ID` 可续聊。两版 Session JSON 字段兼容；切换实现并续用同一份数据时，配置相同 `DATA_DIR` 且先停止原服务。**同一数据目录仅允许一个服务进程写入**。

## 测试与提交资料

| 检查 | 在 `javascript/` 执行 | 在 `python/` 执行 |
| --- | --- | --- |
| 语法检查 | `npm run check` | `python scripts/check.py` |
| 离线测试 | `npm test` | `python -m unittest discover -s tests -v` |
| 真实 API 验收 | `npm run test:live` | `python scripts/live_smoke.py` |

离线测试不需要密钥。真实验收使用实际千问 API 并产生用量，单独检查工具 trace 和持久化状态。跨语言 Session 兼容检查在**仓库根目录**执行 `python scripts/check_compat.py`，需要 Node.js 和 Python，不调用模型。

共同提交资料保留在根目录 [docs/](docs/)：

- [系统接口说明](docs/API.md)
- [测试用例与要求映射](docs/TEST_CASES.md)
- [JavaScript 历史验证记录](docs/VALIDATION.md)与[真实千问证据](docs/LIVE_API_EVIDENCE.md)
- [Python 验证记录](docs/PYTHON_VALIDATION.md)
- [AI Prompt 与问题解决记录](docs/AI_PROMPTS_AND_LOG.md)

两版 README 分别说明系统设计和 memory 的召回时机、放置方式及压缩边界。验证记录注明执行时间及适用版本，历史通过记录不代表之后每次目录调整的 CI 已通过。

代码仓库：[Kyousuk1e/Vibe-coding-Agent](https://github.com/Kyousuk1e/Vibe-coding-Agent)。
