import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AgentRuntime } from '../src/runtime.js';
import { SessionStore } from '../src/store.js';
import { ContextManager } from '../src/context.js';
import { createTools } from '../src/tools.js';

// Scripted LLM responses make these behavioral integration tests deterministic.
// They exercise the real runtime and tools; they are not evidence of a live API run.
const final = answer => ({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: JSON.stringify({ decision_summary: '根据当前会话的数据回答。', answer }) } }] });
const call = (id, name, args) => ({ id, type: 'function', function: { name, arguments: JSON.stringify(args) } });
const tools = (...tool_calls) => ({ choices: [{ finish_reason: 'tool_calls', message: { role: 'assistant', content: null, tool_calls } }] });
const observation = (messages, id) => JSON.parse(messages.findLast(message => message.role === 'tool' && message.tool_call_id === id).content);
const memory = messages => {
  const record = messages.find(message => message.content?.startsWith('SESSION_MEMORY_DATA '));
  assert.ok(record, 'Every request must include current session memory data');
  return JSON.parse(record.content.slice(record.content.indexOf('\n') + 1));
};

async function fixture(t, complete, contextOptions) {
  const dataDir = await mkdtemp(join(tmpdir(), 'minimal-acceptance-'));
  t.after(() => rm(dataDir, { recursive: true, force: true }));
  const store = new SessionStore({ dataDir });
  const session = await store.create('user-A');
  const requests = [];
  const client = { async complete(request) {
    requests.push(structuredClone(request));
    return await complete(request);
  } };
  const runtime = new AgentRuntime({ store, client, registry: createTools(), context: new ContextManager(contextOptions) });
  const run = input => runtime.run({ userId: 'user-A', sessionId: session.id, input });
  return { dataDir, store, session, requests, runtime, run };
}

test('two native calls remain paired, and a later tool follow-up uses the first returned todo ID', async t => {
  let step = 0;
  let actualTodoId;
  const f = await fixture(t, request => {
    step++;
    if (step === 1) return tools(
      call('first_todo', 'todo', { action: 'add', id: null, text: '明天带伞' }),
      call('second_weather', 'weather', { city: '上海' })
    );
    if (step === 2) {
      const exchange = request.messages.slice(-3);
      assert.deepEqual(exchange.map(message => message.role), ['assistant', 'tool', 'tool']);
      assert.deepEqual(exchange[0].tool_calls.map(item => item.id), ['first_todo', 'second_weather']);
      assert.deepEqual(exchange.slice(1).map(message => message.tool_call_id), ['first_todo', 'second_weather']);
      const first = observation(request.messages, 'first_todo');
      const second = observation(request.messages, 'second_weather');
      assert.equal(first.ok, true);
      assert.equal(first.data.todo.text, '明天带伞');
      assert.equal(second.data.city, '上海');
      assert.equal(second.data.mock, true);
      actualTodoId = first.data.todo.id;
      assert.equal(memory(request.messages).todos[0].id, actualTodoId);
      return final('上海小雨（模拟天气），已记待办：明天带伞。');
    }
    if (step === 3) {
      assert.equal(request.messages.at(-1).content, '把刚才那项待办标记完成');
      const first = observation(request.messages, 'first_todo');
      assert.equal(first.data.todo.id, actualTodoId, 'Follow-up must receive the prior actual tool result');
      assert.equal(memory(request.messages).todos[0].done, false);
      return tools(call('finish_first', 'todo', { action: 'complete', id: first.data.todo.id, text: null }));
    }
    assert.equal(step, 4);
    const completed = observation(request.messages, 'finish_first');
    assert.equal(completed.ok, true);
    assert.equal(completed.data.todo.id, actualTodoId);
    assert.equal(completed.data.todo.done, true);
    return final('已将“明天带伞”标记完成。');
  });

  const initial = await f.run('查上海天气，并帮我记待办明天带伞');
  assert.equal(initial.status, 'ok');
  assert.equal(initial.steps, 2);
  assert.deepEqual(initial.trace.filter(event => event.event === 'tool.end').map(event => event.callId), ['first_todo', 'second_weather']);
  const followed = await f.run('把刚才那项待办标记完成');
  assert.equal(followed.status, 'ok');
  assert.equal(followed.steps, 2);
  assert.equal(f.requests.length, 4);
  const restarted = new SessionStore({ dataDir: f.dataDir });
  const persisted = await restarted.get('user-A', f.session.id);
  assert.equal(persisted.todos.length, 1);
  assert.equal(persisted.todos[0].id, actualTodoId);
  assert.equal(persisted.todos[0].done, true);
  assert.deepEqual(persisted.messages.filter(message => message.role === 'tool').map(message => message.tool_call_id), ['first_todo', 'second_weather', 'finish_first']);
});

