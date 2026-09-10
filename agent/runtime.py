"""Hand-written loop: model decision -> tool -> observation -> model."""

import asyncio
import copy
import hashlib
import re
import time
import uuid

from .errors import AgentError
from .jsonutil import dumps
from .parser import parse_completion
from .prompt import SYSTEM_PROMPT
from .trace import preview, timestamp


class AgentRuntime:
    def __init__(self, *, store, client, registry, context, trace_writer=None,
                 max_steps=8, max_calls_per_step=4):
        if type(max_steps) is not int or not 1 <= max_steps <= 30:
            raise AgentError("CONFIG", "max_steps 必须在 1–30 之间")
        if type(max_calls_per_step) is not int or not 1 <= max_calls_per_step <= 16:
            raise AgentError("CONFIG", "max_calls_per_step 必须在 1–16 之间")
        self.store, self.client, self.registry = store, client, registry
        self.context, self.trace_writer = context, trace_writer
        self.max_steps, self.max_calls_per_step = max_steps, max_calls_per_step

    async def run(self, *, user_id, session_id, input, request_id=None):
        if not isinstance(input, str) or not input.strip() or len(input) > 4000:
            raise AgentError("BAD_INPUT", "输入应为 1–4000 字符")
        if request_id is None:
            request_id = str(uuid.uuid4())
        if (not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", request_id)
                or request_id in {"__proto__", "constructor", "prototype"}):
            raise AgentError("BAD_INPUT", "requestId 格式无效")
        input_hash = hashlib.sha256(input.encode("utf-8")).hexdigest()
        # Lock before reading/cache lookup, and release only after the final save.
        async with self.store.lock(user_id, session_id):
            session = await self.store.get(user_id, session_id)
            completed = session.setdefault("completedRequests", {})
            if request_id in completed:
                old = completed[request_id]
                if old.get("inputHash") != input_hash:
                    raise AgentError("REQUEST_CONFLICT", "同一 requestId 不能用于不同输入")
                return {**copy.deepcopy(old["result"]), "replayed": True}
            original_todos = copy.deepcopy(session["todos"])
            current_messages = [{"role": "user", "content": input}]
            trace, started = [], time.monotonic()

            def emit(event, **details):
                trace.append({"timestamp": timestamp(), "requestId": request_id,
                              "sessionId": session_id, "event": event, **details})

            answer, status, decision_summary, steps = "", "ok", "", 0
            emit("run.start")
            try:
                for step in range(1, self.max_steps + 1):
                    steps = step
                    tools = self.registry.schemas()
                    built = self.context.build(session, system_prompt=SYSTEM_PROMPT,
                                               tools=tools, current_messages=current_messages)
                    emit("context.ready", step=step, **built["stats"])
                    emit("llm.start", step=step)
                    raw = await self.client.complete(messages=built["messages"], tools=tools)
                    parsed = parse_completion(raw)
                    decision_summary = parsed["decisionSummary"]
                    raw_usage = raw.get("usage") or {}
                    usage = {k: v for k, v in (raw_usage.items() if isinstance(raw_usage, dict) else [])
                             if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                             and type(v) is int and v >= 0}
                    emit("llm.decision", step=step, type=parsed["type"],
                         decisionSummary=preview(decision_summary, 300), usage=usage)
                    if parsed["type"] == "final":
                        answer = parsed["answer"]
                        current_messages.append({"role": "assistant", "content": answer})
                        break
                    if len(parsed["calls"]) > self.max_calls_per_step:
                        raise AgentError("TOO_MANY_TOOLS", "模型一次请求了过多工具")
                    current_messages.append(parsed["assistantMessage"])
                    for call in parsed["calls"]:
                        tool_start = time.monotonic()
                        emit("tool.start", step=step, callId=call["id"], tool=call["name"],
                             arguments=preview(call.get("args", call.get("argumentError"))))
                        if call.get("argumentError"):
                            result = {"ok": False, "error": {"code": "INVALID_ARGUMENTS",
                                      "message": "工具参数不是合法 JSON 对象，请按 Schema 修正。"}}
                        else:
                            result = await self.registry.execute(call["name"], call["args"], {"session": session})
                        current_messages.append({"role": "tool", "tool_call_id": call["id"], "content": dumps(result)})
                        emit("tool.end", step=step, callId=call["id"], tool=call["name"],
                             ok=result["ok"], result=preview(result),
                             durationMs=round((time.monotonic() - tool_start) * 1000))
                if not answer:
                    status = "max_steps"
                    answer = (f"已达到本轮 {self.max_steps} 次模型调用上限，任务可能尚未完成。"
                              "已成功执行的工具结果和待办已保存；可以继续追问。")
                    current_messages.append({"role": "assistant", "content": answer})
            except (Exception, asyncio.CancelledError) as exc:
                status = "error"
                session["todos"] = original_todos
                code = getattr(exc, "code", "CANCELLED" if isinstance(exc, asyncio.CancelledError) else "RUNTIME_ERROR")
                safe_messages = {
                    "CONTEXT_LIMIT": "本轮上下文超过限制，请缩短输入或新建会话。",
                    "LLM_AUTH": "LLM 认证失败，请检查服务端 API Key。",
                    "LLM_TIMEOUT": "LLM 请求超时，请稍后重试。",
                    "LLM_PROTOCOL": "LLM 返回格式无效，请重试。",
                    "LLM_TRUNCATED": "LLM 输出被截断，请提高输出长度限制或缩小任务。",
                    "CANCELLED": "本轮执行已取消。",
                }
                answer = safe_messages.get(code, "本轮执行失败，请检查配置或 trace 后重试。") + " 本轮待办修改未提交。"
                current_messages[1:] = [{"role": "assistant", "content": answer}]
                emit("run.error", code=code, **({"httpStatus": exc.status} if type(getattr(exc, "status", None)) is int else {}))
            session["messages"].extend(current_messages)
            emit("run.end", status=status, steps=steps, durationMs=round((time.monotonic() - started) * 1000))
            result = {"requestId": request_id, "sessionId": session_id, "status": status,
                      "answer": answer, "decisionSummary": "" if status == "error" else decision_summary,
                      "steps": steps, "trace": trace, "replayed": False}
            ordered = sorted(completed.items(), key=lambda item: item[1].get("sequence", 0))
            ordered.append((request_id, {"inputHash": input_hash, "result": result}))
            session["completedRequests"] = {key: {**record, "sequence": sequence}
                                             for sequence, (key, record) in enumerate(ordered[-20:])}
            await self.store.save(session)
            if self.trace_writer:
                try:
                    await self.trace_writer.write(user_id, session_id, trace)
                except Exception:
                    result["traceWarning"] = "会话已保存，但 trace 文件写入失败。"
            return result
