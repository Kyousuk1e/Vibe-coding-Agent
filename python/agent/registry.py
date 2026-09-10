"""Strict tool schemas and bounded, isolated tool execution (standard library only)."""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import math
import re
import threading
from collections.abc import Callable
from typing import Any

from .errors import AgentError
from .jsonutil import dumps


class ToolError(AgentError):
    """An expected, safe-to-report tool failure."""


_TYPE_KEYS = {
    "object": {"properties", "required", "additionalProperties"},
    "array": {"items", "minItems", "maxItems"},
    "string": {"minLength", "maxLength"},
    "number": {"minimum", "maximum"},
    "integer": {"minimum", "maximum"},
    "boolean": set(),
    "null": set(),
}
_MAX_SAFE_INTEGER = 2**53 - 1


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _matches_type(value: Any, kind: Any) -> bool:
    if isinstance(kind, list):
        return any(_matches_type(value, item) for item in kind)
    if kind == "null":
        return value is None
    if kind == "object":
        return type(value) is dict
    if kind == "array":
        return type(value) is list
    if kind == "integer":
        return _finite_number(value) and abs(value) <= _MAX_SAFE_INTEGER and int(value) == value
    if kind == "number":
        return _finite_number(value)
    if kind == "boolean":
        return type(value) is bool
    return kind == "string" and type(value) is str


def _same_value(left: Any, right: Any) -> bool:
    # Python considers True == 1; JSON Schema and the original registry do not.
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    return left == right


def _invalid_schema(message: str) -> None:
    raise TypeError(f"Unsupported tool schema: {message}")


def check_schema(schema: Any, depth: int = 0) -> None:
    """Validate the deliberately small, strict subset published to the LLM."""
    if depth > 12 or type(schema) is not dict:
        _invalid_schema("schema must be a plain object with depth <= 12")
    if "description" in schema and not isinstance(schema["description"], str):
        _invalid_schema("description must be a string")
    if "anyOf" in schema:
        if set(schema) - {"anyOf", "description"}:
            _invalid_schema("anyOf cannot have sibling constraints")
        choices = schema["anyOf"]
        if (not isinstance(choices, list) or len(choices) != 2
                or sum(isinstance(item, dict) and item.get("type") == "null" for item in choices) != 1):
            _invalid_schema("anyOf supports exactly one schema plus null")
        for child in choices:
            check_schema(child, depth + 1)
        return
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if (any(not isinstance(kind, str) or kind not in _TYPE_KEYS for kind in kinds)
            or len(set(kinds)) != len(kinds) or not kinds):
        _invalid_schema("unknown or repeated type")
    if isinstance(schema.get("type"), list) and (len(kinds) != 2 or "null" not in kinds):
        _invalid_schema("type arrays support one type plus null")
    kind = next((item for item in kinds if item != "null"), "null")
    if set(schema) - ({"type", "description", "enum", "anyOf"} | _TYPE_KEYS[kind]):
        _invalid_schema(f"unsupported keyword for {kind}")
    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, list) or not 1 <= len(choices) <= 100:
            _invalid_schema("enum must contain 1-100 values")
        for index, value in enumerate(choices):
            if value is not None and type(value) not in (str, bool, int, float):
                _invalid_schema("enum supports primitive values")
            if not _matches_type(value, schema["type"]):
                _invalid_schema("enum value must match type")
            if any(_same_value(value, old) for old in choices[:index]):
                _invalid_schema("enum values must be unique")
    if kind == "object":
        properties, required = schema.get("properties"), schema.get("required")
        if type(properties) is not dict or schema.get("additionalProperties") is not False or not isinstance(required, list):
            _invalid_schema("objects need properties, required, and additionalProperties: false")
        if (len(properties) > 50 or len(required) != len(properties)
                or any(not isinstance(key, str) for key in properties)
                or any(not isinstance(key, str) or key not in properties for key in required)
                or len(set(required)) != len(properties)):
            _invalid_schema("all properties must be required; use null for optional values")
        for child in properties.values():
            check_schema(child, depth + 1)
    if kind == "array":
        check_schema(schema.get("items"), depth + 1)
    for minimum, maximum in (("minLength", "maxLength"), ("minItems", "maxItems"), ("minimum", "maximum")):
        for key in (minimum, maximum):
            if key not in schema:
                continue
            value = schema[key]
            if not _finite_number(value):
                _invalid_schema(f"{key} must be finite")
            if key not in ("minimum", "maximum") and not (_matches_type(value, "integer") and value >= 0):
                _invalid_schema(f"{key} must be a nonnegative integer")
        if minimum in schema and maximum in schema and schema[minimum] > schema[maximum]:
            _invalid_schema(f"{minimum} exceeds {maximum}")


