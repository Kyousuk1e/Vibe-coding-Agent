"""Real Qwen acceptance. No mock fallback; requires a locally configured API key."""

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.app import create_app
from agent.config import read_config
from agent.jsonutil import dumps, loads


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def observations(session, tool_name):
    names, values = {}, []
    for message in session["messages"]:
        for call in message.get("tool_calls", []):
            names[call["id"]] = call["function"]["name"]
        if message["role"] == "tool" and names.get(message["tool_call_id"]) == tool_name:
            value = loads(message["content"])
            if value.get("ok"):
                values.append(value["data"])
    return values


async def run(report_path):
    # Never serialize Config: it contains the API credential.
    config = read_config()
    report = {"implementation": "python", "provider": config.provider, "model": config.model,
              "startedAt": datetime.now(timezone.utc).isoformat(), "passed": False, "turns": []}
    try:
        with tempfile.TemporaryDirectory(prefix="python-agent-live-") as data_dir:
            config = replace(config, data_dir=data_dir)
            app = create_app(config)
            a = await app.store.create("live-user", "天气和待办")
            b = await app.store.create("live-user", "周报和待办")

            async def turn(label, session, text, expected_tools=(), no_tools=False, instance=None):
                current = instance or app
                result = await current.runtime.run(user_id="live-user", session_id=session["id"], input=text)
                report["turns"].append({"label": label, "result": result})
                require(result["status"] == "ok", f"{label}: runtime did not finish successfully")
                calls = [e for e in result["trace"] if e["event"] == "tool.end"]
                for name in expected_tools:
                    require(any(e["tool"] == name and e["ok"] for e in calls), f"{label}: missing successful {name}")
                require(not no_tools or not calls, f"{label}: unexpected tool invocation")
                print(f"PASS {label} ({result['steps']} model calls, {len(calls)} tool executions)", flush=True)
                return result

            await turn("memory write", a, "我的项目代号是蓝鲸七号，请记住。", no_tools=True)
            result = await turn("conversation followup", a, "我的项目代号是什么？", no_tools=True)
            require("蓝鲸七号" in result["answer"], "conversation memory was not recalled")
            result = await turn("calculator", a, "请计算 (123+456)*7", ["calculator"])
            require("4053" in result["answer"], "incorrect calculator answer")
            require(observations(await app.store.get("live-user", a["id"]), "calculator")[-1]["result"] == 4053,
                    "incorrect calculator tool result")
            await turn("search", a, "请使用search搜索上下文压缩资料，并注明资料来源。", ["search"])
            search = observations(await app.store.get("live-user", a["id"]), "search")[-1]
            require(search["mock"] is True and search["source"] == "local-demo-corpus" and search["results"], "search source mismatch")
            await turn("weather and todo", a, "查询上海天气，并添加一条文字恰好为“明天带伞”的待办。", ["weather", "todo"])
            saved_a = await app.store.get("live-user", a["id"])
            weather = observations(saved_a, "weather")[-1]
            require(weather["mock"] is True and weather["city"] == "上海", "weather source mismatch")
            require(len(saved_a["todos"]) == 1 and saved_a["todos"][0]["text"] == "明天带伞", "first todo was not saved exactly")
            todo_id = saved_a["todos"][0]["id"]
            await turn("separate weekly report", b, "本周完成工具注册，下周补测试。写一段简短周报，并添加文字恰好为“周五提交周报”的待办。", ["todo"])
            saved_b = await app.store.get("live-user", b["id"])
            require(len(saved_b["todos"]) == 1 and saved_b["todos"][0]["text"] == "周五提交周报", "second session todo mismatch")
            await turn("tool followup", a, "把刚才明天带伞的待办标记完成。", ["todo"])
            saved_a = await app.store.get("live-user", a["id"])
            require(len(saved_a["todos"]) == 1 and saved_a["todos"][0]["id"] == todo_id and saved_a["todos"][0]["done"] is True,
                    "followup did not update the original todo")
            require(any(o.get("action") == "complete" for o in observations(saved_a, "todo")), "missing complete operation")
            restored = create_app(config)
            result = await turn("new runtime recovery", a, "我的项目代号是什么？明天带伞完成了吗？只查询，不要修改待办。", instance=restored)
            require("蓝鲸七号" in result["answer"] and "完成" in result["answer"], "restored context was not recalled")
            require((await restored.store.get("live-user", a["id"]))["todos"] == saved_a["todos"], "recovery changed first todos")
            require((await restored.store.get("live-user", b["id"]))["todos"] == saved_b["todos"], "session isolation failed")
            report["persistenceCheck"] = "passed: new Store/Runtime read same files; same process"
            report["passed"] = True
    except Exception as exc:
        # Only our synthetic assertion labels or a safe public error code are emitted.
        report["failure"] = str(exc) if isinstance(exc, AssertionError) else getattr(exc, "code", type(exc).__name__)
    finally:
        report["finishedAt"] = datetime.now(timezone.utc).isoformat()
        events = [e for turn in report["turns"] for e in turn["result"]["trace"]]
        report["modelCalls"] = sum(e["event"] == "llm.start" for e in events)
        report["toolExecutions"] = sum(e["event"] == "tool.end" for e in events)
        report["totalTokens"] = sum(e.get("usage", {}).get("total_tokens", 0) for e in events)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(dumps(report) + "\n", encoding="utf-8")
        print(dumps({k: report[k] for k in ("passed", "modelCalls", "toolExecutions", "totalTokens")}))
        if not report["passed"]:
            print("FAIL " + report["failure"], file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path,
                        default=Path(__file__).resolve().parents[2] / "docs/python-live-result.json")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args.report)))
    except Exception as exc:
        print("Live verification could not start: " + getattr(exc, "code", type(exc).__name__), file=sys.stderr)
        raise SystemExit(1)
