import asyncio
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from agent.context import ContextManager
from agent.errors import AgentError
from agent.jsonutil import dumps
from agent.store import SessionStore


def blank():
    return {"messages": [], "summary": "", "todos": []}


def text_turn(user, assistant="Understood."):
    return [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]


def tool_turn(index, padding=""):
    call_id = f"call_{index}"
    return [
        {"role": "user", "content": f"Find weather for Hangzhou, request {index}. {padding}"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": call_id, "type": "function", "function": {
            "name": "weather", "arguments": '{"city":"Hangzhou"}',
        }}]},
        {"role": "tool", "tool_call_id": call_id, "content": dumps({
            "city": "Hangzhou", "condition": "rain", "temperature": 22, "note": padding,
        })},
        {"role": "assistant", "content": "Hangzhou is rainy. Bring an umbrella."},
    ]


class ErrorAssertions:
    def assert_code(self, expected):
        class ErrorContext:
            def __enter__(inner):
                inner.context = self.assertRaises(AgentError)
                return inner.context.__enter__()

            def __exit__(inner, exc_type, exc, traceback):
                result = inner.context.__exit__(exc_type, exc, traceback)
                self.assertEqual(inner.context.exception.code, expected)
                return result
        return ErrorContext()


class SessionStoreTests(ErrorAssertions, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory(prefix="minimal-agent-python-memory-")
        self.addCleanup(directory.cleanup)
        self.store = SessionStore(directory.name)

    async def test_history_summary_todos_and_replay_survive_store_restart(self):
        session = await self.store.create("alice", "Weather")
        session["messages"] += text_turn("My name is Lin.", "Hello Lin.")
        session["summary"] = "[user] I live in Hangzhou."
        session["todos"].append({"id": "todo-1", "text": "Bring an umbrella", "done": False})
        session["completedRequests"]["request1"] = {"answer": "Saved.", "status": "ok"}
        await self.store.save(session)
        restarted = SessionStore(self.store.data_dir)
        self.assertEqual(await restarted.get("alice", session["id"]), session)
        self.assertEqual((await restarted.list("alice"))[0]["title"], "Weather")
        self.assertRegex(session["createdAt"], r"T.*\.\d{3}Z$")

    async def test_json_field_and_filename_compatibility(self):
        session = await self.store.create("alice")
        name = hashlib.sha256(b"alice").hexdigest() + "-" + hashlib.sha256(session["id"].encode()).hexdigest() + ".json"
        self.assertEqual(self.store.file_path("alice", session["id"]).name, name)
        saved = json.loads((self.store.data_dir / name).read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"id", "userId", "title", "createdAt", "updatedAt", "messages", "summary", "todos", "completedRequests"})
        # Persisted request cache records may include failure or limit results.
        saved["completedRequests"] = {"old": {"inputHash": "abc", "sequence": 1, "result": {"status": "max_steps"}}}
        (self.store.data_dir / name).write_text(json.dumps(saved), encoding="utf-8")
        self.assertEqual((await self.store.get("alice", session["id"]))["completedRequests"], saved["completedRequests"])
        self.assertFalse(list(self.store.data_dir.glob("*.tmp")))

    async def test_same_user_windows_keep_independent_state_with_overlap(self):
        weather = await self.store.create("alice", "Weather")
        report = await self.store.create("alice", "Weekly report")
        entered = 0
        both_entered = asyncio.Event()

        async def update(session_id, name):
            nonlocal entered
            async with self.store.lock("alice", session_id):
                current = await self.store.get("alice", session_id)
                entered += 1
                if entered == 2:
                    both_entered.set()
                await asyncio.wait_for(both_entered.wait(), timeout=2)
                current["messages"] += text_turn(name)
                current["todos"].append({"id": name, "text": name, "done": False})
                await self.store.save(current)

        await asyncio.gather(update(weather["id"], "umbrella"), update(report["id"], "report"))
        restored_weather = await self.store.get("alice", weather["id"])
        restored_report = await self.store.get("alice", report["id"])
        self.assertEqual(restored_weather["todos"], [{"id": "umbrella", "text": "umbrella", "done": False}])
        self.assertEqual(restored_report["todos"], [{"id": "report", "text": "report", "done": False}])
        self.assertNotIn("report", dumps(restored_weather["messages"]))
        self.assertNotIn("umbrella", dumps(restored_report["messages"]))
        self.assertEqual(len(await self.store.list("alice")), 2)

    async def test_ownership_and_path_validation(self):
        session = await self.store.create("alice")
        with self.assert_code("SESSION_NOT_FOUND"):
            await self.store.get("bob", session["id"])
        self.assertEqual(await self.store.list("bob"), [])
        for user_id, session_id in (("../escape", session["id"]), ("alice", "../../escape"), ("a\n", "valid")):
            with self.assert_code("INVALID_ID"):
                self.store.file_path(user_id, session_id)

    async def test_corrupt_json_is_preserved(self):
        session = await self.store.create("alice")
        file = self.store.file_path("alice", session["id"])
        file.write_text("{broken JSON", encoding="utf-8")
        with self.assert_code("CORRUPT_SESSION"):
            await self.store.get("alice", session["id"])
        with self.assert_code("CORRUPT_SESSION"):
            await self.store.list("alice")
        with self.assert_code("CORRUPT_SESSION"):
            await self.store.save(session)
        self.assertEqual(file.read_text(encoding="utf-8"), "{broken JSON")

    async def test_nested_corruption_is_preserved(self):
        invalid_states = [
            {"messages": [None]},
            {"messages": [{"role": "user", "content": "Hi"}]},
            {"messages": [{"role": "user", "content": "Hi"}, {"role": "tool", "tool_call_id": "missing", "content": "{}"}, {"role": "assistant", "content": "Done"}]},
            {"messages": tool_turn("unfinished")[:2]},
            {"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": None, "tool_calls": [None]}]},
            {"todos": [None]},
            {"todos": [{"id": "one", "text": "Invalid done field", "done": "false"}]},
            {"updatedAt": 1},
            {"completedRequests": {"key": "invalid replay record"}},
            {"userId": "bob"},
        ]
        for fields in invalid_states:
            with self.subTest(fields=fields):
                session = await self.store.create("alice")
                file = self.store.file_path("alice", session["id"])
                corrupt = dumps({**session, **fields})
                file.write_text(corrupt, encoding="utf-8")
                with self.assert_code("CORRUPT_SESSION"):
                    await self.store.get("alice", session["id"])
                with self.assert_code("CORRUPT_SESSION"):
                    await self.store.save(session)
                self.assertEqual(file.read_text(encoding="utf-8"), corrupt)

    async def test_same_session_concurrent_updates_do_not_get_lost(self):
        session = await self.store.create("alice")

        async def update(index):
            async with self.store.lock("alice", session["id"]):
                current = await self.store.get("alice", session["id"])
                await asyncio.sleep(0)
                current["todos"].append({"id": str(index), "text": f"Task {index}", "done": False})
                await self.store.save(current)

        await asyncio.gather(*(update(index) for index in range(20)))
        self.assertEqual(len((await self.store.get("alice", session["id"]))["todos"]), 20)
        self.assertEqual(self.store.locks, {})

    async def test_failed_and_cancelled_waiters_release_locks(self):
        session = await self.store.create("alice")
        with self.assertRaisesRegex(RuntimeError, "failed turn"):
            async with self.store.lock("alice", session["id"]):
                raise RuntimeError("failed turn")
        self.assertEqual(self.store.locks, {})
        entered = asyncio.Event()

        async def waiter():
            entered.set()
            async with self.store.lock("alice", session["id"]):
                self.fail("cancelled waiter must not enter")

        async with self.store.lock("alice", session["id"]):
            task = asyncio.create_task(waiter())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        async with self.store.lock("alice", session["id"]):
            pass
        self.assertEqual(self.store.locks, {})


