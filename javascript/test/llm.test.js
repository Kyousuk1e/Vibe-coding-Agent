import test from 'node:test';
import assert from 'node:assert/strict';
import { ChatClient } from '../src/llm.js';

const completion = { choices: [{ message: { role: 'assistant', content: '你好' }, finish_reason: 'stop' }], usage: { total_tokens: 12 } };
const messages = [{ role: 'user', content: '你好' }];
const tool = { type: 'function', function: { name: 'calculator', description: 'Calculate arithmetic', strict: true, parameters: { type: 'object', properties: { expression: { type: 'string' } }, required: ['expression'], additionalProperties: false } } };
const jsonResponse = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const client = options => new ChatClient({ apiKey: 'secret-unit-test-key', maxRetries: 0, ...options });

test('Qwen sends a real compatible Chat Completions request and returns full JSON', async () => {
  let request;
  const llm = client({ fetchImpl: async (url, options) => { request = { url, ...options }; return jsonResponse(completion); } });
  assert.deepEqual(await llm.complete({ messages, tools: [tool] }), completion);
  assert.equal(request.url, 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions');
  assert.equal(request.method, 'POST');
  assert.equal(request.headers.Authorization, 'Bearer secret-unit-test-key');
  assert.equal(request.redirect, 'error');
  const body = JSON.parse(request.body);
  assert.equal(body.model, 'qwen-plus');
  assert.equal(body.max_tokens, 1200);
  assert.equal(body.enable_thinking, false);
  assert.equal(body.max_completion_tokens, undefined);
  assert.equal(body.tools[0].function.strict, undefined);
  assert.deepEqual(body.tools[0].function.parameters, tool.function.parameters);
  assert.equal(tool.function.strict, true, 'Caller tool schema must not be mutated');
  assert.equal(body.tool_choice, 'auto');
  assert.equal(body.parallel_tool_calls, false);
  assert.deepEqual(body.messages, messages);
  assert.equal(body.stream, false);
  assert.ok(!JSON.stringify(llm).includes('secret-unit-test-key'));
});

test('OpenAI provider preserves strict schemas and uses max_completion_tokens', async () => {
  let body;
  const llm = client({ provider: 'openai', baseUrl: 'https://api.openai.com/v1/', model: 'gpt-4.1-mini', maxOutputTokens: 500, fetchImpl: async (url, options) => {
    assert.equal(url, 'https://api.openai.com/v1/chat/completions');
    body = JSON.parse(options.body);
    return jsonResponse(completion);
  } });
  await llm.complete({ messages, tools: [tool] });
  assert.equal(body.max_completion_tokens, 500);
  assert.equal(body.max_tokens, undefined);
  assert.equal(body.enable_thinking, undefined);
  assert.equal(body.tools[0].function.strict, true);
});

test('provider selection alone chooses the matching endpoint and model', async () => {
  await client({ provider: 'openai', fetchImpl: async (url, options) => {
    assert.equal(url, 'https://api.openai.com/v1/chat/completions');
    assert.equal(JSON.parse(options.body).model, 'gpt-4.1-mini');
    return jsonResponse(completion);
  } }).complete({ messages });
});

test('conversation without tools omits tool-specific fields', async () => {
  await client({ fetchImpl: async (_url, options) => {
    const body = JSON.parse(options.body);
    assert.equal(body.tools, undefined);
    assert.equal(body.tool_choice, undefined);
    assert.equal(body.parallel_tool_calls, undefined);
    return jsonResponse(completion);
  } }).complete({ messages });
});

for (const status of [429, 500, 503]) {
  test(`HTTP ${status} is retried and Retry-After is respected`, async () => {
    let calls = 0;
    const llm = client({ maxRetries: 1, fetchImpl: async () => ++calls === 1
      ? new Response('provider private diagnostic', { status, headers: { 'Retry-After': '0' } })
      : jsonResponse(completion) });
    assert.deepEqual(await llm.complete({ messages }), completion);
    assert.equal(calls, 2);
  });
}

for (const status of [400, 401, 403, 404]) {
  test(`HTTP ${status} fails without retries and never discloses credentials or body`, async () => {
    let calls = 0;
    const llm = client({ maxRetries: 3, fetchImpl: async () => {
      calls++;
      return new Response('secret-unit-test-key PRIVATE_PROVIDER_BODY', { status });
    } });
    await assert.rejects(llm.complete({ messages }), err => {
      assert.equal(err.code, [401, 403].includes(status) ? 'LLM_AUTH' : 'LLM_HTTP');
      assert.equal(err.status, status);
      assert.doesNotMatch(String(err), /secret-unit-test-key|PRIVATE_PROVIDER_BODY/);
      return true;
    });
    assert.equal(calls, 1);
  });
}

test('network failures are sanitized even when they forge an internal error code', async () => {
  const llm = client({ fetchImpl: async () => { throw Object.assign(new Error('secret-unit-test-key'), { code: 'LLM_HTTP' }); } });
  await assert.rejects(llm.complete({ messages }), err => err.code === 'LLM_NETWORK' && !String(err).includes('secret-unit-test-key'));
});

test('network failures use a bounded retry count', async () => {
  let calls = 0;
  const llm = client({ maxRetries: 1, fetchImpl: async () => {
    calls++;
    throw new Error('sensitive network error');
  } });
  await assert.rejects(llm.complete({ messages }), { code: 'LLM_NETWORK' });
  assert.equal(calls, 2);
});

test('timeout aborts HTTP work and is reported with a stable code', async () => {
  let calls = 0;
  const llm = client({ timeoutMs: 5, fetchImpl: async (_url, { signal }) => {
    calls++;
    return await new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('secret-unit-test-key')), { once: true }));
  } });
  await assert.rejects(llm.complete({ messages }), err => err.code === 'LLM_TIMEOUT' && !String(err).includes('secret-unit-test-key'));
  assert.equal(calls, 1);
});

