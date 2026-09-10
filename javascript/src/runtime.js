import { randomUUID, createHash } from 'node:crypto';
import { parseCompletion } from './parser.js';
import { SYSTEM_PROMPT } from './prompt.js';
import { preview } from './trace.js';

function error(code, message) { return Object.assign(new Error(message), { code }); }
export class AgentRuntime {
  constructor({ store, client, registry, context, traceWriter, maxSteps = 8, maxCallsPerStep = 4 }) {
    if (!Number.isInteger(maxSteps) || maxSteps < 1 || maxSteps > 30) throw error('CONFIG', 'maxSteps 必须在 1–30 之间');
    Object.assign(this, { store, client, registry, context, traceWriter, maxSteps, maxCallsPerStep });
  }

  async run({ userId, sessionId, input, requestId = randomUUID(), signal }) {
    if (typeof input !== 'string' || !input.trim() || input.length > 4000) throw error('BAD_INPUT', '输入应为 1–4000 字符');
    if (typeof requestId !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(requestId) || ['__proto__', 'constructor', 'prototype'].includes(requestId)) throw error('BAD_INPUT', 'requestId 格式无效');
    const inputHash = createHash('sha256').update(input).digest('hex');
    return this.store.withLock(userId, sessionId, async () => {
      const session = await this.store.get(userId, sessionId);
      session.completedRequests ??= {};
      if (Object.hasOwn(session.completedRequests, requestId)) {
        const old = session.completedRequests[requestId];
        if (old.inputHash !== inputHash) throw error('REQUEST_CONFLICT', '同一 requestId 不能用于不同输入');
        return { ...old.result, replayed: true };
      }
      // Work only on the loaded snapshot. Built-in local writes are committed with the turn.
      const originalTodos = structuredClone(session.todos);
      const currentMessages = [{ role: 'user', content: input }];
      const trace = [];
      const started = Date.now();
      const emit = (event, details = {}) => trace.push({ timestamp: new Date().toISOString(), requestId, sessionId, event, ...details });
      let answer = '', status = 'ok', decisionSummary = '', steps = 0;
      emit('run.start');
      try {
        for (let step = 1; step <= this.maxSteps; step++) {
          steps = step;
          signal?.throwIfAborted();
          const tools = this.registry.schemas();
          const built = await this.context.build(session, { systemPrompt: SYSTEM_PROMPT, tools, currentMessages });
          emit('context.ready', { step, ...built.stats });
          emit('llm.start', { step });
          const raw = await this.client.complete({ messages: built.messages, tools, signal });
          const parsed = parseCompletion(raw);
          decisionSummary = parsed.decisionSummary;
          const usage = {};
          for (const key of ['prompt_tokens', 'completion_tokens', 'total_tokens']) {
            if (Number.isSafeInteger(raw.usage?.[key]) && raw.usage[key] >= 0) usage[key] = raw.usage[key];
          }
          emit('llm.decision', { step, type: parsed.type, decisionSummary: preview(decisionSummary, 300), usage });
          if (parsed.type === 'final') {
            answer = parsed.answer;
            currentMessages.push({ role: 'assistant', content: answer });
            break;
          }
          if (parsed.calls.length > this.maxCallsPerStep) throw error('TOO_MANY_TOOLS', '模型一次请求了过多工具');
          currentMessages.push(parsed.assistantMessage);
          // Sequential execution gives todo mutations deterministic ordering.
          for (const call of parsed.calls) {
            const toolStart = Date.now();
            emit('tool.start', { step, callId: call.id, tool: call.name, arguments: preview(call.args ?? call.argumentError) });
            const result = call.argumentError
              ? { ok: false, error: { code: 'INVALID_ARGUMENTS', message: '工具参数不是合法 JSON 对象，请按 Schema 修正。' } }
              : await this.registry.execute(call.name, call.args, { session, signal });
            currentMessages.push({ role: 'tool', tool_call_id: call.id, content: JSON.stringify(result) });
            emit('tool.end', { step, callId: call.id, tool: call.name, ok: result.ok, result: preview(result), durationMs: Date.now() - toolStart });
          }
        }
        if (!answer) {
          status = 'max_steps';
          answer = `已达到本轮 ${this.maxSteps} 次模型调用上限，任务可能尚未完成。已成功执行的工具结果和待办已保存；可以继续追问。`;
          currentMessages.push({ role: 'assistant', content: answer });
        }
      } catch (err) {
        status = 'error';
        // Failed turns roll back local mutations. Do not retain success claims for rolled-back tools.
        session.todos = originalTodos;
        const code = typeof err.code === 'string' ? err.code : 'RUNTIME_ERROR';
        const safeMessages = {
          CONTEXT_LIMIT: '本轮上下文超过限制，请缩短输入或新建会话。',
          LLM_AUTH: 'LLM 认证失败，请检查服务端 API Key。',
          LLM_TIMEOUT: 'LLM 请求超时，请稍后重试。',
          LLM_PROTOCOL: 'LLM 返回格式无效，请重试。',
          LLM_TRUNCATED: 'LLM 输出被截断，请提高输出长度限制或缩小任务。'
        };
        answer = `${safeMessages[code] ?? '本轮执行失败，请检查配置或 trace 后重试。'} 本轮待办修改未提交。`;
        currentMessages.splice(1, currentMessages.length, { role: 'assistant', content: answer });
        emit('run.error', { code, ...(Number.isInteger(err.status) ? { httpStatus: err.status } : {}) });
      }
      session.messages.push(...currentMessages);
      emit('run.end', { status, steps, durationMs: Date.now() - started });
      const result = { requestId, sessionId, status, answer, decisionSummary: status === 'error' ? '' : decisionSummary, steps, trace, replayed: false };
      // Explicit order: Object.keys sorts integer-looking request IDs numerically.
      const ordered = Object.entries(session.completedRequests).sort((a, b) => (a[1].sequence ?? 0) - (b[1].sequence ?? 0));
      ordered.push([requestId, { inputHash, result }]);
      session.completedRequests = Object.fromEntries(ordered.slice(-20).map(([id, record], sequence) => [id, { ...record, sequence }]));
      await this.store.save(session);
      if (this.traceWriter) {
        try { await this.traceWriter.write(userId, sessionId, trace); }
        catch { result.traceWarning = '会话已保存，但 trace 文件写入失败。'; }
      }
      return result;
    });
  }
}
