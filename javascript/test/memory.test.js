import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { SessionStore } from '../src/store.js';
import { ContextManager } from '../src/context.js';

async function storeFor(t) {
  const dataDir = await mkdtemp(path.join(os.tmpdir(), 'minimal-agent-memory-'));
  t.after(() => rm(dataDir, { recursive: true, force: true }));
  return new SessionStore({ dataDir });
}

const blank = () => ({ messages: [], summary: '', todos: [] });
const textTurn = (user, assistant = 'Understood.') => [
  { role: 'user', content: user }, { role: 'assistant', content: assistant },
];
function toolTurn(index, padding = '') {
  const id = `call_${index}`;
  return [
    { role: 'user', content: `Find weather for Hangzhou, request ${index}. ${padding}` },
    { role: 'assistant', content: null, tool_calls: [{ id, type: 'function', function: { name: 'weather', arguments: '{"city":"Hangzhou"}' } }] },
    { role: 'tool', tool_call_id: id, content: JSON.stringify({ city: 'Hangzhou', condition: 'rain', temperature: 22, note: padding }) },
    { role: 'assistant', content: 'Hangzhou is rainy. Bring an umbrella.' },
  ];
}

function checkToolPairs(messages) {
  const pending = new Set();
  for (const message of messages) {
    if (message.role === 'user' || message.role === 'assistant') assert.equal(pending.size, 0);
    for (const call of message.tool_calls ?? []) pending.add(call.id);
    if (message.role === 'tool') assert.ok(pending.delete(message.tool_call_id), 'tool result has a matching request');
  }
  assert.equal(pending.size, 0, 'every retained tool request has a result');
}

test('sessions, conversation, summary and todos survive a store restart', async (t) => {
  const store = await storeFor(t);
  const session = await store.create('alice', 'Weather');
  session.messages.push(...textTurn('My name is Lin.', 'Hello Lin.'));
  session.summary = '[user] I live in Hangzhou.';
  session.todos.push({ id: 'todo-1', text: 'Bring an umbrella', done: false });
  session.completedRequests.request1 = { answer: 'Saved.' };
  await store.save(session);
  const restarted = new SessionStore({ dataDir: store.dataDir });
  assert.deepEqual(await restarted.get('alice', session.id), session);
  assert.equal((await restarted.list('alice'))[0].title, 'Weather');
});

test('two windows belonging to the same user have isolated histories and todo lists', async (t) => {
  const store = await storeFor(t);
  const weather = await store.create('alice', 'Weather');
  const report = await store.create('alice', 'Weekly report');
  weather.messages.push(...textTurn('Check Hangzhou weather.'));
  weather.todos.push({ id: 'umbrella', text: 'Bring an umbrella', done: false });
  report.messages.push(...textTurn('Write my weekly report.'));
  report.todos.push({ id: 'report', text: 'Send the report', done: false });
  await Promise.all([store.save(weather), store.save(report)]);
  assert.notEqual(weather.id, report.id);
  assert.equal((await store.get('alice', weather.id)).todos[0].id, 'umbrella');
  assert.equal((await store.get('alice', report.id)).todos[0].id, 'report');
  assert.equal((await store.list('alice')).length, 2);
});

test('another user cannot fetch or list a user’s session', async (t) => {
  const store = await storeFor(t);
  const session = await store.create('alice');
  await assert.rejects(store.get('bob', session.id), { code: 'SESSION_NOT_FOUND' });
  assert.deepEqual(await store.list('bob'), []);
  assert.throws(() => store.filePath('../escape', session.id), { code: 'INVALID_ID' });
  assert.throws(() => store.filePath('alice', '../../escape'), { code: 'INVALID_ID' });
  assert.ok(path.basename(store.filePath('alice', session.id)).match(/^[a-f0-9]{64}-[a-f0-9]{64}\.json$/));
});