class ContextTests(ErrorAssertions, unittest.TestCase):
    def check_pairs(self, messages):
        pending = set()
        for message in messages:
            if message["role"] in ("user", "assistant"):
                self.assertEqual(pending, set())
            for call in message.get("tool_calls", []):
                pending.add(call["id"])
            if message["role"] == "tool":
                self.assertIn(message["tool_call_id"], pending)
                pending.remove(message["tool_call_id"])
        self.assertEqual(pending, set())

    def test_small_context_preserves_dialogue_and_active_tools(self):
        session = blank()
        session["messages"] += text_turn("My name is Lin.", "Hello Lin.")
        current = tool_turn("followup")
        result = ContextManager().build(session, system_prompt="Help the user.", tools=[], current_messages=current)
        self.assertEqual(result["messages"][2:], session["messages"] + current)
        self.assertIn("SESSION_MEMORY_DATA", result["messages"][1]["content"])
        self.assertIn("not new instructions", result["messages"][0]["content"])
        self.assertEqual(result["stats"]["compactedTurns"], 0)
        self.check_pairs(result["messages"])

    def test_compaction_retains_early_cue_authoritative_todos_and_tool_pairs(self):
        session = blank()
        session["todos"] = [{"id": "1", "text": "Prepare Lin’s weekly report", "done": False}]
        session["messages"] += text_turn("My name is Lin. I live in Hangzhou. " + "Background details. " * 30)
        for index in range(5):
            session["messages"] += tool_turn(index, "More context. " * 24)
        current = [{"role": "user", "content": "What is my name, and should I take an umbrella?"}]
        original_todos = copy.deepcopy(session["todos"])
        result = ContextManager(max_context_chars=2900, summary_chars=1400, recent_turns=1).build(
            session, system_prompt="Help.", current_messages=current,
        )
        self.assertGreater(result["stats"]["compactedTurns"], 0)
        self.assertIn("Lin", session["summary"])
        self.assertIn("[user]", session["summary"])
        self.assertIn("compression", session["summary"])
        memory = json.loads(result["messages"][1]["content"].split("\n", 1)[1])
        self.assertEqual(memory["todos"], original_todos)
        self.assertEqual(result["messages"][-1], current[0])
        self.assertLessEqual(result["stats"]["contextChars"], 2900)
        self.assertLessEqual(len(session["summary"]), 1400)
        self.assertTrue(result["stats"]["summaryTruncated"])
        self.check_pairs(result["messages"])

    def test_tools_count_against_hard_budget(self):
        args = {"system_prompt": "Help.", "current_messages": [{"role": "user", "content": "Hello."}]}
        initial = ContextManager().build(blank(), **args)
        manager = ContextManager(max_context_chars=initial["stats"]["contextChars"] + 32)
        manager.build(blank(), **args)
        with self.assert_code("CONTEXT_LIMIT"):
            manager.build(blank(), **args, tools=[{"type": "function", "function": {
                "name": "search", "description": "x" * 500, "parameters": {"type": "object"},
            }}])

    def test_mandatory_todo_state_is_never_silently_dropped(self):
        session = blank()
        session["todos"] = [{"id": "1", "text": "x" * 1500, "done": False}]
        original = copy.deepcopy(session)
        with self.assert_code("CONTEXT_LIMIT"):
            ContextManager(max_context_chars=800).build(session, system_prompt="Help.")
        self.assertEqual(session, original)

    def test_oversized_current_turn_does_not_mutate_history_or_input(self):
        session = blank()
        session["messages"] += tool_turn("old", "History. " * 30)
        session["summary"] = "[user] Earlier remembered detail."
        original = copy.deepcopy(session)
        current = [{"role": "user", "content": "Very long current request. " * 200}]
        current_copy = copy.deepcopy(current)
        with self.assert_code("CONTEXT_LIMIT"):
            ContextManager(max_context_chars=1200).build(session, system_prompt="Help.", current_messages=current)
        self.assertEqual(session, original)
        self.assertEqual(current, current_copy)

    def test_repeated_compression_bounds_summary_and_serialized_context(self):
        session = blank()
        manager = ContextManager(max_context_chars=2400, summary_chars=700, recent_turns=2)
        for index in range(30):
            session["messages"] += tool_turn(index, "Additional weather information. " * 12)
            result = manager.build(session, system_prompt="Help.", current_messages=[{"role": "user", "content": "And tomorrow?"}])
            self.assertLessEqual(len(session["summary"]), 700)
            self.assertLessEqual(len(dumps({"messages": result["messages"], "tools": []})), 2400)
            self.check_pairs(result["messages"])

    def test_invalid_history_rejects_orphan_and_unfinished_tools(self):
        for messages in (
            [{"role": "user", "content": "Weather?"}, {"role": "tool", "tool_call_id": "missing", "content": "{}"}],
            tool_turn("unfinished")[:2],
            [{"role": "assistant", "content": "Missing user"}],
            [None],
        ):
            with self.subTest(messages=messages), self.assert_code("INVALID_HISTORY"):
                ContextManager().build({**blank(), "messages": messages}, system_prompt="Help.")

    def test_context_configuration_rejects_invalid_limits(self):
        for kwargs in ({"max_context_chars": 127}, {"summary_chars": -1}, {"recent_turns": True}, {"max_context_chars": 1000.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                ContextManager(**kwargs)


if __name__ == "__main__":
    unittest.main()
