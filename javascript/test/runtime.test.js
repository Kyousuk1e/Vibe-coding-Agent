import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { SessionStore } from '../src/store.js';
import { ContextManager } from '../src/context.js';
import { createTools } from '../src/tools.js';
import { AgentRuntime } from '../src/runtime.js';

const final = answer => ({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: JSON.stringify({ decision_summary: '根据已知信息回答', answer }) } }] });
const tool = (name, args, id = 'call_1') => ({ choices: [{ finish_reason: 'tool_calls', message: { role: 'assistant', content: null, tool_calls: [{ id, type: 'function', function: { name, arguments: typeof args === 'string' ? args : JSON.stringify(args) } }] } }] });
async function fixture(t, replies, options = {}) {
  const dataDir = await mkdtemp(join(tmpdir(), 'minimal-runtime-'));
  t.after(() => rm(dataDir, { recursive: true, force: true }));
  const store = new SessionStore({ dataDir });
  const session = await store.create('A');
  const requests = [];
  const client = { async complete(request) { requests.push(structuredClone(request)); const value = replies.shift(); if (value instanceof Error) throw value; return typeof value === 'function' ? value(request) : value; } };
  const runtime = new AgentRuntime({ store, client, registry: createTools(), context: new ContextManager(), ...options });
  const run = (input, extras = {}) => runtime.run({ userId: 'A', sessionId: session.id, input, ...extras });
  return { dataDir, store, session, requests, runtime, run };
}

test('direct reply and pure conversational follow-up receive earlier user and assistant text', async t => {
  const f = await fixture(t, [final('好的，小林。'), request => {
    assert(request.messages.some(m => m.content === '我叫小林'));
    assert(request.messages.some(m => m.content === '好的，小林。'));
    return final('你叫小林。');
  }]);
  assert.equal((await f.run('我叫小林')).steps, 1);
  assert.equal((await f.run('我叫什么？')).answer, '你叫小林。');
});

test('tool loop feeds native paired result to the next LLM request', async t => {
  const f = await fixture(t, [tool('calculator', { expression: '(12+8)*3' }), request => {
    const result = request.messages.at(-1);
    assert.equal(result.role, 'tool'); assert.equal(result.tool_call_id, 'call_1');
    assert.equal(JSON.parse(result.content).ok, true);
    assert.match(result.content, /60/);
    return final('结果是 60。');
  }]);
  const result = await f.run('计算 (12+8)*3');
  assert.equal(result.steps, 2); assert.equal(result.status, 'ok');
  assert(result.trace.some(e => e.event === 'tool.end' && e.ok));
});

test('bad tool JSON and unknown names are observations which the model can correct', async t => {
  const f = await fixture(t, [tool('calculator', '{broken', 'a'), tool('missing', {}, 'b'), tool('calculator', { expression: '2+3' }, 'c'), final('5')]);
  const result = await f.run('算 2+3');
  assert.equal(result.status, 'ok');
  assert.deepEqual(result.trace.filter(e => e.event === 'tool.end').map(e => e.ok), [false, false, true]);
  assert.equal(f.requests[2].messages.filter(m => m.role === 'tool').length, 2);
});

test('same user in two sessions has separate todo state and histories; tool follow-up survives restart', async t => {
  const f = await fixture(t, [tool('todo', { action: 'add', text: '带伞', id: null }), final('已添加带伞。'), tool('todo', { action: 'add', text: '写周报', id: null }), final('已添加写周报。')]);
  const second = await f.store.create('A');
  await f.run('记住明天带伞');
  await f.runtime.run({ userId: 'A', sessionId: second.id, input: '记待办写周报' });
  const restarted = new SessionStore({ dataDir: f.dataDir });
  const a = await restarted.get('A', f.session.id), b = await restarted.get('A', second.id);
  assert.deepEqual(a.todos.map(x => x.text), ['带伞']); assert.deepEqual(b.todos.map(x => x.text), ['写周报']);
  assert(!JSON.stringify(a.messages).includes('写周报'));
  const replies = [tool('todo', { action: 'complete', text: null, id: a.todos[0].id }), final('已完成。')];
  const runtime = new AgentRuntime({ store: restarted, client: { complete: async () => replies.shift() }, registry: createTools(), context: new ContextManager() });
  await runtime.run({ userId: 'A', sessionId: a.id, input: '把刚才那项标记完成' });
  assert.equal((await restarted.get('A', a.id)).todos[0].done, true);
  assert.equal((await restarted.get('A', b.id)).todos[0].done, false);
});