def validation_error(schema: dict, value: Any, path: str = "$", depth: int = 0) -> str | None:
    if depth > 12:
        return f"{path}: nesting is too deep"
    if "anyOf" in schema:
        if any(validation_error(child, value, path, depth + 1) is None for child in schema["anyOf"]):
            return None
        return f"{path}: value does not match the nullable schema"
    if not _matches_type(value, schema["type"]):
        return f"{path}: invalid type"
    if "enum" in schema and not any(_same_value(value, choice) for choice in schema["enum"]):
        return f"{path}: value is not in enum"
    if value is None:
        return None
    kind = schema["type"]
    if isinstance(kind, list):
        kind = next(item for item in kind if item != "null")
    if kind == "object":
        if any(key not in value for key in schema["required"]):
            return f"{path}: required property is missing"
        if any(not isinstance(key, str) or key not in schema["properties"] for key in value):
            return f"{path}: additional properties are not allowed"
        for key, child in schema["properties"].items():
            error = validation_error(child, value[key], f"{path}.{key}", depth + 1)
            if error:
                return error
    elif kind == "array":
        if len(value) > 10000:
            return f"{path}: array exceeds validation budget"
        if "minItems" in schema and len(value) < schema["minItems"]:
            return f"{path}: too few items"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return f"{path}: too many items"
        for index, item in enumerate(value):
            error = validation_error(schema["items"], item, f"{path}[{index}]", depth + 1)
            if error:
                return error
    elif kind == "string":
        if len(value) > 100000:
            return f"{path}: string exceeds validation budget"
        if "minLength" in schema and len(value) < schema["minLength"]:
            return f"{path}: string is too short"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return f"{path}: string is too long"
    elif kind in ("number", "integer"):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{path}: number is below minimum"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{path}: number exceeds maximum"
    return None


