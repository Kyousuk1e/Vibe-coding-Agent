import { randomUUID } from 'node:crypto';
import { ToolError, ToolRegistry } from './registry.js';

const schema = properties => ({ type: 'object', properties, required: Object.keys(properties), additionalProperties: false });
const text = (maxLength, extra = {}) => ({ type: 'string', minLength: 1, maxLength, ...extra });
const nullableText = maxLength => ({ type: ['string', 'null'], minLength: 1, maxLength });
const bad = message => { throw new ToolError('INVALID_ARGUMENTS', message); };

/** Small expression grammar, deliberately without identifiers, property access, calls, or eval. */
export function calculate(expression) {
  if (typeof expression !== 'string' || expression.length > 500) bad('Expression must contain at most 500 characters');
  const tokens = [];
  const pattern = /\s*(?:(\d+(?:\.\d*)?|\.\d+)([eE][+-]?\d+)?|(\*\*|[+\-*/%()]))/gy;
  let offset = 0;
  while (offset < expression.length) {
    if (!expression.slice(offset).trim()) break;
    pattern.lastIndex = offset;
    const match = pattern.exec(expression);
    if (!match) bad('Expression contains unsupported syntax');
    tokens.push(match[3] ?? Number(match[1] + (match[2] ?? '')));
    offset = pattern.lastIndex;
    if (tokens.length > 200) bad('Expression has too many tokens');
  }
  let position = 0;
  let depth = 0;
  const finite = value => {
    if (!Number.isFinite(value)) bad('Calculation must produce a finite real number');
    return Object.is(value, -0) ? 0 : value;
  };
  const peek = () => tokens[position];
  function nested(fn) {
    if (++depth > 32) bad('Expression nesting exceeds 32 levels');
    try { return fn(); } finally { depth--; }
  }
  function primary() {
    const token = tokens[position++];
    if (typeof token === 'number') return finite(token);
    if (token !== '(') bad('Expected a number or parenthesis');
    const value = nested(additive);
    if (tokens[position++] !== ')') bad('Missing closing parenthesis');
    return value;
  }
  function power() {
    const left = primary();
    if (peek() !== '**') return left;
    position++;
    return finite(left ** nested(unary));
  }
  function unary() {
    if (peek() === '+' || peek() === '-') {
      const sign = tokens[position++] === '-' ? -1 : 1;
      return finite(sign * nested(unary));
    }
    return power();
  }
  function multiplicative() {
    let value = unary();
    while (['*', '/', '%'].includes(peek())) {
      const operator = tokens[position++];
      const right = unary();
      if ((operator === '/' || operator === '%') && right === 0) bad('Division by zero is not allowed');
      value = finite(operator === '*' ? value * right : operator === '/' ? value / right : value % right);
    }
    return value;
  }
  function additive() {
    let value = multiplicative();
    while (peek() === '+' || peek() === '-') {
      const operator = tokens[position++];
      const right = multiplicative();
      value = finite(operator === '+' ? value + right : value - right);
    }
    return value;
  }
  const result = additive();
  if (position !== tokens.length) bad('Unexpected token in expression');
  return result;
}

const CORPUS = [
  { id: 'agent-loop', title: 'Agent 基本循环', text: 'Agent 接收用户输入，由 LLM 根据工具 Schema 决定调用工具或直接回复。将工具结果加入上下文，继续循环，直到最终答案或达到最大轮次。' },
  { id: 'session-memory', title: 'Session 与 Memory', text: '同一用户的不同窗口使用不同 session_id。每个 session 独立保存消息、摘要与待办；追问前读取该 session 的历史与工具状态。' },
  { id: 'context-compression', title: 'Context 上下文压缩', text: '长对话压缩旧轮次为摘要，保留最近完整工具调用链和用户原话。摘要提供背景，待办等结构化数据以当前 session 状态为准。' },
  { id: 'weekly-report', title: '周报模板', text: '周报可以包含：本周完成、关键成果、遇到的问题、下周计划。先收集事实，再生成周报，避免编造进度。' },
];
const WEATHER = [
  { city: '上海', aliases: ['上海', 'shanghai'], condition: '小雨', temperatureC: 24 },
  { city: '北京', aliases: ['北京', 'beijing'], condition: '晴', temperatureC: 27 },
  { city: '杭州', aliases: ['杭州', 'hangzhou'], condition: '多云', temperatureC: 26 },
  { city: '深圳', aliases: ['深圳', 'shenzhen'], condition: '阵雨', temperatureC: 29 },
  { city: '广州', aliases: ['广州', 'guangzhou'], condition: '多云', temperatureC: 30 },
];

