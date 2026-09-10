import asyncio
import tempfile
import unittest
from types import SimpleNamespace

from agent.context import ContextManager
from agent.jsonutil import dumps, loads
from agent.runtime import AgentRuntime
from agent.server import start_server
from agent.store import SessionStore
from agent.tools import create_tools
from tests.helpers import FakeClient, final


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SessionStore(self.temp.name)
        self.client = FakeClient([final("你好")])
        registry = create_tools()
        runtime = AgentRuntime(store=self.store, client=self.client, registry=registry, context=ContextManager())
        self.server = await start_server(SimpleNamespace(store=self.store, runtime=runtime, registry=registry), 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.temp.cleanup()

    async def request(self, path, *, method="GET", body=None, user="A", extra="", raw=None):
        encoded = raw if raw is not None else (b"" if body is None else dumps(body).encode())
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        headers = (f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nX-User-Id: {user}\r\n"
                   f"Content-Type: application/json\r\nContent-Length: {len(encoded)}\r\n{extra}\r\n")
        writer.write(headers.encode() + encoded)
        await writer.drain()
        data = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, value = data.split(b"\r\n\r\n", 1)
        return int(head.split(b" ", 2)[1]), loads(value)

    async def test_health_and_tool_schema(self):
        self.assertEqual(await self.request("/health", user=""), (200, {"ok": True}))
        status, result = await self.request("/tools")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["tools"]), 4)

    async def test_create_chat_replay_and_private_cache(self):
        status, session = await self.request("/sessions", method="POST", body={"title": "测试"})
        self.assertEqual(status, 201)
        path = f"/sessions/{session['id']}/messages"
        status, result = await self.request(path, method="POST", body={"input": "你好", "requestId": "http1"})
        self.assertEqual((status, result["status"]), (200, "ok"))
        _, replay = await self.request(path, method="POST", body={"input": "你好", "requestId": "http1"})
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.client.requests), 1)
        status, _ = await self.request(path, method="POST", body={"input": "不同", "requestId": "http1"})
        self.assertEqual(status, 409)
        status, restored = await self.request(f"/sessions/{session['id']}")
        self.assertNotIn("completedRequests", restored)
        self.assertEqual(len(restored["messages"]), 2)

    async def test_cross_user_session_not_found(self):
        _, session = await self.request("/sessions", method="POST", body={})
        status, _ = await self.request(f"/sessions/{session['id']}", user="B")
        self.assertEqual(status, 404)

    async def test_invalid_title_body_and_user(self):
        for body in [{"title": 42}, {"title": "x" * 201}, []]:
            self.assertEqual((await self.request("/sessions", method="POST", body=body))[0], 400)
        self.assertEqual((await self.request("/sessions", user="../A"))[0], 400)
        self.assertEqual((await self.request("/sessions", method="POST", raw=b"bad json"))[0], 400)

    async def test_origin_denied_and_no_cors(self):
        status, _ = await self.request("/sessions", method="POST", body={}, extra="Origin: https://example.test\r\n")
        self.assertEqual(status, 403)

    async def test_oversized_body_rejected_without_model(self):
        status, _ = await self.request("/sessions", method="POST", raw=b"x" * 32769)
        self.assertEqual(status, 400)
        self.assertEqual(self.client.requests, [])

    async def test_non_loopback_binding_rejected(self):
        with self.assertRaises(Exception):
            await start_server(SimpleNamespace(), 0, "0.0.0.0")

    async def test_explicit_null_request_id_is_rejected_before_execution(self):
        _, session = await self.request("/sessions", method="POST", body={})
        status, result = await self.request(f"/sessions/{session['id']}/messages", method="POST",
                                            body={"input": "你好", "requestId": None})
        self.assertEqual(status, 400)
        self.assertEqual(result["error"]["code"], "BAD_INPUT")
        self.assertEqual(self.client.requests, [])