test('a transient timeout is retried and can recover', async () => {
  let calls = 0;
  const llm = client({ timeoutMs: 5, maxRetries: 1, fetchImpl: async (_url, { signal }) => {
    if (++calls > 1) return jsonResponse(completion);
    return await new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('timeout')), { once: true }));
  } });
  assert.deepEqual(await llm.complete({ messages }), completion);
  assert.equal(calls, 2);
});

test('caller cancellation does not retry or reveal the cancellation reason', async () => {
  const controller = new AbortController();
  controller.abort('private cancellation reason');
  let calls = 0;
  const llm = client({ maxRetries: 2, fetchImpl: async () => { calls++; return jsonResponse(completion); } });
  await assert.rejects(llm.complete({ messages, signal: controller.signal }), { code: 'LLM_ABORTED' });
  assert.equal(calls, 0);
});

test('in-flight cancellation aborts without retries', async () => {
  const controller = new AbortController();
  let calls = 0;
  const llm = client({ maxRetries: 2, fetchImpl: async (_url, { signal }) => {
    calls++;
    queueMicrotask(() => controller.abort());
    return await new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
  } });
  await assert.rejects(llm.complete({ messages, signal: controller.signal }), { code: 'LLM_ABORTED' });
  assert.equal(calls, 1);
});

test('malformed JSON is a protocol failure with no retries or raw body', async () => {
  let calls = 0;
  const llm = client({ maxRetries: 2, fetchImpl: async () => { calls++; return new Response('SECRET not json'); } });
  await assert.rejects(llm.complete({ messages }), err => err.code === 'LLM_PROTOCOL' && !String(err).includes('SECRET'));
  assert.equal(calls, 1);
});

test('oversized streamed responses are rejected even without a Content-Length', async () => {
  const llm = client({ fetchImpl: async () => new Response('x'.repeat(1_048_577)) });
  await assert.rejects(llm.complete({ messages }), { code: 'LLM_PROTOCOL' });
});

test('oversized declared responses are rejected before reading', async () => {
  const llm = client({ fetchImpl: async () => new Response('{}', { headers: { 'Content-Length': '2097152' } }) });
  await assert.rejects(llm.complete({ messages }), { code: 'LLM_PROTOCOL' });
});

test('unsafe URL forms are rejected while localhost HTTP is supported', () => {
  for (const baseUrl of ['http://example.com/v1', 'https://user:secret@example.com/v1', 'https://example.com/v1?key=secret', 'https://example.com/v1#secret', 'file:///tmp/api']) {
    assert.throws(() => client({ baseUrl }), err => err.code === 'LLM_CONFIG' && !String(err).includes('secret'));
  }
  for (const baseUrl of ['http://localhost:8000/v1', 'http://127.0.0.1:8000/v1', 'http://[::1]:8000/v1']) assert.doesNotThrow(() => client({ baseUrl }));
});

test('missing keys and invalid bounds fail early', () => {
  for (const options of [{ apiKey: '' }, { apiKey: 'x\ny' }, { timeoutMs: 0 }, { maxRetries: 10 }, { maxOutputTokens: 0 }, { provider: 'unknown' }]) assert.throws(() => client(options), { code: 'LLM_CONFIG' });
});
