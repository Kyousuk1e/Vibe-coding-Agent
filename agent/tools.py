"""Four independently registered tools. Search and weather are explicit demo data."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from uuid import uuid4

from .jsonutil import dumps
from .registry import ToolError, ToolRegistry


def _bad(message: str) -> None:
    raise ToolError("INVALID_ARGUMENTS", message)


def calculate(expression: str) -> float:
    """Bounded recursive-descent arithmetic grammar; no eval, names or calls.

    IEEE-754 floats and fmod retain the original calculator's numerical behavior,
    including -7 % 4 == -3 and right-associative exponentiation.
    """
    if not isinstance(expression, str) or len(expression) > 500:
        _bad("Expression must contain at most 500 characters")
    pattern = re.compile(r"\s*(?:(\d+(?:\.\d*)?|\.\d+)([eE][+-]?\d+)?|(\*\*|[+\-*/%()]))", re.ASCII)
    tokens: list[str | float] = []
    offset = 0
    while offset < len(expression):
        if not expression[offset:].strip():
            break
        match = pattern.match(expression, offset)
        if match is None:
            _bad("Expression contains unsupported syntax")
        tokens.append(match[3] if match[3] else float(match[1] + (match[2] or "")))
        offset = match.end()
        if len(tokens) > 200:
            _bad("Expression has too many tokens")
    position = depth = 0

    def finite(value: float) -> float:
        if not math.isfinite(value):
            _bad("Calculation must produce a finite real number")
        return 0.0 if value == 0 else value

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take():
        nonlocal position
        token = peek()
        position += 1
        return token

    def nested(function):
        nonlocal depth
        depth += 1
        if depth > 32:
            _bad("Expression nesting exceeds 32 levels")
        try:
            return function()
        finally:
            depth -= 1

    def primary():
        token = take()
        if isinstance(token, float):
            return finite(token)
        if token != "(":
            _bad("Expected a number or parenthesis")
        value = nested(additive)
        if take() != ")":
            _bad("Missing closing parenthesis")
        return value

    def power():
        left = primary()
        if peek() != "**":
            return left
        take()
        right = nested(unary)
        try:
            return finite(math.pow(left, right))
        except (OverflowError, ValueError):
            _bad("Calculation must produce a finite real number")

    def unary():
        if peek() in ("+", "-"):
            sign = -1 if take() == "-" else 1
            return finite(sign * nested(unary))
        return power()

    def multiplicative():
        value = unary()
        while peek() in ("*", "/", "%"):
            operator = take()
            right = unary()
            if operator in ("/", "%") and right == 0:
                _bad("Division by zero is not allowed")
            value = finite(value * right if operator == "*" else value / right if operator == "/" else math.fmod(value, right))
        return value

    def additive():
        value = multiplicative()
        while peek() in ("+", "-"):
            operator = take()
            right = multiplicative()
            value = finite(value + right if operator == "+" else value - right)
        return value

    result = additive()
    if position != len(tokens):
        _bad("Unexpected token in expression")
    return result


CORPUS = [
    {"id": "agent-loop", "title": "Agent 基本循环", "text": "Agent 接收用户输入，由 LLM 根据工具 Schema 决定调用工具或直接回复。将工具结果加入上下文，继续循环，直到最终答案或达到最大轮次。"},
    {"id": "session-memory", "title": "Session 与 Memory", "text": "同一用户的不同窗口使用不同 session_id。每个 session 独立保存消息、摘要与待办；追问前读取该 session 的历史与工具状态。"},
    {"id": "context-compression", "title": "Context 上下文压缩", "text": "长对话压缩旧轮次为摘要，保留最近完整工具调用链和用户原话。摘要提供背景，待办等结构化数据以当前 session 状态为准。"},
    {"id": "weekly-report", "title": "周报模板", "text": "周报可以包含：本周完成、关键成果、遇到的问题、下周计划。先收集事实，再生成周报，避免编造进度。"},
]
WEATHER = [
    ("上海", "shanghai", "小雨", 24),
    ("北京", "beijing", "晴", 27),
    ("杭州", "hangzhou", "多云", 26),
    ("深圳", "shenzhen", "阵雨", 29),
    ("广州", "guangzhou", "多云", 30),
]


def _schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _text(maximum: int, *, nullable: bool = False) -> dict:
    return {"type": ["string", "null"] if nullable else "string", "minLength": 1, "maxLength": maximum}


def _calculator(args: dict, _context: dict) -> dict:
    return {"expression": args["expression"], "result": calculate(args["expression"])}


def _search(args: dict, _context: dict) -> dict:
    query = args["query"]
    terms = re.findall(r"[a-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002ffff]", query.lower())
    scored = [(sum(term in f'{item["title"]} {item["text"]}'.lower() for term in terms), item) for item in CORPUS]
    scored.sort(key=lambda row: row[0], reverse=True)
    return {"mock": True, "source": "local-demo-corpus", "query": query,
            "results": [dict(item) for score, item in scored if score > 0][:3]}


def _todo(args: dict, context: dict) -> dict:
    context["signal"].throw_if_aborted()
    session = context.get("session")
    if not isinstance(session, dict):
        raise ToolError("SESSION_REQUIRED", "Todo requires a session")
    if not isinstance(session.get("todos"), list):
        raise ToolError("INVALID_SESSION", "Session todos must be an array")
    action, item_id, title = args["action"], args["id"], args["text"]
    if action == "add":
        normalized = re.sub(r"[\x00-\x1f\x7f]", " ", title).strip() if title is not None else ""
        if item_id is not None or not normalized:
            _bad("Add requires nonempty text and id=null")
        if len(session["todos"]) >= 20:
            raise ToolError("TODO_LIMIT", "A session may contain at most 20 todos")
        item = {"id": str(uuid4()), "text": normalized, "done": False,
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
        if len(dumps(dumps([*session["todos"], item]))) > 6500:
            raise ToolError("TODO_MEMORY_LIMIT", "Todo memory is full; shorten the new text or remove existing todos")
        session["todos"].append(item)
        return {"action": action, "todo": dict(item)}
    if action == "list":
        if item_id is not None or title is not None:
            _bad("List requires id=null and text=null")
        return {"action": action, "todos": [dict(item) for item in session["todos"]]}
    if item_id is None or title is not None:
        _bad("Complete/remove requires id and text=null")
    index = next((index for index, item in enumerate(session["todos"]) if item["id"] == item_id), -1)
    if index < 0:
        raise ToolError("TODO_NOT_FOUND", "Todo was not found in the current session")
    if action == "complete":
        session["todos"][index]["done"] = True
    item = session["todos"].pop(index) if action == "remove" else session["todos"][index]
    return {"action": action, "todo": dict(item)}


def _weather(args: dict, _context: dict) -> dict:
    normalized = args["city"].strip().lower().removesuffix("市")
    for city, alias, condition, temperature in WEATHER:
        if normalized in (city, alias):
            return {"mock": True, "source": "fixed-demo-data", "city": city, "condition": condition,
                    "temperatureC": temperature, "notice": "固定模拟数据，并非实时天气"}
    raise ToolError("CITY_NOT_SUPPORTED", "Mock weather supports 上海、北京、杭州、深圳、广州 only")


def create_tools(**registry_options) -> ToolRegistry:
    registry = ToolRegistry(**registry_options)
    registry.register(name="calculator", description="安全计算数学表达式，支持 + - * / % **、括号、小数、科学计数法；返回 IEEE-754 浮点数结果。",
                      parameters=_schema({"expression": _text(500)}), execute=_calculator)
    registry.register(name="search", description="MOCK 搜索：仅搜索本地演示语料（Agent 循环、Session、上下文压缩、周报），不访问互联网，也不提供实时事实。",
                      parameters=_schema({"query": _text(200)}), execute=_search)
    registry.register(name="todo", description="管理当前 session 的独立待办，最多 20 项、每项 200 字，另有总记忆容量限制。add 需要 text、id=null；list 两者均为 null；complete/remove 需要 id、text=null。id 从 list/add 返回。",
                      parameters=_schema({"action": {"type": "string", "enum": ["add", "list", "complete", "remove"]},
                                          "id": _text(64, nullable=True), "text": _text(200, nullable=True)}), execute=_todo)
    registry.register(name="weather", description="MOCK 天气：返回上海、北京、杭州、深圳、广州的固定演示天气，不代表真实天气；请在回复中明确标注模拟数据。",
                      parameters=_schema({"city": _text(80)}), execute=_weather)
    return registry
