from copy import deepcopy
import json
import unittest

from agent.errors import AgentError
from agent.parser import parse_completion


def response(message, finish="stop"):
    return {"choices": [{"message": {"role": "assistant", **message}, "finish_reason": finish}]}


def call(call_id="call_1", name="calculator", args='{"expression":"2+3"}'):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


class ParserTests(unittest.TestCase):
    def assert_code(self, raw, code="LLM_PROTOCOL"):
        with self.assertRaises(AgentError) as caught:
            parse_completion(raw)
        self.assertEqual(caught.exception.code, code)

    def test_plain_text_answer_is_normalized(self):
        parsed = parse_completion(response({"content": "  你好  "}))
        self.assertEqual(parsed["type"], "final")
        self.assertEqual(parsed["answer"], "你好")
        self.assertEqual(parsed["calls"], [])
        self.assertEqual(parsed["assistantMessage"], {"role": "assistant", "content": "你好"})

    def test_json_envelope_and_fence(self):
        content = '{"decision_summary":"完成计算。","answer":"5"}'
        for value in (content, "```json\n" + content + "\n```", "```\n" + content + "\n```"):
            with self.subTest(value=value):
                parsed = parse_completion(response({"content": value}))
                self.assertEqual(parsed["answer"], "5")
                self.assertEqual(parsed["decisionSummary"], "完成计算。")

    def test_malformed_attempted_envelope_fails(self):
        for content in ('{"answer":', '```json\n{"answer":\n```', '{"answer":null}', '{"answer":" "}', '{"answer":"hi","decision_summary":5}', '{"answer":"hi","decision_summary":null}'):
            with self.subTest(content=content):
                self.assert_code(response({"content": content}))

    def test_ordinary_json_data_stays_user_visible(self):
        text = '{"name":"Ada","language":"Python"}'
        self.assertEqual(parse_completion(response({"content": text}))["answer"], text)

    def test_native_calls_have_canonical_pairing_and_null_content(self):
        parsed = parse_completion(response({"content": None, "tool_calls": [call()]}, "tool_calls"))
        self.assertEqual(parsed["type"], "tools")
        self.assertEqual(parsed["calls"], [{"id": "call_1", "name": "calculator", "args": {"expression": "2+3"}}])
        self.assertEqual(parsed["assistantMessage"], {"role": "assistant", "content": None, "tool_calls": [call()]})
        self.assertIn("calculator", parsed["decisionSummary"])
        self.assertEqual(parsed["answer"], "")

    def test_multiple_calls_keep_order_and_exact_ids(self):
        raw = response({"tool_calls": [call("first"), call("second", "weather", '{"city":"上海"}')]}, "tool_calls")
        parsed = parse_completion(raw)
        self.assertEqual([item["id"] for item in parsed["calls"]], ["first", "second"])
        self.assertEqual(parsed["calls"][1]["args"], {"city": "上海"})

    def test_bad_arguments_are_recoverable_and_raw_json_is_preserved(self):
        broken = '{"expression":'
        parsed = parse_completion(response({"tool_calls": [call("a", args=broken), call("b")]}))
        self.assertIsNone(parsed["calls"][0]["args"])
        self.assertIn("invalid JSON", parsed["calls"][0]["argumentError"])
        self.assertEqual(parsed["assistantMessage"]["tool_calls"][0]["function"]["arguments"], broken)
        self.assertEqual(parsed["calls"][1]["args"], {"expression": "2+3"})

    def test_non_objects_and_nonfinite_arguments_are_recoverable(self):
        for arguments in ("null", "[]", "42", "true", '"text"', '{"value":NaN}', '{"value":Infinity}', '{"value":1e999}'):
            with self.subTest(arguments=arguments):
                parsed = parse_completion(response({"tool_calls": [call(args=arguments)]}))
                self.assertIsNone(parsed["calls"][0]["args"])
                self.assertIn("argumentError", parsed["calls"][0])

    def test_hidden_reasoning_is_never_read_or_retained(self):
        for content in ({"content": "答案"}, {"content": None, "tool_calls": [call()]}):
            raw = response({**content, "reasoning_content": "PRIVATE_REASONING", "reasoning": "PRIVATE_REASONING"})
            before = deepcopy(raw)
            self.assertNotIn("PRIVATE_REASONING", json.dumps(parse_completion(raw)))
            self.assertEqual(raw, before)

    def test_refusal_is_final_and_truncation_and_filtering_are_errors(self):
        parsed = parse_completion(response({"content": None, "refusal": "无法协助此请求。"}))
        self.assertEqual(parsed["type"], "final")
        self.assertEqual(parsed["answer"], "无法协助此请求。")
        self.assert_code(response({"content": "partial"}, "length"), "LLM_TRUNCATED")
        self.assert_code(response({"content": None}, "content_filter"), "LLM_CONTENT_FILTER")

    def test_bad_response_structures_fail(self):
        invalid = [None, {}, {"choices": []}, {"choices": [None]}, response({"role": "user", "content": "hi"}), response({"content": None}), response({"content": []}), response({"content": ""}), response({"content": "hi", "function_call": {"name": "legacy"}}), response({"content": "hi", "tool_calls": {}}), response({"content": "hi"}, "tool_calls")]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.assert_code(raw)

    def test_native_call_structural_limits(self):
        invalid_calls = [[{**call(), "id": ""}], [{**call(), "id": "bad id"}], [call("x" * 129)], [call("same"), call("same")], [{**call(), "type": "custom"}], [call(name="bad name")], [call(name="x" * 65)], [call(args={})], [call(args="x" * 65537)], [None]]
        for calls in invalid_calls:
            with self.subTest(count=len(calls)):
                self.assert_code(response({"content": None, "tool_calls": calls}))

    def test_summary_content_and_call_count_budgets(self):
        self.assert_code(response({"content": "x" * 128001}))
        self.assert_code(response({"tool_calls": [call(f"call_{index}") for index in range(33)]}))
        parsed = parse_completion(response({"content": json.dumps({"answer": "ok", "decision_summary": "x" * 400})}))
        self.assertEqual(len(parsed["decisionSummary"]), 300)


if __name__ == "__main__":
    unittest.main()