test('max steps is enforced with a terminal answer and complete tool pairs', async t => {
  const f = await fixture(t, [tool('calculator', { expression: '1+1' })], { maxSteps: 1 });
  const result = await f.run('一直计算');
  assert.equal(result.status, 'max_steps'); assert.equal(f.requests.length, 1);
  const messages = (await f.store.get('A', f.session.id)).messages;
  assert.deepEqual(messages.map(x => x.role), ['user', 'assistant', 'tool', 'assistant']);
});

test('API error after local write rolls back todos and does not retain false success tool results', async t => {
  const f = await fixture(t, [tool('todo', { action: 'add', text: '草稿', id: null }), Object.assign(new Error('secret provider message'), { code: 'LLM_AUTH' })]);
  const result = await f.run('添加草稿');
  assert.equal(result.status, 'error'); assert(!JSON.stringify(result).includes('secret provider'));
  const session = await f.store.get('A', f.session.id);
  assert.deepEqual(session.todos, []); assert.deepEqual(session.messages.map(m => m.role), ['user', 'assistant']);
  assert.match(result.answer, /未提交/);
});

test('same request is replayed without new model calls or duplicate writes, including concurrent delivery', async t => {
  const f = await fixture(t, [tool('todo', { action: 'add', text: '唯一任务', id: null }), final('已添加')]);
  const [first, replay] = await Promise.all([f.run('添加唯一任务', { requestId: 'req1' }), f.run('添加唯一任务', { requestId: 'req1' })]);
  assert.equal(first.replayed, false); assert.equal(replay.replayed, true); assert.equal(f.requests.length, 2);
  assert.equal((await f.store.get('A', f.session.id)).todos.length, 1);
  await assert.rejects(f.run('另一条输入', { requestId: 'req1' }), { code: 'REQUEST_CONFLICT' });
});

test('oversized input and reserved replay keys fail before model invocation', async t => {
  const f = await fixture(t, []);
  await assert.rejects(f.run('x'.repeat(4001)), { code: 'BAD_INPUT' });
  await assert.rejects(f.run('hello', { requestId: '__proto__' }), { code: 'BAD_INPUT' });
  assert.equal(f.requests.length, 0);
});

test('replay eviction follows completion order even for integer-looking request IDs after restart', async t => {
  const f = await fixture(t, Array.from({ length: 21 }, () => final('确认')));
  for (let id = 100; id < 120; id++) await f.run('确认', { requestId: String(id) });
  await f.run('最新', { requestId: '1' });
  const saved = await f.store.get('A', f.session.id);
  assert.equal(Object.keys(saved.completedRequests).length, 20);
  assert(!Object.hasOwn(saved.completedRequests, '100'));
  assert(Object.hasOwn(saved.completedRequests, '1'));
  const restarted = new AgentRuntime({ store: new SessionStore({ dataDir: f.dataDir }), client: { complete: async () => { throw new Error('must not call'); } }, registry: createTools(), context: new ContextManager() });
  const replay = await restarted.run({ userId: 'A', sessionId: f.session.id, input: '最新', requestId: '1' });
  assert.equal(replay.replayed, true); assert.equal(replay.status, 'ok');
});

test('oversized active context is a bounded error and does not call LLM', async t => {
  const f = await fixture(t, [], { context: new ContextManager({ maxContextChars: 1000 }) });
  assert.equal((await f.run('简短输入')).status, 'error');
  assert.equal(f.requests.length, 0);
});
