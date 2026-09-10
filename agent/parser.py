"""Parse native tool calls and final answers, never hidden reasoning fields."""
import json
import math
import re

from .errors import AgentError


def _protocol():
    return AgentError("LLM_PROTOCOL", "LLM completion has an invalid structure.")


def _reject_constant(_value):
    raise ValueError("Non-finite JSON constants are not supported")


def _load_json(text):
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON number")
        return number
    return json.loads(text, parse_constant=_reject_constant, parse_float=finite_float)


def _parse_content(content):
    if content is None:
        return {"answer": "", "decisionSummary": ""}
    if not isinstance(content, str) or len(content) > 128000:
        raise _protocol()
    text = content.strip()
    if not text:
        return {"answer": "", "decisionSummary": ""}
    fence = re.fullmatch(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text, re.IGNORECASE)
    candidate = fence.group(1).strip() if fence else text
    if candidate.startswith("{"):
        try:
            parsed = _load_json(candidate)
        except (ValueError, RecursionError):
            if fence or re.search(r"[\"'](?:decision_summary|answer)[\"']\s*:", candidate):
                raise _protocol() from None
            return {"answer": text, "decisionSummary": "直接回答用户。"}
        if isinstance(parsed, dict) and ("answer" in parsed or "decision_summary" in parsed):
            answer = parsed.get("answer")
            summary = parsed.get("decision_summary", "直接回答用户。")
            if not isinstance(answer, str) or not answer.strip() or not isinstance(summary, str):
                raise _protocol()
            return {"answer": answer.strip(), "decisionSummary": summary.strip()[:300]}
    return {"answer": text, "decisionSummary": "直接回答用户。"}


def parse_completion(response):
    if not isinstance(response, dict) or not isinstance(response.get("choices"), list) or not response["choices"]:
        raise _protocol()
    choice = response["choices"][0]
    if not isinstance(choice, dict):
        raise _protocol()
    if choice.get("finish_reason") == "length":
        raise AgentError("LLM_TRUNCATED", "LLM completion was truncated.")
    if choice.get("finish_reason") == "content_filter":
        raise AgentError("LLM_CONTENT_FILTER", "LLM response was filtered.")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
        raise _protocol()
    if "function_call" in message:
        raise _protocol()
    native_calls = message.get("tool_calls")
    if native_calls is not None and not isinstance(native_calls, list):
        raise _protocol()
    native_calls = native_calls or []
    if native_calls:
        content = message.get("content")
        if len(native_calls) > 32 or (content is not None and (not isinstance(content, str) or len(content) > 128000)):
            raise _protocol()
        ids, calls, tool_calls = set(), [], []
        for call in native_calls:
            if not isinstance(call, dict) or call.get("type") != "function":
                raise _protocol()
            call_id, function = call.get("id"), call.get("function")
            if not isinstance(call_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call_id) or call_id in ids or not isinstance(function, dict):
                raise _protocol()
            name, raw_arguments = function.get("name"), function.get("arguments")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not isinstance(raw_arguments, str) or len(raw_arguments) > 65536:
                raise _protocol()
            ids.add(call_id)
            args, argument_error = None, None
            try:
                args = _load_json(raw_arguments)
                if not isinstance(args, dict):
                    args, argument_error = None, "Tool arguments must be a JSON object."
            except (ValueError, RecursionError):
                argument_error = "Tool arguments contain invalid JSON."
            parsed = {"id": call_id, "name": name, "args": args}
            if argument_error:
                parsed["argumentError"] = argument_error
            calls.append(parsed)
            # Preserve malformed JSON verbatim for a valid assistant/tool protocol pairing.
            tool_calls.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": raw_arguments}})
        return {
            "type": "tools", "calls": calls, "answer": "",
            "decisionSummary": ("调用工具：" + "、".join(call["name"] for call in calls))[:300],
            "assistantMessage": {"role": "assistant", "content": content, "tool_calls": tool_calls},
        }
    if choice.get("finish_reason") == "tool_calls":
        raise _protocol()
    refusal = message.get("refusal")
    if refusal is not None:
        if not isinstance(refusal, str) or not refusal.strip() or len(refusal) > 128000:
            raise _protocol()
        parsed = {"answer": refusal.strip(), "decisionSummary": "模型拒绝了该请求。"}
    else:
        parsed = _parse_content(message.get("content"))
    if not parsed["answer"]:
        raise _protocol()
    return {"type": "final", **parsed, "calls": [], "assistantMessage": {"role": "assistant", "content": parsed["answer"]}}
