import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { readConfig } from '../src/config.js';
import { createApp } from '../src/app.js';
import { SessionStore } from '../src/store.js';

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
  const run = async (session, label, input, expectedTool) => {
    console.log(`Running: ${label}`);
    const result = await runtime.run({ userId: 'live_A', sessionId: session.id, input, requestId: randomUUID() });
    const check = { label, input, status: result.status, answer: result.answer, trace: result.trace };
    report.checks.push(check);
    assert.equal(result.status, 'ok', `${label}: ${result.answer}`);
    if (expectedTool) assert(result.trace.some(e => e.event === 'tool.end' && e.tool === expectedTool && e.ok), `${label}: expected successful ${expectedTool}`);
    return result;
  };
  await run(first, '纯对话记忆写入', '请记住：我的项目代号是蓝鲸七号。简短确认即可，不需要工具。');
  const follow = await run(first, '纯对话追问', '我的项目代号是什么？');
  assert.match(follow.answer, /蓝鲸七号/);
  const calc = await run(first, '计算工具', '请用计算器算 (123+456)*7，并告诉我数值。', 'calculator');
  assert.match(calc.answer, /4053|4,053/);
  await run(first, '搜索工具', '请调用 search 搜索上下文压缩的资料，并说明数据来源。', 'search');
  const weather = await run(first, '查天气并写待办', '查询上海天气，并添加一条文字恰好为“明天带伞”的待办。', 'weather');
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
  report.checks.push({ label: '重启恢复和双窗口隔离', status: 'ok' });
  report.passed = true;
  console.log('Real API smoke checks passed. See docs/live-result.json.');
} catch (err) {
  report.failure = { code: err.code || 'LIVE_CHECK_FAILED', message: String(err.message).slice(0, 600) };
  console.error(`Real API checks failed: ${report.failure.message}`);
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  await mkdir('docs', { recursive: true });
  await writeFile('docs/live-result.json', JSON.stringify(report, null, 2) + '\n');
}
