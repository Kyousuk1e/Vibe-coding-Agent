import test from 'node:test';
import assert from 'node:assert/strict';
import { ToolRegistry } from '../src/registry.js';
import { calculate, createTools } from '../src/tools.js';
import { ContextManager } from '../src/context.js';
import { SYSTEM_PROMPT } from '../src/prompt.js';

const empty = () => ({ type: 'object', properties: {}, required: [], additionalProperties: false });
const tool = (parameters = empty(), execute = () => 'ok') => ({ name: 'demo', description: 'Demo', parameters, execute });

test('registry publishes isolated strict schemas and rejects duplicate/unsupported definitions', () => {
  const registry = new ToolRegistry().register(tool());
  assert.equal(registry.schemas()[0].function.strict, true);
  const external = registry.schemas();
  external[0].function.parameters.type = 'string';
  assert.equal(registry.schemas()[0].function.parameters.type, 'object');
  assert.throws(() => registry.register(tool()), /Duplicate/);
  for (const parameters of [
    { ...empty(), additionalProperties: true },
    { ...empty(), pattern: 'x' },
    { ...empty(), properties: { a: { type: 'string' } } },
    { ...empty(), properties: { a: { type: 'integer', minimum: NaN } }, required: ['a'] },
    { ...empty(), properties: { a: { type: 'array' } }, required: ['a'] },
    { ...empty(), properties: { a: { type: ['string', 'number'] } }, required: ['a'] },
  ]) assert.throws(() => new ToolRegistry().register(tool(parameters)), /schema/);
});

test('recursive arguments validation rejects extra/missing/wrong values before execution', async () => {
  let count = 0;
  const parameters = {
    type: 'object', additionalProperties: false, required: ['rows', 'note'],
    properties: {
      rows: { type: 'array', minItems: 1, maxItems: 2, items: { type: 'integer', minimum: 0, maximum: 5 } },
      note: { anyOf: [{ type: 'string', minLength: 1, maxLength: 3 }, { type: 'null' }] },
    },
  };
  const registry = new ToolRegistry().register(tool(parameters, args => { count++; return args; }));
  for (const args of [
    {}, { rows: [], note: null }, { rows: [1, 2, 3], note: null }, { rows: [1.2], note: null },
    { rows: [Infinity], note: null }, { rows: [6], note: null }, { rows: [1], note: '' },
    { rows: [1], note: '1234' }, { rows: [1], note: null, extra: 1 }, { rows: [1] },
  ]) assert.equal((await registry.execute('demo', args)).error.code, 'INVALID_ARGUMENTS');
  assert.equal(count, 0);
  assert.deepEqual(await registry.execute('demo', { rows: [1, 2], note: null }), { ok: true, data: { rows: [1, 2], note: null } });
  assert.equal((await registry.execute('missing', {})).error.code, 'UNKNOWN_TOOL');
});

test('calculator handles precedence, right associative powers, decimals and scientific numbers', () => {
  for (const [expression, expected] of [
    ['2+3*4', 14], ['(2+3)*4', 20], ['2**3**2', 512], ['-2**2', -4], ['(-2)**2', 4],
    ['2**-2', .25], ['.5 + 1.5e2', 150.5], ['7%4', 3], ['1 - -2', 3], [' 1. + 2 ', 3], ['-0', 0],
  ]) assert.equal(calculate(expression), expected, expression);
});

test('calculator rejects injection, overflow, divide/modulo zero and excessive nesting', () => {
  for (const expression of [
    '', ' ', 'process.exit()', '1;globalThis.x=1', 'constructor.constructor("return 1")()',
    '2(3)', '1e', '0x10', '2//3', '(2+3', '2+3)', '1/0', '1%0', '1e999', '10**999', '(-1)**.5',
    `${'('.repeat(40)}1${')'.repeat(40)}`, `${'-'.repeat(40)}1`, '1'.repeat(501),
  ]) assert.throws(() => calculate(expression), { name: 'ToolError' }, expression);
});