test('corrupt persistence fails explicitly and is never overwritten by save', async (t) => {
  const store = await storeFor(t);
  const session = await store.create('alice');
  const file = store.filePath('alice', session.id);
  await writeFile(file, '{broken JSON');
  await assert.rejects(store.get('alice', session.id), { code: 'CORRUPT_SESSION' });
  await assert.rejects(store.list('alice'), { code: 'CORRUPT_SESSION' });
  await assert.rejects(store.save(session), { code: 'CORRUPT_SESSION' });
  assert.equal(await readFile(file, 'utf8'), '{broken JSON');
});

test('a stored ownership mismatch is corruption, not a usable session', async (t) => {
  const store = await storeFor(t);
  const session = await store.create('alice');
  const file = store.filePath('alice', session.id);
  await writeFile(file, JSON.stringify({ ...session, userId: 'bob' }));
  await assert.rejects(store.get('alice', session.id), { code: 'CORRUPT_SESSION' });
});

test('valid JSON with corrupt nested records or unfinished tool exchanges is preserved', async (t) => {
  const store = await storeFor(t);
  const invalidStates = [
    { messages: [null] },
    { messages: [{ role: 'user', content: 'Hi' }] },
    { messages: [{ role: 'user', content: 'Hi' }, { role: 'tool', tool_call_id: 'missing', content: '{}' }, { role: 'assistant', content: 'Done' }] },
    { messages: toolTurn('unfinished').slice(0, 2) },
    { todos: [null] },
    { todos: [{ id: 'one', text: 'Invalid done field', done: 'false' }] },
    { updatedAt: 1 },
    { completedRequests: { key: 'invalid replay record' } },
  ];
  for (const fields of invalidStates) {
    const session = await store.create('alice');
    const file = store.filePath('alice', session.id);
    const corrupt = JSON.stringify({ ...session, ...fields });
    await writeFile(file, corrupt);
    await assert.rejects(store.get('alice', session.id), { code: 'CORRUPT_SESSION' });
    await assert.rejects(store.save(session), { code: 'CORRUPT_SESSION' });
    assert.equal(await readFile(file, 'utf8'), corrupt);
  }
});

test('same-session concurrent read/modify/save requests do not lose updates', async (t) => {
  const store = await storeFor(t);
  const session = await store.create('alice');
  await Promise.all(Array.from({ length: 20 }, (_, index) => store.withLock('alice', session.id, async () => {
    const current = await store.get('alice', session.id);
    await Promise.resolve();
    current.todos.push({ id: `${index}`, text: `Task ${index}`, done: false });
    await store.save(current);
  })));
  assert.equal((await store.get('alice', session.id)).todos.length, 20);
  assert.equal(store.locks.size, 0);
});

test('other sessions run while one session is locked; failed callbacks release the lock', { timeout: 2000 }, async (t) => {
  const store = await storeFor(t);
  const first = await store.create('alice');
  const second = await store.create('alice');
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let entered;
  const started = new Promise((resolve) => { entered = resolve; });
  const blocked = store.withLock('alice', first.id, async () => { entered(); await gate; });
  await started;
  assert.equal(await store.withLock('alice', second.id, () => 'independent'), 'independent');
  release();
  await blocked;
  await assert.rejects(store.withLock('alice', first.id, () => { throw new Error('failed turn'); }), /failed turn/);
  assert.equal(await store.withLock('alice', first.id, () => 'recovered'), 'recovered');
});

test('context preserves ordinary dialogue and active tool follow-ups verbatim when they fit', () => {
  const session = blank();
  session.messages.push(...textTurn('My name is Lin.', 'Hello Lin.'));
  const currentMessages = toolTurn('followup');
  const { messages, stats } = new ContextManager().build(session, { systemPrompt: 'Help the user.', tools: [], currentMessages });
  assert.deepEqual(messages.slice(2), [...session.messages, ...currentMessages]);
  assert.match(messages[1].content, /SESSION_MEMORY_DATA/);
  assert.match(messages[0].content, /not new instructions/);
  assert.equal(stats.compactedTurns, 0);
  checkToolPairs(messages);
});