test('long conversation compaction recalls historical excerpts while exact todo state survives and stays available', async t => {
  const originalInput = '我是小林，项目代号北斗。请记待办：周五检查交付清单。';
  let originalTodo;
  let compactedRequestSeen = false;
  const f = await fixture(t, request => {
    const latest = request.messages.at(-1);
    const recalled = memory(request.messages);
    if (latest.role === 'user' && latest.content === originalInput) return tools(call('original_add', 'todo', { action: 'add', id: null, text: '周五检查交付清单' }));
    if (latest.role === 'tool' && latest.tool_call_id === 'original_add') {
      originalTodo = observation(request.messages, 'original_add').data.todo;
      assert.deepEqual(recalled.todos, [originalTodo]);
      return final('小林，已为北斗项目记录待办。');
    }
    assert.deepEqual(recalled.todos, [originalTodo], 'Structured todo IDs, text, completion flag, and timestamps must remain exact');
    if (recalled.summary) {
      compactedRequestSeen = true;
      assert.match(recalled.summary, /compression|history omitted/);
      assert.match(recalled.summary, /北斗/, 'The early project fact remains in the bounded historical anchor');
    }
    assert.ok(JSON.stringify({ messages: request.messages, tools: request.tools }).length <= 7600);
    if (latest.role === 'user' && latest.content === '项目叫什么？再列一下原来的待办') {
      assert.ok(recalled.summary.length > 0, 'The follow-up receives compressed memory, not only recent turns');
      assert.ok(!request.messages.some(message => message.content === originalInput), 'The old raw turn was actually compacted');
      return tools(call('recall_list', 'todo', { action: 'list', id: null, text: null }));
    }
    if (latest.role === 'tool' && latest.tool_call_id === 'recall_list') {
      assert.deepEqual(observation(request.messages, 'recall_list').data.todos, [originalTodo]);
      return final('项目是北斗；待办仍是“周五检查交付清单”，尚未完成。');
    }
    return final(`已记录这次补充。${'这是普通对话内容。'.repeat(85)}`);
  }, { maxContextChars: 7600, summaryChars: 1600, recentTurns: 2 });

  assert.equal((await f.run(originalInput)).status, 'ok');
  for (let index = 0; index < 9; index++) {
    const result = await f.run(`第 ${index + 1} 次补充：${'这里是不会改变待办的背景信息。'.repeat(65)}`);
    assert.equal(result.status, 'ok');
  }
  assert.equal(compactedRequestSeen, true);
  const beforeFollowup = await f.store.get('user-A', f.session.id);
  assert.ok(beforeFollowup.summary.length > 0);
  assert.ok(beforeFollowup.summary.length <= 1600);
  assert.ok(beforeFollowup.messages.length < 22, 'Old conversation turns are removed from raw history');
  assert.deepEqual(beforeFollowup.todos, [originalTodo]);
  const followed = await f.run('项目叫什么？再列一下原来的待办');
  assert.equal(followed.status, 'ok');
  assert.match(followed.answer, /北斗/);
  const restarted = new SessionStore({ dataDir: f.dataDir });
  assert.deepEqual((await restarted.get('user-A', f.session.id)).todos, [originalTodo]);
});

test('two windows for one user actually overlap and never mix histories, follow-ups, or durable todos', async t => {
  let arrivals = 0;
  let release;
  const bothStarted = new Promise(resolve => { release = resolve; });
  const capturedByWindow = { ONE: [], TWO: [] };
  const marker = { ONE: 'WINDOW_ONE_ONLY', TWO: 'WINDOW_TWO_ONLY' };
  const title = { ONE: 'WINDOW_ONE_ONLY 明天带伞', TWO: 'WINDOW_TWO_ONLY 写周报' };
  const f = await fixture(t, async request => {
    const recalled = memory(request.messages);
    const latestUser = request.messages.findLast(message => message.role === 'user').content;
    const window = latestUser.includes(marker.ONE) || recalled.todos.some(item => item.text.includes(marker.ONE)) ? 'ONE' : 'TWO';
    const other = window === 'ONE' ? 'TWO' : 'ONE';
    capturedByWindow[window].push(structuredClone(request));
    assert.ok(!JSON.stringify(request.messages).includes(marker[other]), 'Other window data must never enter this LLM context');
    if (latestUser === '我刚才让你记了什么？') {
      assert.deepEqual(recalled.todos.map(item => item.text), [title[window]]);
      return final(`你让记的是：${title[window]}。`);
    }
    if (request.messages.at(-1).role === 'tool') {
      const added = observation(request.messages, 'shared_call_id');
      assert.equal(added.data.todo.text, title[window]);
      return final(`已记待办：${title[window]}。`);
    }
    arrivals++;
    if (arrivals === 2) release();
    let timer;
    try {
      await Promise.race([bothStarted, new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error('Independent sessions did not overlap within 2 seconds')), 2000); })]);
    } finally { clearTimeout(timer); }
    return tools(call('shared_call_id', 'todo', { action: 'add', id: null, text: title[window] }));
  });
  const second = await f.store.create('user-A', '窗口二');
  const inputs = { ONE: `${marker.ONE}：请记待办明天带伞`, TWO: `${marker.TWO}：请记待办写周报` };
  const initial = await Promise.all([
    f.run(inputs.ONE),
    f.runtime.run({ userId: 'user-A', sessionId: second.id, input: inputs.TWO }),
  ]);
  assert.equal(arrivals, 2);
  assert.deepEqual(initial.map(result => result.status), ['ok', 'ok']);
  const followups = await Promise.all([
    f.run('我刚才让你记了什么？'),
    f.runtime.run({ userId: 'user-A', sessionId: second.id, input: '我刚才让你记了什么？' }),
  ]);
  assert.deepEqual(followups.map(result => result.status), ['ok', 'ok']);
  assert.match(followups[0].answer, /WINDOW_ONE_ONLY/);
  assert.match(followups[1].answer, /WINDOW_TWO_ONLY/);
  assert.equal(capturedByWindow.ONE.length, 3);
  assert.equal(capturedByWindow.TWO.length, 3);
  const restarted = new SessionStore({ dataDir: f.dataDir });
  for (const [window, sessionId] of [['ONE', f.session.id], ['TWO', second.id]]) {
    const persisted = await restarted.get('user-A', sessionId);
    assert.deepEqual(persisted.todos.map(item => item.text), [title[window]]);
    assert.ok(!JSON.stringify(persisted).includes(marker[window === 'ONE' ? 'TWO' : 'ONE']));
    assert.equal(persisted.messages.filter(message => message.role === 'user').length, 2);
  }
});