test('todos are session-local, support followups and cannot address another session item', async () => {
  const registry = createTools();
  const a = { todos: [] };
  const b = { todos: [] };
  const add = await registry.execute('todo', { action: 'add', id: null, text: '  带伞  ' }, { session: a });
  assert.equal(add.ok, true);
  assert.equal(add.data.todo.text, '带伞');
  const id = add.data.todo.id;
  assert.equal((await registry.execute('todo', { action: 'complete', id, text: null }, { session: b })).error.code, 'TODO_NOT_FOUND');
  assert.deepEqual(b.todos, []);
  assert.equal((await registry.execute('todo', { action: 'list', id: null, text: null }, { session: a })).data.todos.length, 1);
  assert.equal((await registry.execute('todo', { action: 'complete', id, text: null }, { session: a })).data.todo.done, true);
  assert.equal(a.todos[0].done, true);
  await registry.execute('todo', { action: 'remove', id, text: null }, { session: a });
  assert.deepEqual(a.todos, []);
});

test('todo arguments and capacity limits do not mutate state on failure', async () => {
  const registry = createTools();
  const session = { todos: [] };
  for (const args of [
    { action: 'add', id: null, text: ' ' }, { action: 'add', id: null, text: '\u0000\u0001' }, { action: 'add', id: 'x', text: 'x' },
    { action: 'add', id: null, text: 'x'.repeat(201) }, { action: 'list', id: 'x', text: null },
    { action: 'complete', id: null, text: null }, { action: 'remove', id: 'x', text: 'extra' },
    { action: 'unexpected', id: null, text: null },
  ]) assert.equal((await registry.execute('todo', args, { session })).ok, false);
  assert.deepEqual(session.todos, []);
  session.todos = Array.from({ length: 20 }, (_, i) => ({ id: String(i), text: 'x', done: false }));
  assert.equal((await registry.execute('todo', { action: 'add', id: null, text: 'x' }, { session })).error.code, 'TODO_LIMIT');
  assert.equal(session.todos.length, 20);
});

test('mock tools label provenance and return bounded relevant data', async () => {
  const registry = createTools();
  assert.deepEqual(registry.schemas().map(item => item.function.name), ['calculator', 'search', 'todo', 'weather']);
  const search = await registry.execute('search', { query: 'session memory' });
  assert.equal(search.data.mock, true);
  assert.equal(search.data.results[0].id, 'session-memory');
  assert.ok(search.data.results.length <= 3);
  assert.equal((await registry.execute('weather', { city: '上海市' })).data.city, '上海');
  assert.equal((await registry.execute('weather', { city: 'Shanghai' })).data.mock, true);
  assert.equal((await registry.execute('weather', { city: 'Atlantis' })).error.code, 'CITY_NOT_SUPPORTED');
});

test('maximum-size todo lists fit tool output and default full context budgets', async () => {
  const registry = createTools();
  const session = { todos: [] };
  for (let i = 0; i < 20; i++) {
    const added = await registry.execute('todo', { action: 'add', id: null, text: '待'.repeat(200) }, { session });
    assert.equal(added.ok, true);
  }
  const listed = await registry.execute('todo', { action: 'list', id: null, text: null }, { session });
  assert.equal(listed.ok, true);
  assert.equal(listed.data.todos.length, 20);
  const built = new ContextManager().build(session, {
    systemPrompt: SYSTEM_PROMPT,
    tools: registry.schemas(),
    currentMessages: [
      { role: 'user', content: '待'.repeat(4000) },
      { role: 'assistant', content: null, tool_calls: [{ id: 'call_list', type: 'function', function: { name: 'todo', arguments: JSON.stringify({ action: 'list', id: null, text: null }) } }] },
      { role: 'tool', tool_call_id: 'call_list', content: JSON.stringify(listed) },
    ],
  });
  assert.ok(built.stats.contextChars <= 24000);
});

