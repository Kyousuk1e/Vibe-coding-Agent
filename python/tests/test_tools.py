"""Offline tool/schema tests exercise the real registry and real handlers."""

import asyncio
import copy
import math
import threading
import time
import unittest

from agent.jsonutil import dumps
from agent.registry import AbortSignal, ToolError, ToolRegistry
from agent.tools import calculate, create_tools


def empty_schema():
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def definition(parameters=None, execute=None):
    return {"name": "demo", "description": "Demo", "parameters": empty_schema() if parameters is None else parameters,
            "execute": (lambda _args, _context: "ok") if execute is None else execute}


class SchemaAndCalculatorTests(unittest.TestCase):
    def test_registry_publishes_isolated_strict_schemas(self):
        original = empty_schema()
        registry = ToolRegistry().register(**definition(original))
        original["type"] = "string"
        external = registry.schemas()
        self.assertTrue(external[0]["function"]["strict"])
        external[0]["function"]["parameters"]["type"] = "string"
        self.assertEqual(registry.schemas()[0]["function"]["parameters"]["type"], "object")
        with self.assertRaisesRegex(TypeError, "Duplicate"):
            registry.register(**definition())

    def test_registry_rejects_unsupported_schemas(self):
        cases = [
            {**empty_schema(), "additionalProperties": True},
            {**empty_schema(), "pattern": "x"},
            {**empty_schema(), "properties": {"a": {"type": "string"}}},
            {**empty_schema(), "properties": {"a": {"type": "integer", "minimum": math.nan}}, "required": ["a"]},
            {**empty_schema(), "properties": {"a": {"type": "array"}}, "required": ["a"]},
            {**empty_schema(), "properties": {"a": {"type": ["string", "number"]}}, "required": ["a"]},
            {**empty_schema(), "properties": {"a": {"type": "integer", "enum": [True]}}, "required": ["a"]},
            {**empty_schema(), "properties": {"a": {"type": "number", "enum": [1, 1.0]}}, "required": ["a"]},
            {**empty_schema(), "properties": {"a": {"type": "string", "minLength": True}}, "required": ["a"]},
            {"type": "string"},
        ]
        cyclic = empty_schema()
        cyclic["properties"]["a"] = cyclic
        cyclic["required"] = ["a"]
        cases.append(cyclic)
        for parameters in cases:
            with self.subTest(parameters=str(parameters)[:150]), self.assertRaisesRegex(TypeError, "schema"):
                ToolRegistry().register(**definition(parameters))

    def test_registry_validates_configuration(self):
        for options in ({"timeout_ms": 0}, {"timeout_ms": True}, {"timeout_ms": 300001}, {"max_result_chars": 99}):
            with self.subTest(options=options), self.assertRaises(TypeError):
                ToolRegistry(**options)
        for changes in ({"name": "x.y"}, {"description": " "}, {"execute": None}):
            with self.subTest(changes=changes), self.assertRaises(TypeError):
                ToolRegistry().register(**{**definition(), **changes})

    def test_calculator_precedence_powers_decimals_and_scientific_numbers(self):
        for expression, expected in (
            ("2+3*4", 14), ("(2+3)*4", 20), ("2**3**2", 512), ("-2**2", -4), ("(-2)**2", 4),
            ("2**-2", .25), (".5 + 1.5e2", 150.5), ("7%4", 3), ("-7%4", -3), ("7%-4", 3),
            ("1 - -2", 3), (" 1. + 2 ", 3), ("-0", 0), ("1e2/4", 25), ("0**0", 1),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(calculate(expression), expected)
        self.assertEqual(math.copysign(1, calculate("-0")), 1)

    def test_calculator_rejects_injection_overflow_and_invalid_syntax(self):
        for expression in (
            "", " ", "process.exit()", "__import__('os').getcwd()", "1;globalThis.x=1", "True", "False",
            "2(3)", "1e", "0x10", "2//3", "(2+3", "2+3)", "1/0", "1%0", "1e999", "10**999", "(-1)**.5",
            "(" * 40 + "1" + ")" * 40, "-" * 40 + "1", "1" * 501, "+".join(["1"] * 101), True, 123,
        ):
            with self.subTest(expression=expression), self.assertRaises(ToolError):
                calculate(expression)


class ToolExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_recursive_argument_validation_precedes_execution(self):
        calls = []
        parameters = {
            "type": "object", "additionalProperties": False, "required": ["rows", "note"],
            "properties": {
                "rows": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "integer", "minimum": 0, "maximum": 5}},
                "note": {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 3}, {"type": "null"}]},
            },
        }

        def handler(args, _context):
            calls.append(args)
            return args

        registry = ToolRegistry().register(**definition(parameters, handler))
        for args in (
            {}, {"rows": [], "note": None}, {"rows": [1, 2, 3], "note": None}, {"rows": [1.2], "note": None},
            {"rows": [math.inf], "note": None}, {"rows": [6], "note": None}, {"rows": [1], "note": ""},
            {"rows": [True], "note": None}, {"rows": [1], "note": "1234"},
            {"rows": [1], "note": None, "extra": 1}, {"rows": [1]},
        ):
            with self.subTest(args=args):
                self.assertEqual((await registry.execute("demo", args))["error"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(calls, [])
        args = {"rows": [1, 2], "note": None}
        self.assertEqual(await registry.execute("demo", args), {"ok": True, "data": args})
        self.assertEqual((await registry.execute("missing", {}))["error"]["code"], "UNKNOWN_TOOL")

    async def test_nullable_boolean_enum_validation_is_strict(self):
        parameters = {**empty_schema(), "properties": {"flag": {"type": ["boolean", "null"], "enum": [True, None]}}, "required": ["flag"]}
        registry = ToolRegistry().register(**definition(parameters))
        for value in (False, 1, "true"):
            self.assertFalse((await registry.execute("demo", {"flag": value}))["ok"])
        for value in (True, None):
            self.assertTrue((await registry.execute("demo", {"flag": value}))["ok"])

    async def test_todos_are_session_local_and_support_id_followups(self):
        registry = create_tools()
        a, b = {"todos": []}, {"todos": []}
        added = await registry.execute("todo", {"action": "add", "id": None, "text": "  带伞  "}, {"session": a})
        self.assertTrue(added["ok"])
        self.assertEqual(added["data"]["todo"]["text"], "带伞")
        item_id = added["data"]["todo"]["id"]
        failed = await registry.execute("todo", {"action": "complete", "id": item_id, "text": None}, {"session": b})
        self.assertEqual(failed["error"]["code"], "TODO_NOT_FOUND")
        self.assertEqual(b["todos"], [])
        listed = await registry.execute("todo", {"action": "list", "id": None, "text": None}, {"session": a})
        self.assertEqual(len(listed["data"]["todos"]), 1)
        completed = await registry.execute("todo", {"action": "complete", "id": item_id, "text": None}, {"session": a})
        self.assertTrue(completed["data"]["todo"]["done"])
        self.assertTrue(a["todos"][0]["done"])
        await registry.execute("todo", {"action": "remove", "id": item_id, "text": None}, {"session": a})
        self.assertEqual(a["todos"], [])

    async def test_todo_invalid_arguments_do_not_mutate_session(self):
        registry, session = create_tools(), {"todos": []}
        for args in (
            {"action": "add", "id": None, "text": " "}, {"action": "add", "id": None, "text": "\x00\x01"},
            {"action": "add", "id": "x", "text": "x"}, {"action": "add", "id": None, "text": "x" * 201},
            {"action": "list", "id": "x", "text": None}, {"action": "complete", "id": None, "text": None},
            {"action": "remove", "id": "x", "text": "extra"}, {"action": "unexpected", "id": None, "text": None},
        ):
            with self.subTest(args=args):
                self.assertFalse((await registry.execute("todo", args, {"session": session}))["ok"])
        self.assertEqual(session["todos"], [])
        self.assertEqual((await registry.execute("todo", {"action": "list", "id": None, "text": None}))["error"]["code"], "SESSION_REQUIRED")

    async def test_todo_capacity_and_unicode_character_limit(self):
        registry, session = create_tools(), {"todos": []}
        for _ in range(20):
            added = await registry.execute("todo", {"action": "add", "id": None, "text": "待" * 200}, {"session": session})
            self.assertTrue(added["ok"], added)
        self.assertEqual(len(session["todos"]), 20)
        failed = await registry.execute("todo", {"action": "add", "id": None, "text": "x"}, {"session": session})
        self.assertEqual(failed["error"]["code"], "TODO_LIMIT")
        self.assertEqual(len(session["todos"]), 20)
        listed = await registry.execute("todo", {"action": "list", "id": None, "text": None}, {"session": session})
        self.assertTrue(listed["ok"])
        self.assertLessEqual(len(dumps(dumps(session["todos"]))), 6500)

    async def test_escaped_todo_memory_remains_bounded_and_removable(self):
        registry, session = create_tools(), {"todos": []}
        failure = None
        for _ in range(20):
            previous = copy.deepcopy(session)
            result = await registry.execute("todo", {"action": "add", "id": None, "text": '"' * 200}, {"session": session})
            if not result["ok"]:
                failure = result
                self.assertEqual(session, previous)
                break
        self.assertEqual(failure["error"]["code"], "TODO_MEMORY_LIMIT")
        self.assertLessEqual(len(dumps(dumps(session["todos"]))), 6500)
        listed = await registry.execute("todo", {"action": "list", "id": None, "text": None}, {"session": session})
        self.assertTrue(listed["ok"])
        count = len(session["todos"])
        removed = await registry.execute("todo", {"action": "remove", "id": session["todos"][0]["id"], "text": None}, {"session": session})
        self.assertTrue(removed["ok"])
        self.assertEqual(len(session["todos"]), count - 1)

    async def test_mock_tools_keep_provenance_and_relevance(self):
        registry = create_tools()
        self.assertEqual([item["function"]["name"] for item in registry.schemas()], ["calculator", "search", "todo", "weather"])
        search = (await registry.execute("search", {"query": "session memory"}))["data"]
        self.assertTrue(search["mock"])
        self.assertEqual(search["source"], "local-demo-corpus")
        self.assertEqual(search["results"][0]["id"], "session-memory")
        self.assertLessEqual(len(search["results"]), 3)
        self.assertEqual((await registry.execute("weather", {"city": "上海市"}))["data"]["city"], "上海")
        self.assertTrue((await registry.execute("weather", {"city": "Shanghai"}))["data"]["mock"])
        self.assertEqual((await registry.execute("weather", {"city": "Atlantis"}))["error"]["code"], "CITY_NOT_SUPPORTED")

    async def test_async_timeout_sets_signal(self):
        observed = []

        async def handler(_args, context):
            observed.append(context["signal"])
            await asyncio.Event().wait()

        registry = ToolRegistry(timeout_ms=15).register(**definition(execute=handler))
        result = await registry.execute("demo", {})
        self.assertEqual(result["error"]["code"], "TOOL_TIMEOUT")
        self.assertTrue(observed[0].aborted)

    async def test_caller_cancellation_is_reported(self):
        async def handler(_args, _context):
            await asyncio.Event().wait()

        registry = ToolRegistry().register(**definition(execute=handler))
        signal = AbortSignal()
        signal.abort()
        self.assertEqual((await registry.execute("demo", {}, {"signal": signal}))["error"]["code"], "ABORTED")
        signal = asyncio.Event()
        pending = asyncio.create_task(registry.execute("demo", {}, {"signal": signal}))
        await asyncio.sleep(0)
        signal.set()
        self.assertEqual((await pending)["error"]["code"], "ABORTED")

    async def test_unexpected_error_hides_secrets_and_output_is_bounded(self):
        def unsafe(_args, _context):
            raise RuntimeError("secret-api-key")

        registry = ToolRegistry().register(**definition(execute=unsafe))
        self.assertEqual(await registry.execute("demo", {}), {"ok": False, "error": {"code": "TOOL_ERROR", "message": "Tool execution failed"}})
        huge = ToolRegistry(max_result_chars=100).register(**definition(execute=lambda _args, _context: "x" * 101))
        self.assertEqual((await huge.execute("demo", {}))["error"]["code"], "OUTPUT_TOO_LARGE")
        nonfinite = ToolRegistry().register(**definition(execute=lambda _args, _context: math.nan))
        self.assertEqual((await nonfinite.execute("demo", {}))["error"]["code"], "TOOL_ERROR")

    async def test_expected_tool_errors_are_bounded_and_sanitized(self):
        def handler(_args, _context):
            raise ToolError("A" * 100, "safe\nmessage\x00" + "x" * 400)

        result = await ToolRegistry().register(**definition(execute=handler)).execute("demo", {})
        self.assertEqual(len(result["error"]["code"]), 50)
        self.assertEqual(len(result["error"]["message"]), 300)
        self.assertNotIn("\n", result["error"]["message"])

    async def test_failed_or_oversized_tools_cannot_commit_todos(self):
        def failing(_args, context):
            context["session"]["todos"].append({"text": "failed"})
            raise RuntimeError("failure")

        def oversized(_args, context):
            context["session"]["todos"].append({"text": "oversized"})
            return "x" * 101

        def invalid(_args, context):
            context["session"]["todos"] = None
            return "invalid"

        for handler in (failing, oversized, invalid):
            with self.subTest(handler=handler.__name__):
                registry = ToolRegistry(max_result_chars=100).register(**definition(execute=handler))
                session = {"todos": []}
                self.assertFalse((await registry.execute("demo", {}, {"session": session}))["ok"])
                self.assertEqual(session["todos"], [])

    async def test_success_commits_detached_todos_only(self):
        captured = []

        def handler(_args, context):
            captured.append(context["session"])
            context["session"]["todos"].append({"text": "committed", "done": False})
            context["session"]["userId"] = "another-user"
            return context["session"]["todos"]

        registry = ToolRegistry().register(**definition(execute=handler))
        session = {"todos": [], "userId": "original-user"}
        result = await registry.execute("demo", {}, {"session": session})
        self.assertTrue(result["ok"])
        captured[0]["todos"][0]["text"] = "late mutation"
        result["data"][0]["text"] = "result mutation"
        self.assertEqual(session["todos"][0]["text"], "committed")
        self.assertEqual(session["userId"], "original-user")

    async def test_sync_timeout_returns_promptly_and_late_writes_are_isolated(self):
        started = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        captured = []

        def handler(_args, context):
            captured.append(context["signal"])
            context["session"]["todos"].append({"text": "early"})
            started.set()
            release.wait(2)
            context["session"]["todos"].append({"text": "late"})
            completed.set()
            return "late result"

        registry = ToolRegistry(timeout_ms=30).register(**definition(execute=handler))
        session = {"todos": []}
        start = time.monotonic()
        try:
            result = await registry.execute("demo", {}, {"session": session})
            self.assertEqual(result["error"]["code"], "TOOL_TIMEOUT")
            self.assertLess(time.monotonic() - start, 1)
            self.assertTrue(started.is_set())
            self.assertTrue(captured[0].aborted)
            self.assertEqual(session["todos"], [])
        finally:
            release.set()
        self.assertTrue(await asyncio.to_thread(completed.wait, 1))
        await asyncio.sleep(0)
        self.assertEqual(session["todos"], [])

    async def test_async_late_continuation_cannot_commit(self):
        released = asyncio.Event()
        completed = asyncio.Event()

        async def handler(_args, context):
            context["session"]["todos"].append({"text": "early"})
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await released.wait()
                context["session"]["todos"].append({"text": "late"})
                completed.set()
                return "late result"

        session = {"todos": []}
        registry = ToolRegistry(timeout_ms=15).register(**definition(execute=handler))
        result = await registry.execute("demo", {}, {"session": session})
        self.assertEqual(result["error"]["code"], "TOOL_TIMEOUT")
        released.set()
        await asyncio.wait_for(completed.wait(), 1)
        self.assertEqual(session["todos"], [])


if __name__ == "__main__":
    unittest.main()
