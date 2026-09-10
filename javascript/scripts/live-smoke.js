import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { readConfig } from '../src/config.js';
import { createApp } from '../src/app.js';
import { SessionStore } from '../src/store.js';

const reportPath = fileURLToPath(new URL('../docs/live-result.json', import.meta.url));

function successfulToolData(result, tool) {
  return result.trace.filter(event => event.event === 'tool.end' && event.tool === tool && event.ok).map(event => {
    let observed;
    try { observed = JSON.parse(event.result); }
    catch { assert.fail(`${tool}: successful tool trace must include its complete bounded result JSON`); }
    assert.equal(observed.ok, true, `${tool}: tool trace must represent a successful observation`);
    return observed.data;
  });
}

function assertNoToolCalls(result, label) {
  assert.equal(result.trace.filter(event => event.event === 'tool.start').length, 0, `${label}: direct conversation must not call tools`);
}

// Explicit command only. Missing credentials cause failure, never a mocked success or skip.
const report = { startedAt: new Date().toISOString(), kind: 'real-llm-api', checks: [], passed: false };
try {
  const config = readConfig();
  config.dataDir = await mkdtemp(join(tmpdir(), 'minimal-agent-live-'));
  report.provider = config.provider; report.model = config.model;
  report.apiOrigin = new URL(config.baseUrl).origin;
  const { runtime, store } = createApp(config);
  const first = await store.create('live_A', '天气和待办');
  const second = await store.create('live_A', '周报和待办');
  const run = async (session, label, input, expectedTool, activeRuntime = runtime) => {
    console.log(`Running: ${label}`);
    const result = await activeRuntime.run({ userId: 'live_A', sessionId: session.id, input, requestId: randomUUID() });
    const check = { label, input, status: result.status, answer: result.answer, trace: result.trace };
    report.checks.push(check);
    assert.equal(result.status, 'ok', `${label}: ${result.answer}`);
    if (expectedTool) assert(result.trace.some(e => e.event === 'tool.end' && e.tool === expectedTool && e.ok), `${label}: expected successful ${expectedTool}`);
    return result;
  };
  const remembered = await run(first, '纯对话记忆写入', '请记住：我的项目代号是蓝鲸七号。简短确认即可，不需要工具。');
  assertNoToolCalls(remembered, '纯对话记忆写入');
  const follow = await run(first, '纯对话追问', '我的项目代号是什么？');
  assertNoToolCalls(follow, '纯对话追问');
  assert.match(follow.answer, /蓝鲸七号/);
  const calc = await run(first, '计算工具', '请用计算器算 (123+456)*7，并告诉我数值。', 'calculator');
  assert(successfulToolData(calc, 'calculator').some(data => data.result === 4053), 'calculator: observed result must be exactly 4053');
  assert.match(calc.answer, /4053|4,053/);
  const search = await run(first, '搜索工具', '请调用 search 搜索上下文压缩的资料，并说明数据来源。', 'search');
  assert(successfulToolData(search, 'search').some(data => data.mock === true && data.source === 'local-demo-corpus' && Array.isArray(data.results) && data.results.length > 0), 'search: expected nonempty results from the local mock corpus');
  assert.match(search.answer, /模拟|mock|演示|本地|local-demo-corpus/i, 'search: answer must disclose its mock/local source');
  const weather = await run(first, '查天气并写待办', '查询上海天气，并添加一条文字恰好为“明天带伞”的待办。', 'weather');
  assert(successfulToolData(weather, 'weather').some(data => data.city === '上海' && data.mock === true && data.source === 'fixed-demo-data'), 'weather: expected the Shanghai mock observation');
  assert(weather.trace.some(e => e.event === 'tool.end' && e.tool === 'todo' && e.ok));
  assert.match(weather.answer, /模拟|mock|演示|固定/i);
  await run(second, '独立窗口写周报和待办', '本周完成了 Agent 工具注册，下周准备补测试。帮我写一段简短周报，并添加一条文字恰好为“周五提交周报”的待办。', 'todo');
  const a = await store.get('live_A', first.id), b = await store.get('live_A', second.id);
  assert.deepEqual(a.todos.map(x => x.text), ['明天带伞']);
  assert.deepEqual(b.todos.map(x => x.text), ['周五提交周报']);
  await run(first, '带工具追问', '把刚才添加的带伞那项待办标记完成。', 'todo');
  const restarted = new SessionStore({ dataDir: config.dataDir });
  assert.equal((await restarted.get('live_A', first.id)).todos[0].done, true);
  assert.equal((await restarted.get('live_A', second.id)).todos[0].done, false);
  report.checks.push({ label: '重新读取持久化状态和双窗口隔离', status: 'ok' });
  // A fresh app creates a new runtime, client, context manager, and store over the saved files.
  const restartedApp = createApp(config);
  const restoredFirst = await restartedApp.store.get('live_A', first.id);
  const restoredSecond = await restartedApp.store.get('live_A', second.id);
  const afterRestart = await run(restoredFirst, '新 Runtime 续聊恢复记忆和待办', '我的项目代号是什么？“明天带伞”这项待办的状态是已完成还是未完成？请明确回答实际状态，只查询，不修改任何待办。', undefined, restartedApp.runtime);
  assert.match(afterRestart.answer, /蓝鲸七号/);
  assert.match(afterRestart.answer, /已.{0,8}完成|完成了|done\s*[:：=]\s*true/i, 'restart: answer must reflect the saved completed status');
  for (const observed of successfulToolData(afterRestart, 'todo')) {
    assert.equal(observed.action, 'list', 'restart: any successful todo invocation must be read-only');
  }
  assert.deepEqual((await restartedApp.store.get('live_A', first.id)).todos, restoredFirst.todos, 'restart: first-window todo state must remain unchanged');
  assert.deepEqual((await restartedApp.store.get('live_A', second.id)).todos, restoredSecond.todos, 'restart: second-window todo state must remain unchanged');
  report.passed = true;
  console.log('Real API smoke checks passed. See docs/live-result.json.');
} catch (err) {
  report.failure = { code: err.code || 'LIVE_CHECK_FAILED', message: String(err.message).slice(0, 600) };
  console.error(`Real API checks failed: ${report.failure.message}`);
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  await mkdir(dirname(reportPath), { recursive: true });
  await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
}