def _failure(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


class AbortSignal:
    """Cooperative cancellation signal shared with a detached tool handler."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    def abort(self) -> None:
        self._event.set()

    def throw_if_aborted(self) -> None:
        if self.aborted:
            raise ToolError("ABORTED", "Tool call was cancelled")


def _is_aborted(signal: Any) -> bool:
    if signal is None:
        return False
    if hasattr(signal, "is_set"):
        return bool(signal.is_set())
    return bool(getattr(signal, "aborted", False))


async def _run_sync(function: Callable, *args: Any) -> Any:
    """A daemon worker avoids blocking the loop or waiting for a timed-out thread at exit.

    Python cannot forcibly terminate a thread; the handler sees isolated session
    data and must honor its signal before producing any external side effects.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def complete(value: Any, error: BaseException | None) -> None:
        if future.done():
            return
        if error is None:
            future.set_result(value)
        else:
            future.set_exception(error)

    def worker() -> None:
        try:
            value, error = function(*args), None
        except BaseException as caught:
            value, error = None, caught
        try:
            loop.call_soon_threadsafe(complete, value, error)
        except RuntimeError:
            pass  # The call timed out and its event loop has already closed.

    threading.Thread(target=worker, name="agent-tool", daemon=True).start()
    return await future


class ToolRegistry:
    def __init__(self, *, timeout_ms: int = 5000, max_result_chars: int = 16000) -> None:
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 300000:
            raise TypeError("Invalid tool timeout")
        if type(max_result_chars) is not int or max_result_chars < 100:
            raise TypeError("Invalid result limit")
        self.timeout_ms = timeout_ms
        self.max_result_chars = max_result_chars
        self._tools: dict[str, dict] = {}

    def register(self, *, name: str, description: str, parameters: dict, execute: Callable) -> ToolRegistry:
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name) is None:
            raise TypeError("Invalid tool name")
        if name in self._tools:
            raise TypeError(f"Duplicate tool: {name}")
        if not isinstance(description, str) or not description.strip() or len(description) > 2000:
            raise TypeError("Invalid tool description")
        if not callable(execute):
            raise TypeError("Tool execute must be a function")
        check_schema(parameters)
        if parameters.get("type") != "object":
            _invalid_schema("tool parameters must be an object")
        if len(dumps(parameters)) > 16000:
            _invalid_schema("schema is too large")
        self._tools[name] = {"name": name, "description": description,
                             "parameters": copy.deepcopy(parameters), "execute": execute}
        return self

    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": item["name"], "description": item["description"],
            "parameters": copy.deepcopy(item["parameters"]), "strict": True,
        }} for item in self._tools.values()]

    async def execute(self, name: str, args: Any, context: dict | None = None) -> dict:
        context = {} if context is None else context
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            return _failure("UNKNOWN_TOOL", "Unknown tool")
        invalid = validation_error(tool["parameters"], args)
        if invalid:
            return _failure("INVALID_ARGUMENTS", invalid[:300])
        if _is_aborted(context.get("signal")):
            return _failure("ABORTED", "Tool call was cancelled")
        signal = AbortSignal()
        execution = cancellation = None
        try:
            original_session = context.get("session")
            snapshot = copy.deepcopy(original_session)
            isolated_args = copy.deepcopy(args)
            isolated_context = {**context, "session": snapshot, "signal": signal}

            async def invoke() -> Any:
                signal.throw_if_aborted()
                handler = tool["execute"]
                if inspect.iscoroutinefunction(handler):
                    return await handler(isolated_args, isolated_context)
                result = await _run_sync(handler, isolated_args, isolated_context)
                return await result if inspect.isawaitable(result) else result

            async def cancelled() -> None:
                while not _is_aborted(context["signal"]):
                    await asyncio.sleep(0.005)

            execution = asyncio.create_task(invoke())
            pending = {execution}
            if context.get("signal") is not None:
                cancellation = asyncio.create_task(cancelled())
                pending.add(cancellation)
            done, _ = await asyncio.wait(pending, timeout=self.timeout_ms / 1000,
                                         return_when=asyncio.FIRST_COMPLETED)
            if cancellation in done or _is_aborted(context.get("signal")):
                signal.abort()
                return _failure("ABORTED", "Tool call was cancelled")
            if execution not in done:
                signal.abort()
                return _failure("TOOL_TIMEOUT", "Tool execution timed out")
            data = execution.result()
            serialized = dumps(data)
            if len(serialized) > self.max_result_chars:
                return _failure("OUTPUT_TOO_LARGE", "Tool result exceeds the output limit")
            result = json.loads(serialized)
            signal.throw_if_aborted()
            if isinstance(snapshot, dict) and ("todos" in snapshot or (isinstance(original_session, dict) and "todos" in original_session)):
                if not isinstance(snapshot.get("todos"), list):
                    return _failure("INVALID_SESSION", "Session todos must be an array")
                original_session["todos"] = copy.deepcopy(snapshot["todos"])
            return {"ok": True, "data": result}
        except ToolError as error:
            return _failure(str(error.code)[:50], re.sub(r"[\x00-\x1f]", " ", str(error))[:300])
        except asyncio.CancelledError:
            signal.abort()
            raise
        except Exception:
            return _failure("TOOL_ERROR", "Tool execution failed")
        finally:
            for task in (execution, cancellation):
                if task is not None:
                    if not task.done():
                        task.cancel()
                    task.add_done_callback(lambda item: None if item.cancelled() else item.exception())
