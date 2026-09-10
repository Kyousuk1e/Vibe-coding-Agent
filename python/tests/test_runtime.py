import asyncio
import copy
import tempfile
import unittest

from agent.context import ContextManager
from agent.errors import AgentError
from agent.jsonutil import dumps, loads
from agent.runtime import AgentRuntime
from agent.store import SessionStore
from agent.tools import create_tools
from tests.helpers import FakeClient, call, final, tool, tool_calls


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SessionStore(self.temp.name)
        self.session = await self.store.create("A")

    def runtime(self, replies, **overrides):
        self.client = FakeClient(replies)
        options = dict(store=self.store, client=self.client, registry=create_tools(), context=ContextManager())
        options.update(overrides)
        return AgentRuntime(**options)

    async def run_turn(self, runtime, text="测试", request_id=None, session=None):
        return await runtime.run(user_id="A", session_id=(session or self.session)["id"], input=text, request_id=request_id)

    async def test_direct_followup_receives_both_sides_of_history(self):
        runtime = self.runtime([final("记住了，小林"), final("你叫小林")])
        await self.run_turn(runtime, "我叫小林")
        result = await self.run_turn(runtime, "我叫什么？")
        self.assertEqual(result["status"], "ok")
        messages = self.client.requests[1]["messages"]
        self.assertIn({"role": "user", "content": "我叫小林"}, messages)
        self.assertIn({"role": "assistant", "content": "记住了，小林"}, messages)
        self.assertFalse(any(e["event"] == "tool.start" for e in result["trace"]))

    async def test_two_tool_results_keep_native_call_ids(self):
        runtime = self.runtime([tool_calls(call("weather", {"city": "上海"}, "weather_1"),
                                                call("calculator", {"expression": "3*7"}, "calc_1")), final("完成")])
        result = await self.run_turn(runtime)
        self.assertEqual(result["steps"], 2)
        messages = self.client.requests[1]["messages"]
        observed = [m for m in messages if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in observed], ["weather_1", "calc_1"])
        self.assertTrue(all(loads(m["content"])["ok"] for m in observed))
        self.assertEqual(len([e for e in result["trace"] if e["event"] == "tool.end"]), 2)

    async def test_todo_followup_uses_persisted_id_and_current_state(self):
        runtime = self.runtime([tool("todo", {"action": "add", "id": None, "text": "提交周报"}), final()])
        await self.run_turn(runtime, "添加提交周报")
        saved = await self.store.get("A", self.session["id"])
        todo_id = saved["todos"][0]["id"]
        runtime.client = FakeClient([tool("todo", {"action": "complete", "id": todo_id, "text": None}), final()])
        await self.run_turn(runtime, "完成刚才的待办")
        memory = runtime.client.requests[0]["messages"][1]["content"]
        self.assertIn(todo_id, memory)
        saved = await self.store.get("A", self.session["id"])
        self.assertEqual(len(saved["todos"]), 1)
        self.assertEqual(saved["todos"][0]["id"], todo_id)
        self.assertTrue(saved["todos"][0]["done"])

    async def test_repair_bad_json_unknown_tool_and_bad_schema(self):
        runtime = self.runtime([tool("calculator", "not json", "a"), tool("unknown", {}, "b"),
                                tool("calculator", {"expression": 123}, "c"),
                                tool("calculator", {"expression": "123*2"}, "d"), final("246")])
        result = await self.run_turn(runtime)
        self.assertEqual(result["status"], "ok")
        outputs = [loads(m["content"]) for m in self.client.requests[-1]["messages"] if m["role"] == "tool"]
        self.assertEqual([o["ok"] for o in outputs], [False, False, False, True])

    async def test_max_steps_stops_exactly_and_preserves_successful_todo(self):
        runtime = self.runtime([tool("todo", {"action": "add", "id": None, "text": "已执行"})], max_steps=1)
        result = await self.run_turn(runtime, request_id="limit")
        self.assertEqual(result["status"], "max_steps")
        self.assertEqual(result["steps"], 1)
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(len((await self.store.get("A", self.session["id"]))["todos"]), 1)
        replay = await self.run_turn(runtime, request_id="limit")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["status"], "max_steps")
        self.assertEqual(len(self.client.requests), 1)

    async def test_failure_rolls_back_local_todo_and_replays_saved_error(self):
        runtime = self.runtime([tool("todo", {"action": "add", "id": None, "text": "草稿"}),
                                AgentError("LLM_TIMEOUT", "secret raw provider body")])
        result = await self.run_turn(runtime, request_id="failed")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret", dumps(result))
        saved = await self.store.get("A", self.session["id"])
        self.assertEqual(saved["todos"], [])
        self.assertEqual([m["role"] for m in saved["messages"]], ["user", "assistant"])
        self.assertTrue((await self.run_turn(runtime, request_id="failed"))["replayed"])
        self.assertEqual(len(self.client.requests), 2)

    async def test_concurrent_same_request_replays_without_another_execution(self):
        runtime = self.runtime([tool("todo", {"action": "add", "id": None, "text": "唯一"}), final()])
        first, second = await asyncio.gather(self.run_turn(runtime, request_id="same"),
                                             self.run_turn(runtime, request_id="same"))
        self.assertEqual([first["replayed"], second["replayed"]], [False, True])
        self.assertEqual(len(self.client.requests), 2)
        self.assertEqual(len((await self.store.get("A", self.session["id"]))["todos"]), 1)
        with self.assertRaises(AgentError) as caught:
            await self.run_turn(runtime, "不同输入", request_id="same")
        self.assertEqual(caught.exception.code, "REQUEST_CONFLICT")

    async def test_new_request_id_is_a_new_operation(self):
        add = tool("todo", {"action": "add", "id": None, "text": "同样文字"})
        runtime = self.runtime([add, final(), add, final()])
        await self.run_turn(runtime, request_id="one")
        await self.run_turn(runtime, request_id="two")
        self.assertEqual(len((await self.store.get("A", self.session["id"]))["todos"]), 2)

    async def test_numeric_cache_eviction_restore_and_evicted_request(self):
        runtime = self.runtime([final()] * 22)
        for number in range(100, 120):
            await self.run_turn(runtime, request_id=str(number))
        await self.run_turn(runtime, "最新", request_id="1")
        saved = await self.store.get("A", self.session["id"])
        self.assertEqual(len(saved["completedRequests"]), 20)
        self.assertNotIn("100", saved["completedRequests"])
        restored_client = FakeClient([])
        restored = self.runtime([], store=SessionStore(self.temp.name), client=restored_client)
        self.assertTrue((await self.run_turn(restored, "最新", request_id="1"))["replayed"])
        self.assertEqual(restored_client.requests, [])
        self.assertFalse((await self.run_turn(runtime, request_id="100"))["replayed"])

    async def test_different_sessions_really_overlap_without_cross_contamination(self):
        second = await self.store.create("A")
        arrived = 0
        both_started = asyncio.Event()
        snapshots = []

        class OverlappingClient:
            async def complete(inner, *, messages, tools):
                nonlocal arrived
                snapshots.append(copy.deepcopy(messages))
                current_input = [m["content"] for m in messages if m["role"] == "user"][-1]
                if not any(m["role"] == "tool" for m in messages):
                    arrived += 1
                    if arrived == 2:
                        both_started.set()
                    await asyncio.wait_for(both_started.wait(), 2)
                    return tool("todo", {"action": "add", "id": None, "text": current_input})
                return final()

        runtime = self.runtime([], client=OverlappingClient())
        a, b = await asyncio.gather(self.run_turn(runtime, "带伞"), self.run_turn(runtime, "提交周报", session=second))
        self.assertEqual([a["status"], b["status"]], ["ok", "ok"])
        self.assertEqual(arrived, 2)
        for session, own, other in [(self.session, "带伞", "提交周报"), (second, "提交周报", "带伞")]:
            saved = await self.store.get("A", session["id"])
            self.assertEqual([t["text"] for t in saved["todos"]], [own])
            self.assertNotIn(other, dumps(saved["messages"]))

    async def test_trace_failure_does_not_undo_saved_state(self):
        class BrokenTrace:
            async def write(self, *args):
                raise OSError("disk error")
        runtime = self.runtime([final()], trace_writer=BrokenTrace())
        result = await self.run_turn(runtime)
        self.assertEqual(result["status"], "ok")
        self.assertIn("traceWarning", result)
        self.assertEqual(len((await self.store.get("A", self.session["id"]))["messages"]), 2)

    async def test_invalid_input_does_not_call_model(self):
        runtime = self.runtime([])
        for text, request_id in [("", None), ("x" * 4001, None), ("ok", "__proto__"), ("ok", "")]:
            with self.assertRaises(AgentError):
                await self.run_turn(runtime, text, request_id=request_id)
        self.assertEqual(self.client.requests, [])

    async def test_context_limit_stops_before_model_call(self):
        runtime = self.runtime([], context=ContextManager(max_context_chars=1000))
        result = await self.run_turn(runtime)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.client.requests, [])

    async def test_too_many_calls_do_not_execute_partial_batch(self):
        runtime = self.runtime([tool_calls(*[call("todo", {"action": "add", "id": None, "text": "不应添加"}, f"c{i}") for i in range(5)])])
        result = await self.run_turn(runtime)
        self.assertEqual(result["status"], "error")
        self.assertEqual((await self.store.get("A", self.session["id"]))["todos"], [])

    async def test_compression_then_tool_followup_preserves_id(self):
        runtime = self.runtime([tool("todo", {"action": "add", "id": None, "text": "提交周报"}), final()] +
                                [final("答复" * 350)] * 9,
                                context=ContextManager(max_context_chars=12000, summary_chars=1000, recent_turns=2))
        await self.run_turn(runtime, "我的项目代号是北斗，请记录提交周报待办")
        compacted = False
        for number in range(9):
            result = await self.run_turn(runtime, f"第{number}轮：" + "历史资料" * 400)
            self.assertEqual(result["status"], "ok")
            compacted |= any(e.get("compactedTurns", 0) > 0 for e in result["trace"])
        saved = await self.store.get("A", self.session["id"])
        todo_id = saved["todos"][0]["id"]
        self.assertTrue(compacted)
        runtime.client = FakeClient([tool("todo", {"action": "complete", "id": todo_id, "text": None}), final()])
        result = await self.run_turn(runtime, "完成之前的提交周报")
        self.assertEqual(result["status"], "ok")
        sent = runtime.client.requests[0]
        self.assertLessEqual(len(dumps(sent)), 12000)
        self.assertIn(todo_id, sent["messages"][1]["content"])
        self.assertIn("北斗", sent["messages"][1]["content"])
        reloaded = await SessionStore(self.temp.name).get("A", self.session["id"])
        self.assertEqual(len(reloaded["todos"]), 1)
        self.assertEqual(reloaded["todos"][0]["id"], todo_id)
        self.assertTrue(reloaded["todos"][0]["done"])


if __name__ == "__main__":
    unittest.main()