export function createTools(options) {
  return new ToolRegistry(options)
    .register({
      name: 'calculator',
      description: '安全计算数学表达式，支持 + - * / % **、括号、小数、科学计数法；返回 JavaScript 浮点数结果。',
      parameters: schema({ expression: text(500) }),
      execute: ({ expression }) => ({ expression, result: calculate(expression) }),
    })
    .register({
      name: 'search',
      description: 'MOCK 搜索：仅搜索本地演示语料（Agent 循环、Session、上下文压缩、周报），不访问互联网，也不提供实时事实。',
      parameters: schema({ query: text(200) }),
      execute: ({ query }) => {
        const terms = query.toLowerCase().match(/[a-z0-9_]+|[\p{Script=Han}]/gu) ?? [];
        const scored = CORPUS.map(item => ({ item, score: terms.filter(term => `${item.title} ${item.text}`.toLowerCase().includes(term)).length }));
        return { mock: true, source: 'local-demo-corpus', query, results: scored.filter(row => row.score > 0).sort((a, b) => b.score - a.score).slice(0, 3).map(row => ({ ...row.item })) };
      },
    })
    .register({
      name: 'todo',
      description: '管理当前 session 的独立待办，最多 20 项、每项 200 字，另有总记忆容量限制。add 需要 text、id=null；list 两者均为 null；complete/remove 需要 id、text=null。id 从 list/add 返回。',
      parameters: schema({
        action: { type: 'string', enum: ['add', 'list', 'complete', 'remove'] },
        id: nullableText(64),
        text: nullableText(200),
      }),
      execute: ({ action, id, text: title }, { session, signal }) => {
        signal?.throwIfAborted();
        if (!session || typeof session !== 'object' || Array.isArray(session)) throw new ToolError('SESSION_REQUIRED', 'Todo requires a session');
        if (!Array.isArray(session.todos)) throw new ToolError('INVALID_SESSION', 'Session todos must be an array');
        if (action === 'add') {
          const normalizedTitle = title?.replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
          if (id !== null || !normalizedTitle) bad('Add requires nonempty text and id=null');
          if (session.todos.length >= 20) throw new ToolError('TODO_LIMIT', 'A session may contain at most 20 todos');
          const item = { id: randomUUID(), text: normalizedTitle, done: false, createdAt: new Date().toISOString() };
          // Todos occur in quoted memory and sometimes a quoted tool result. Bound
          // the actual escaped representation, not just visible title lengths.
          const candidate = [...session.todos, item];
          if (JSON.stringify(JSON.stringify(candidate)).length > 6500) {
            throw new ToolError('TODO_MEMORY_LIMIT', 'Todo memory is full; shorten the new text or remove existing todos');
          }
          session.todos.push(item);
          return { action, todo: { ...item } };
        }
        if (action === 'list') {
          if (id !== null || title !== null) bad('List requires id=null and text=null');
          return { action, todos: session.todos.map(item => ({ ...item })) };
        }
        if (id === null || title !== null) bad('Complete/remove requires id and text=null');
        const index = session.todos.findIndex(item => item.id === id);
        if (index < 0) throw new ToolError('TODO_NOT_FOUND', 'Todo was not found in the current session');
        if (action === 'complete') session.todos[index].done = true;
        const item = action === 'remove' ? session.todos.splice(index, 1)[0] : session.todos[index];
        return { action, todo: { ...item } };
      },
    })
    .register({
      name: 'weather',
      description: 'MOCK 天气：返回上海、北京、杭州、深圳、广州的固定演示天气，不代表真实天气；请在回复中明确标注模拟数据。',
      parameters: schema({ city: text(80) }),
      execute: ({ city }) => {
        const normalized = city.trim().toLowerCase().replace(/市$/, '');
        const item = WEATHER.find(row => row.aliases.includes(normalized));
        if (!item) throw new ToolError('CITY_NOT_SUPPORTED', 'Mock weather supports 上海、北京、杭州、深圳、广州 only');
        return { mock: true, source: 'fixed-demo-data', city: item.city, condition: item.condition, temperatureC: item.temperatureC, notice: '固定模拟数据，并非实时天气' };
      },
    });
}
