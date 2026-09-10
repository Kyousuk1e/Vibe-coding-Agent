import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { createApp } from '../src/app.js';
import { createAgentServer } from '../src/server.js';
import { readConfig } from '../src/config.js';

test('HTTP sessions, chat, ownership, validation, list shape and persistence end to end', async t => {
  const dataDir = await mkdtemp(join(tmpdir(), 'minimal-http-'));
  const app = createApp({ dataDir }, { client: { complete: async () => ({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: '你好' } }] }) } });
  const server = createAgentServer(app); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(dataDir, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const req = (path, data, user = 'A') => fetch(base + path, { method: data ? 'POST' : 'GET', headers: { 'X-User-Id': user, 'Content-Type': 'application/json' }, ...(data ? { body: JSON.stringify(data) } : {}) });
  assert.equal((await fetch(base + '/health')).status, 200);
  assert.equal((await fetch(base + '/sessions')).status, 400);
  assert.equal((await req('/sessions', { title: 123 })).status, 400);
  const created = await req('/sessions', { title: '窗口 1' }); assert.equal(created.status, 201);
  const session = await created.json();
  assert.equal((await req(`/sessions/${session.id}`, null, 'B')).status, 404);
  const result = await (await req(`/sessions/${session.id}/messages`, { input: '你好', requestId: 'http1' })).json();
  assert.equal(result.answer, '你好');
  const restored = await (await req(`/sessions/${session.id}`)).json();
  assert.equal(restored.messages.length, 2); assert(!('completedRequests' in restored));
  const listed = await (await req('/sessions')).json();
  assert.equal(listed.sessions.length, 1); assert(!('messages' in listed.sessions[0]));
  assert.equal((await req(`/sessions/${session.id}/messages`, { input: '' })).status, 400);
  assert.equal((await req(`/sessions/${session.id}/messages`, { input: 'changed', requestId: 'http1' })).status, 409);
  assert.equal((await fetch(base + '/sessions', { headers: { Origin: 'https://example.com', 'X-User-Id': 'A' } })).status, 403);
});

test('configuration has real-Qwen defaults, requires credentials and validates bounds', () => {
  assert.throws(() => readConfig({}), /DASHSCOPE_API_KEY/);
  const config = readConfig({ DASHSCOPE_API_KEY: 'unit-test-placeholder' });
  assert.equal(config.provider, 'qwen'); assert.equal(config.model, 'qwen-plus');
  assert.throws(() => readConfig({ DASHSCOPE_API_KEY: 'test', MAX_STEPS: '0' }), /MAX_STEPS/);
  assert.throws(() => readConfig({ DASHSCOPE_API_KEY: 'test', LLM_PROVIDER: 'typo' }), /LLM_PROVIDER/);
});
