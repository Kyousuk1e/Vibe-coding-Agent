import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCompletion } from '../src/parser.js';

const response = (message, finish_reason = 'stop') => ({ choices: [{ message: { role: 'assistant', ...message }, finish_reason }] });
const call = (id = 'call_1', name = 'calculator', args = '{"expression":"2+3"}') => ({ id, type: 'function', function: { name, arguments: args } });

test('plain conversation final is normalized', () => {
  const parsed = parseCompletion(response({ content: '  你好，今天想做什么？  ' }));
  assert.equal(parsed.type, 'final');
  assert.equal(parsed.answer, '你好，今天想做什么？');
  assert.deepEqual(parsed.calls, []);
  assert.deepEqual(parsed.assistantMessage, { role: 'assistant', content: parsed.answer });
});

test('final JSON envelope exposes an action summary and answer', () => {
  const parsed = parseCompletion(response({ content: JSON.stringify({ decision_summary: '使用已查询的天气。', answer: '上海是晴天（模拟数据）。' }) }));
  assert.equal(parsed.answer, '上海是晴天（模拟数据）。');
  assert.equal(parsed.decisionSummary, '使用已查询的天气。');
});

test('fenced JSON final is accepted, invalid attempted envelope is rejected', () => {
  assert.equal(parseCompletion(response({ content: '```json\n{"decision_summary":"完成计算。","answer":"5"}\n```' })).answer, '5');
  for (const content of ['```json\n{"answer":\n```', '{"answer":', '{"answer":null}', '{"decision_summary":5,"answer":"hello"}', '{"answer":"  "}']) assert.throws(() => parseCompletion(response({ content })), { code: 'LLM_PROTOCOL' });
});

test('ordinary JSON that is not an answer envelope remains user-visible text', () => {
  const content = '{"name":"Ada","language":"Python"}';
  assert.equal(parseCompletion(response({ content })).answer, content);
});

test('native tool calls with null content parse and preserve protocol pairing', () => {
  const parsed = parseCompletion(response({ content: null, tool_calls: [call()] }, 'tool_calls'));
  assert.equal(parsed.type, 'tools');
  assert.deepEqual(parsed.calls, [{ id: 'call_1', name: 'calculator', args: { expression: '2+3' } }]);
  assert.equal(parsed.answer, '');
  assert.match(parsed.decisionSummary, /calculator/);
  assert.deepEqual(parsed.assistantMessage, { role: 'assistant', content: null, tool_calls: [call()] });
});

test('multiple tool calls retain order and each native call id', () => {
  const parsed = parseCompletion(response({ tool_calls: [call('a'), call('b', 'weather', '{"city":"上海"}')] }, 'tool_calls'));
  assert.deepEqual(parsed.calls.map(item => item.id), ['a', 'b']);
  assert.deepEqual(parsed.calls[1].args, { city: '上海' });
});

test('invalid JSON arguments become a recoverable per-tool error preserving original text', () => {
  const args = '{"expression":';
  const parsed = parseCompletion(response({ tool_calls: [call('a', 'calculator', args), call('b')] }, 'tool_calls'));
  assert.equal(parsed.calls[0].args, undefined);
  assert.match(parsed.calls[0].argumentError, /invalid JSON/);
  assert.equal(parsed.assistantMessage.tool_calls[0].function.arguments, args);
  assert.deepEqual(parsed.calls[1].args, { expression: '2+3' });
});

test('non-object tool arguments are recoverable schema errors', () => {
  for (const args of ['null', '[]', 'true', '42', '"text"']) {
    const parsed = parseCompletion(response({ tool_calls: [call('a', 'calculator', args)] }));
    assert.match(parsed.calls[0].argumentError, /JSON object/);
    assert.equal(parsed.calls[0].args, undefined);
  }
});

test('hidden chain-of-thought fields never enter parser results or canonical messages', () => {
  for (const message of [
    { content: '答案', reasoning_content: 'SECRET_REASONS', reasoning: 'SECRET_REASONS' },
    { content: null, tool_calls: [call()], reasoning_content: 'SECRET_REASONS' }
  ]) assert.ok(!JSON.stringify(parseCompletion(response(message))).includes('SECRET_REASONS'));
});

test('refusal is returned as a final answer without assuming success', () => {
  const parsed = parseCompletion(response({ content: null, refusal: '无法协助此请求。' }));
  assert.equal(parsed.type, 'final');
  assert.equal(parsed.answer, '无法协助此请求。');
});

test('truncation and filtering are distinguishable from protocol errors', () => {
  assert.throws(() => parseCompletion(response({ content: 'partial' }, 'length')), { code: 'LLM_TRUNCATED' });
  assert.throws(() => parseCompletion(response({ content: null }, 'content_filter')), { code: 'LLM_CONTENT_FILTER' });
});

test('malformed response structures fail deterministically', () => {
  const invalid = [null, {}, { choices: [] }, { choices: [null] }, response({ role: 'user', content: 'hi' }), response({ content: null }), response({ content: [] }), response({ content: '' }), response({ content: 'hi', function_call: { name: 'old' } }), response({ content: 'hi', tool_calls: {} }), response({ content: 'hi' }, 'tool_calls')];
  for (const item of invalid) assert.throws(() => parseCompletion(item), { code: 'LLM_PROTOCOL' });
});

test('tool call IDs, names, and arguments must have valid bounded structures', () => {
  const invalidCalls = [
    [{ ...call(), id: '' }], [{ ...call(), id: 'bad id' }], [{ ...call(), id: 'x'.repeat(129) }],
    [call('duplicate'), call('duplicate')], [{ ...call(), type: 'custom' }],
    [call('a', 'bad name')], [call('a', 'x'.repeat(65))], [call('a', 'calculator', {})],
    [call('a', 'calculator', 'x'.repeat(65537))], [null]
  ];
  for (const tool_calls of invalidCalls) assert.throws(() => parseCompletion(response({ content: null, tool_calls })), { code: 'LLM_PROTOCOL' });
});

test('output and action summary bounds are enforced', () => {
  assert.throws(() => parseCompletion(response({ content: 'x'.repeat(128001) })), { code: 'LLM_PROTOCOL' });
  assert.equal(parseCompletion(response({ content: JSON.stringify({ answer: 'ok', decision_summary: 'x'.repeat(400) }) })).decisionSummary.length, 300);
  assert.throws(() => parseCompletion(response({ tool_calls: Array.from({ length: 33 }, (_, i) => call(`call_${i}`)) })), { code: 'LLM_PROTOCOL' });
});
