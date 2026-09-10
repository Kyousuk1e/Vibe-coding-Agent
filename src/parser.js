function failure(code, message) { return Object.assign(new Error(message), { code }); }
const protocol = () => failure('LLM_PROTOCOL', 'LLM completion has an invalid structure.');
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function parseContent(content) {
  if (content === null || content === undefined) return { answer: '', decisionSummary: '' };
  if (typeof content !== 'string' || content.length > 128000) throw protocol();
  const text = content.trim();
  if (!text) return { answer: '', decisionSummary: '' };
  const fence = text.match(/^```(?:json)?\s*\n?([\s\S]*?)\n?```$/i);
  const candidate = fence ? fence[1].trim() : text;
  if (candidate.startsWith('{')) {
    let parsed;
    try { parsed = JSON.parse(candidate); }
    catch {
      // An attempted response envelope must be well formed. Ordinary prose remains valid.
      if (fence || /["'](?:decision_summary|answer)["']\s*:/.test(candidate)) throw protocol();
      return { answer: text, decisionSummary: '直接回答用户。' };
    }
    if (isObject(parsed) && ('answer' in parsed || 'decision_summary' in parsed)) {
      if (typeof parsed.answer !== 'string' || !parsed.answer.trim() || (parsed.decision_summary !== undefined && typeof parsed.decision_summary !== 'string')) throw protocol();
      return { answer: parsed.answer.trim(), decisionSummary: (parsed.decision_summary ?? '直接回答用户。').trim().slice(0, 300) };
    }
  }
  return { answer: text, decisionSummary: '直接回答用户。' };
}

/** Parse the native protocol. Hidden reasoning fields are deliberately never read or retained. */
export function parseCompletion(response) {
  if (!isObject(response) || !Array.isArray(response.choices) || !response.choices.length) throw protocol();
  const choice = response.choices[0];
  if (!isObject(choice)) throw protocol();
  if (choice.finish_reason === 'length') throw failure('LLM_TRUNCATED', 'LLM completion was truncated.');
  if (choice.finish_reason === 'content_filter') throw failure('LLM_CONTENT_FILTER', 'LLM response was filtered.');
  const message = choice.message;
  if (!isObject(message) || (message.role !== undefined && message.role !== 'assistant')) throw protocol();
  if (message.function_call !== undefined) throw protocol();
  if (message.tool_calls !== undefined && message.tool_calls !== null && !Array.isArray(message.tool_calls)) throw protocol();
  const nativeCalls = message.tool_calls ?? [];
  if (nativeCalls.length) {
    if (nativeCalls.length > 32 || (message.content !== null && message.content !== undefined && (typeof message.content !== 'string' || message.content.length > 128000))) throw protocol();
    const ids = new Set();
    const calls = [];
    const toolCalls = [];
    for (const call of nativeCalls) {
      if (!isObject(call) || call.type !== 'function' || typeof call.id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(call.id) || ids.has(call.id) || !isObject(call.function)) throw protocol();
      const { name, arguments: rawArguments } = call.function;
      if (typeof name !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(name) || typeof rawArguments !== 'string' || rawArguments.length > 65536) throw protocol();
      ids.add(call.id);
      let args, argumentError;
      try {
        args = JSON.parse(rawArguments);
        if (!isObject(args)) { args = undefined; argumentError = 'Tool arguments must be a JSON object.'; }
      } catch { argumentError = 'Tool arguments contain invalid JSON.'; }
      calls.push({ id: call.id, name, args, ...(argumentError ? { argumentError } : {}) });
      // Even invalid JSON must stay byte-for-byte intact for a valid assistant/tool pairing.
      toolCalls.push({ id: call.id, type: 'function', function: { name, arguments: rawArguments } });
    }
    return {
      type: 'tools',
      decisionSummary: `调用工具：${calls.map(call => call.name).join('、')}`.slice(0, 300),
      calls,
      answer: '',
      assistantMessage: { role: 'assistant', content: message.content ?? null, tool_calls: toolCalls }
    };
  }
  if (choice.finish_reason === 'tool_calls') throw protocol();
  let parsed;
  if (message.refusal !== undefined && message.refusal !== null) {
    if (typeof message.refusal !== 'string' || !message.refusal.trim() || message.refusal.length > 128000) throw protocol();
    parsed = { answer: message.refusal.trim(), decisionSummary: '模型拒绝了该请求。' };
  } else { parsed = parseContent(message.content); }
  if (!parsed.answer) throw protocol();
  return { type: 'final', ...parsed, calls: [], assistantMessage: { role: 'assistant', content: parsed.answer } };
}