test('escaped todo titles cannot exhaust mandatory memory and remain listable/removable', async () => {
  const registry = createTools();
  const session = { todos: [] };
  let failure;
  for (let i = 0; i < 20; i++) {
    const previous = session.todos.length;
    const result = await registry.execute('todo', { action: 'add', id: null, text: '"'.repeat(200) }, { session });
    if (!result.ok) {
      failure = result;
      assert.equal(session.todos.length, previous);
      break;
    }
  }
  assert.equal(failure.error.code, 'TODO_MEMORY_LIMIT');
  assert.ok(JSON.stringify(JSON.stringify(session.todos)).length <= 6500);
  const listed = await registry.execute('todo', { action: 'list', id: null, text: null }, { session });
  assert.equal(listed.ok, true);
  const built = new ContextManager().build(session, {
    systemPrompt: SYSTEM_PROMPT, tools: registry.schemas(),
    currentMessages: [
      { role: 'user', content: '待'.repeat(4000) },
      { role: 'assistant', content: null, tool_calls: [{ id: 'call_list', type: 'function', function: { name: 'todo', arguments: JSON.stringify({ action: 'list', id: null, text: null }) } }] },
      { role: 'tool', tool_call_id: 'call_list', content: JSON.stringify(listed) },
    ],
  });
  assert.ok(built.stats.contextChars <= 24000);
  const beforeRemove = session.todos.length;
  assert.equal((await registry.execute('todo', { action: 'remove', id: session.todos[0].id, text: null }, { session })).ok, true);
  assert.equal(session.todos.length, beforeRemove - 1);
});

test('timeouts and caller cancellation abort cooperatively; unexpected errors hide secrets', async () => {
  let observedSignal;
  const registry = new ToolRegistry({ timeoutMs: 15 }).register(tool(empty(), (_, { signal }) => {
    observedSignal = signal;
    return new Promise(() => {});
  }));
  assert.equal((await registry.execute('demo', {})).error.code, 'TOOL_TIMEOUT');
  assert.equal(observedSignal.aborted, true);
  const controller = new AbortController();
  const promise = registry.execute('demo', {}, { signal: controller.signal });
  controller.abort();
  assert.equal((await promise).error.code, 'ABORTED');
  const unsafe = new ToolRegistry().register(tool(empty(), () => { throw new Error('secret-api-key'); }));
  assert.deepEqual(await unsafe.execute('demo', {}), { ok: false, error: { code: 'TOOL_ERROR', message: 'Tool execution failed' } });
  const huge = new ToolRegistry({ maxResultChars: 100 }).register(tool(empty(), () => 'x'.repeat(101)));
  assert.equal((await huge.execute('demo', {})).error.code, 'OUTPUT_TOO_LARGE');
});

test('failed or oversized custom tools cannot commit staged todo mutations', async () => {
  for (const execute of [
    (_, { session }) => { session.todos.push({ text: 'failed' }); throw new Error('failure'); },
    (_, { session }) => { session.todos.push({ text: 'oversized' }); return 'x'.repeat(101); },
    (_, { session }) => { session.todos = null; return 'invalid'; },
  ]) {
    const registry = new ToolRegistry({ maxResultChars: 100 }).register(tool(empty(), execute));
    const session = { todos: [] };
    assert.equal((await registry.execute('demo', {}, { session })).ok, false);
    assert.deepEqual(session.todos, []);
  }
});

test('successful tool commits detached todos only and late timed-out writes are isolated', async () => {
  let captured;
  const registry = new ToolRegistry().register(tool(empty(), (_, { session }) => {
    captured = session;
    session.todos.push({ text: 'committed', done: false });
    session.userId = 'another-user';
    return session.todos;
  }));
  const session = { todos: [], userId: 'original-user' };
  const result = await registry.execute('demo', {}, { session });
  assert.equal(result.ok, true);
  captured.todos[0].text = 'late mutation';
  result.data[0].text = 'result mutation';
  assert.equal(session.todos[0].text, 'committed');
  assert.equal(session.userId, 'original-user');

  let late;
  const timeoutRegistry = new ToolRegistry({ timeoutMs: 10 }).register(tool(empty(), (_, { session: snapshot }) => {
    late = snapshot;
    snapshot.todos.push({ text: 'early failed mutation' });
    return new Promise(() => {});
  }));
  const pending = { todos: [] };
  assert.equal((await timeoutRegistry.execute('demo', {}, { session: pending })).error.code, 'TOOL_TIMEOUT');
  late.todos.push({ text: 'late failed mutation' });
  assert.deepEqual(pending.todos, []);
});
