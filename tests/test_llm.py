import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import traceback
import unittest
from unittest.mock import patch

from agent.config import Config, read_config
from agent.errors import AgentError
from agent.llm import ChatClient


COMPLETION = {"choices": [{"message": {"role": "assistant", "content": "你好"}, "finish_reason": "stop"}], "usage": {"total_tokens": 12}}
MESSAGES = [{"role": "user", "content": "你好"}]
TOOL = {"type": "function", "function": {"name": "calculator", "description": "算术", "strict": True, "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"], "additionalProperties": False}}}
SECRET = "unit-test-placeholder-key"


def response(value=COMPLETION, status=200, headers=None):
    return status, headers or {}, json.dumps(value, ensure_ascii=False).encode()


def config(**options):
    return Config(api_key=SECRET, max_retries=0, **options)


class ConfigTests(unittest.TestCase):
    def test_qwen_defaults_and_secret_hidden_from_repr(self):
        result = read_config({"DASHSCOPE_API_KEY": SECRET}, env_file=None)
        self.assertEqual(result.provider, "qwen")
        self.assertEqual(result.model, "qwen-plus")
        self.assertEqual(result.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(result.port, 8787)
        self.assertEqual(result.max_steps, 8)
        self.assertTrue(Path(result.data_dir).is_absolute())
        self.assertNotIn(SECRET, repr(result))

    def test_openai_defaults(self):
        result = read_config({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": SECRET}, env_file=None)
        self.assertEqual(result.model, "gpt-4.1-mini")
        self.assertEqual(result.base_url, "https://api.openai.com/v1")
        self.assertEqual(Config(provider="openai").model, "gpt-4.1-mini")

    def test_dotenv_quotes_comments_exports_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory, "fixture.env")
            filename.write_text("\ufeff# example\nexport DASHSCOPE_API_KEY='file-placeholder'\nLLM_MODEL=\"qwen-plus\" # comment\nPORT=8123\nDATA_DIR='work#literal'\n", encoding="utf-8")
            environment = {"DASHSCOPE_API_KEY": SECRET, "PORT": "8124"}
            before = dict(environment)
            result = read_config(environment, env_file=filename)
            self.assertEqual(result.api_key, SECRET)
            self.assertEqual(result.port, 8124)
            self.assertEqual(result.model, "qwen-plus")
            self.assertTrue(result.data_dir.endswith("work#literal"))
            self.assertEqual(environment, before)
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": SECRET, "PORT": "8125"}, clear=True):
                result = read_config(env_file=filename)
                self.assertEqual(result.port, 8125)
                self.assertNotIn("LLM_MODEL", os.environ)

    def test_explicit_empty_environment_key_does_not_fall_back_to_file(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory, "fixture.env")
            filename.write_text("DASHSCOPE_API_KEY=file-placeholder", encoding="utf-8")
            with self.assertRaises(AgentError) as caught:
                read_config({"DASHSCOPE_API_KEY": ""}, env_file=filename)
            self.assertEqual(caught.exception.code, "CONFIG")

    def test_invalid_dotenv_diagnostics_do_not_disclose_contents(self):
        for content in (f"{SECRET}", f"DASHSCOPE_API_KEY=\"{SECRET}", f"DASHSCOPE_API_KEY='{SECRET}' invalid"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                filename = Path(directory, "fixture.env")
                filename.write_text(content, encoding="utf-8")
                with self.assertRaises(AgentError) as caught:
                    read_config({}, env_file=filename)
                self.assertNotIn(SECRET, str(caught.exception))

    def test_missing_key_unknown_provider_and_numeric_bounds(self):
        invalid = [{}, {"DASHSCOPE_API_KEY": "x\ny"}, {"DASHSCOPE_API_KEY": SECRET, "LLM_PROVIDER": "typo"}]
        for name, value in [("PORT", "0"), ("MAX_STEPS", "31"), ("MAX_CONTEXT_CHARS", "9999"), ("LLM_TIMEOUT_MS", "99"), ("LLM_MAX_RETRIES", "4"), ("MAX_OUTPUT_TOKENS", "NaN"), ("MAX_STEPS", "1.5")]:
            invalid.append({"DASHSCOPE_API_KEY": SECRET, name: value})
        for env in invalid:
            with self.subTest(keys=list(env)), self.assertRaises(AgentError):
                read_config(env, env_file=None)
        valid = read_config({"DASHSCOPE_API_KEY": SECRET, "LLM_TIMEOUT_MS": "1e2", "LLM_MAX_RETRIES": "0"}, env_file=None)
        self.assertEqual(valid.timeout_ms, 100)
        self.assertEqual(valid.max_retries, 0)


class LLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_qwen_request_payload_and_full_completion(self):
        captured = []
        async def transport(url, **kwargs):
            captured.append((url, kwargs))
            return response()
        self.assertEqual(await ChatClient(config(), transport=transport).complete(messages=MESSAGES, tools=[TOOL]), COMPLETION)
        url, request = captured[0]
        self.assertEqual(url, "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], f"Bearer {SECRET}")
        self.assertEqual(request["timeout"], 30)
        body = json.loads(request["body"])
        self.assertEqual(body["model"], "qwen-plus")
        self.assertEqual(body["max_tokens"], 1200)
        self.assertFalse(body["enable_thinking"])
        self.assertFalse(body["parallel_tool_calls"])
        self.assertFalse(body["stream"])
        self.assertEqual(body["tool_choice"], "auto")
        self.assertEqual(body["messages"], MESSAGES)
        self.assertNotIn("strict", body["tools"][0]["function"])
        self.assertTrue(TOOL["function"]["strict"], "Caller/local schema must not change")

    async def test_openai_request_uses_provider_specific_fields(self):
        def transport(url, **kwargs):
            self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
            body = json.loads(kwargs["body"])
            self.assertEqual(body["model"], "gpt-4.1-mini")
            self.assertEqual(body["max_completion_tokens"], 500)
            self.assertNotIn("max_tokens", body)
            self.assertNotIn("enable_thinking", body)
            self.assertTrue(body["tools"][0]["function"]["strict"])
            return response()
        await ChatClient(config(provider="openai", max_output_tokens=500), transport=transport).complete(messages=MESSAGES, tools=[TOOL])

    async def test_no_tools_omits_tool_fields(self):
        async def transport(_url, **kwargs):
            body = json.loads(kwargs["body"])
            for key in ("tools", "tool_choice", "parallel_tool_calls"):
                self.assertNotIn(key, body)
            return response()
        await ChatClient(config(), transport=transport).complete(messages=MESSAGES)

    async def test_retryable_http_statuses_recover_and_bound_retry_after(self):
        for status, retry_after, expected_delay in [(429, "0", 0), (500, "10000", 5), (503, "invalid", 0.25)]:
            with self.subTest(status=status):
                calls, delays = [], []
                async def transport(_url, **kwargs):
                    calls.append(kwargs)
                    return (status, {"Retry-After": retry_after}, SECRET.encode()) if len(calls) == 1 else response()
                client = ChatClient(replace(config(), max_retries=1), transport=transport, sleep=delays.append)
                self.assertEqual(await client.complete(messages=MESSAGES), COMPLETION)
                self.assertEqual(len(calls), 2)
                self.assertEqual(delays, [expected_delay])
                self.assertEqual(calls[0]["body"], calls[1]["body"])

    async def test_http_auth_and_bad_requests_are_not_retried_or_exposed(self):
        for status in (400, 401, 403, 404, 307):
            with self.subTest(status=status):
                calls = []
                async def transport(*args, **kwargs):
                    calls.append(True)
                    return status, {}, f"PRIVATE_PROVIDER_BODY {SECRET}".encode()
                with self.assertRaises(AgentError) as caught:
                    await ChatClient(replace(config(), max_retries=3), transport=transport).complete(messages=MESSAGES)
                self.assertEqual(caught.exception.code, "LLM_AUTH" if status in (401, 403) else "LLM_HTTP")
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertNotIn("PRIVATE_PROVIDER_BODY", str(caught.exception))
                self.assertEqual(len(calls), 1)

    async def test_network_failure_is_bounded_and_forged_errors_are_sanitized(self):
        calls, delays = [], []
        async def transport(*args, **kwargs):
            calls.append(True)
            raise AgentError("LLM_AUTH", SECRET)
        with self.assertRaises(AgentError) as caught:
            await ChatClient(replace(config(), max_retries=2), transport=transport, sleep=delays.append).complete(messages=MESSAGES)
        self.assertEqual(caught.exception.code, "LLM_NETWORK")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(SECRET, "".join(traceback.format_exception(caught.exception)))
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, [0.25, 0.5])

    async def test_attempt_timeout_then_recovery(self):
        calls, delays = [], []
        async def transport(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                await asyncio.sleep(10)
            return response()
        client = ChatClient(replace(config(timeout_ms=10), max_retries=1), transport=transport, sleep=delays.append)
        self.assertEqual(await client.complete(messages=MESSAGES), COMPLETION)
        self.assertEqual(len(calls), 2)
        self.assertEqual(delays, [0.25])

    async def test_late_thread_result_is_discarded_after_timeout(self):
        calls = []
        def transport(*args, **kwargs):
            calls.append(True)
            if len(calls) == 1:
                time.sleep(0.05)
                return response({"late": True})
            return response()
        client = ChatClient(config(timeout_ms=10), transport=transport)
        with self.assertRaises(AgentError) as caught:
            await client.complete(messages=MESSAGES)
        self.assertEqual(caught.exception.code, "LLM_TIMEOUT")
        await asyncio.sleep(0.07)
        self.assertEqual(await client.complete(messages=MESSAGES), COMPLETION)

    async def test_external_task_cancellation_is_not_retried(self):
        started, calls = asyncio.Event(), []
        async def transport(*args, **kwargs):
            calls.append(True)
            started.set()
            await asyncio.sleep(10)
        client = ChatClient(replace(config(), max_retries=3), transport=transport)
        task = asyncio.create_task(client.complete(messages=MESSAGES))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(calls), 1)

    async def test_invalid_or_oversized_responses_fail_without_retry(self):
        samples = [(200, {}, b"bad PRIVATE_DATA"), (200, {}, b"\xff"), (200, {}, b"{\"value\":NaN}"), (200, {}, b"{\"value\":1e999}"), (200, {}, b"[]"), (200, {}, b"x" * 1048577), (200, {"Content-Length": "1048577"}, b"{}"), (200, {}, "string instead of bytes"), {"choices": []}]
        for sample in samples:
            with self.subTest(kind=type(sample).__name__):
                calls = []
                async def transport(*args, **kwargs):
                    calls.append(True)
                    return sample
                with self.assertRaises(AgentError) as caught:
                    await ChatClient(replace(config(), max_retries=2), transport=transport).complete(messages=MESSAGES)
                self.assertEqual(caught.exception.code, "LLM_PROTOCOL")
                self.assertNotIn("PRIVATE_DATA", str(caught.exception))
                self.assertEqual(len(calls), 1)

    async def test_invalid_request_cannot_reach_transport(self):
        async def transport(*args, **kwargs):
            self.fail("invalid request reached transport")
        client = ChatClient(config(), transport=transport)
        for messages, tools in [([], []), ("hello", []), (MESSAGES, {}), ([{"bad": float("nan")}], [])]:
            with self.subTest(messages=messages), self.assertRaises(AgentError) as caught:
                await client.complete(messages=messages, tools=tools)
            self.assertEqual(caught.exception.code, "LLM_CONFIG")

    async def test_default_urllib_transport_real_loopback_http_and_no_redirect(self):
        # This is an actual local HTTP server with fixed responses, never a real LLM.
        captured = []
        async def handler(reader, writer):
            headers = await reader.readuntil(b"\r\n\r\n")
            lines = headers.decode().split("\r\n")
            fields = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
            body = await reader.readexactly(int(fields.get("Content-Length", "0")))
            captured.append((lines[0], fields, json.loads(body)))
            code = 307 if "/redirect/" in lines[0] else 401 if "/auth/" in lines[0] else 200
            data = SECRET.encode() if code != 200 else response()[2]
            extra = "Location: /must-not-follow\r\n" if code == 307 else ""
            writer.write(f"HTTP/1.1 {code} Test\r\nContent-Length: {len(data)}\r\n{extra}Connection: close\r\n\r\n".encode() + data)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        async with server:
            base = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            self.assertEqual(await ChatClient(config(base_url=base + "/v1")).complete(messages=MESSAGES), COMPLETION)
            for path, expected in [("auth", "LLM_AUTH"), ("redirect", "LLM_HTTP")]:
                with self.assertRaises(AgentError) as caught:
                    await ChatClient(config(base_url=base + "/" + path)).complete(messages=MESSAGES)
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(len(captured), 3)
        self.assertEqual(captured[0][0], "POST /v1/chat/completions HTTP/1.1")
        self.assertEqual(captured[0][2]["model"], "qwen-plus")

    def test_invalid_client_configuration_and_unsafe_urls(self):
        for options in ({"api_key": ""}, {"api_key": "x\ny"}, {"provider": "bad"}, {"timeout_ms": 0}, {"max_retries": 6}, {"max_output_tokens": 0}):
            with self.subTest(options=options), self.assertRaises(AgentError) as caught:
                ChatClient(replace(config(), **options))
            self.assertEqual(caught.exception.code, "LLM_CONFIG")
        for url in ("http://example.com/v1", f"https://user:{SECRET}@example.com", f"https://example.com?key={SECRET}", f"https://example.com#{SECRET}", "https://example.com:bad/v1", "file:///tmp/api"):
            with self.subTest(kind=url.split(":")[0]), self.assertRaises(AgentError) as caught:
                ChatClient(config(base_url=url))
            self.assertNotIn(SECRET, str(caught.exception))
        for url in ("http://localhost:8000/v1", "http://127.0.0.1:8000/v1", "http://[::1]:8000/v1"):
            self.assertTrue(ChatClient(config(base_url=url)).endpoint.endswith("/chat/completions"))


if __name__ == "__main__":
    unittest.main()