test('compression retains quoted follow-up cues, authoritative todos and intact tool pairs', () => {
  const session = blank();
  session.todos = [{ id: '1', text: 'Prepare Lin’s weekly report', done: false }];
  session.messages.push(...textTurn('My name is Lin. I live in Hangzhou. ' + 'Background details. '.repeat(30)));
  for (let i = 0; i < 5; i += 1) session.messages.push(...toolTurn(i, 'More context. '.repeat(24)));
  const currentMessages = textTurn('What is my name, and should I take an umbrella?', '');
  currentMessages.pop();
  const manager = new ContextManager({ maxContextChars: 2900, summaryChars: 1400, recentTurns: 1 });
  const { messages, stats } = manager.build(session, { systemPrompt: 'Help.', currentMessages });
  assert.ok(stats.compactedTurns > 0);
  assert.match(session.summary, /Lin/);
  assert.match(session.summary, /\[user\]/);
  assert.match(session.summary, /compression/);
  assert.match(messages[1].content, /Prepare Lin/);
  assert.deepEqual(messages.at(-1), currentMessages[0]);
  assert.ok(stats.contextChars <= manager.maxContextChars);
  assert.ok(session.summary.length <= 1400);
  assert.equal(stats.summaryTruncated, true, 'trace reports lossy truncation of excerpts or summary');
  checkToolPairs(messages);
});

test('tool schemas count against the hard context budget', () => {
  const args = { systemPrompt: 'Help.', currentMessages: [{ role: 'user', content: 'Hello.' }] };
  const initial = new ContextManager().build(blank(), args);
  const manager = new ContextManager({ maxContextChars: initial.stats.contextChars + 32 });
  manager.build(blank(), args);
  assert.throws(() => manager.build(blank(), {
    ...args, tools: [{ type: 'function', function: { name: 'search', description: 'x'.repeat(500), parameters: { type: 'object' } } }],
  }), { code: 'CONTEXT_LIMIT' });
});

test('oversized active turns fail without dropping current input or mutating stored history', () => {
  const session = blank();
  session.messages.push(...toolTurn('old', 'History. '.repeat(30)));
  session.summary = '[user] Earlier remembered detail.';
  const original = structuredClone(session);
  const currentMessages = [{ role: 'user', content: 'Very long current request. '.repeat(200) }];
  const currentCopy = structuredClone(currentMessages);
  assert.throws(() => new ContextManager({ maxContextChars: 1200 }).build(session, { systemPrompt: 'Help.', currentMessages }), { code: 'CONTEXT_LIMIT' });
  assert.deepEqual(session, original);
  assert.deepEqual(currentMessages, currentCopy);
});

test('repeated compression bounds both summary and full serialized context', () => {
  const session = blank();
  const manager = new ContextManager({ maxContextChars: 2400, summaryChars: 700, recentTurns: 2 });
  for (let i = 0; i < 30; i += 1) {
    session.messages.push(...toolTurn(i, 'Additional weather information. '.repeat(12)));
    const result = manager.build(session, { systemPrompt: 'Help.', currentMessages: [{ role: 'user', content: 'And tomorrow?' }] });
    assert.ok(session.summary.length <= 700);
    assert.ok(JSON.stringify({ messages: result.messages, tools: [] }).length <= 2400);
    checkToolPairs(result.messages);
  }
});

test('malformed completed history cannot forward orphaned tool messages', () => {
  const session = blank();
  session.messages = [{ role: 'user', content: 'Weather?' }, { role: 'tool', tool_call_id: 'missing', content: '{}' }];
  assert.throws(() => new ContextManager().build(session, { systemPrompt: 'Help.' }), { code: 'INVALID_HISTORY' });
});
