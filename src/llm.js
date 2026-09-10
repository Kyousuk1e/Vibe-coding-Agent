const MAX_RESPONSE_BYTES = 1_048_576;
const SAFE_ERROR = Symbol('sanitized LLM error');

function failure(code, message, status) {
  return Object.assign(new Error(message), { code, [SAFE_ERROR]: true, ...(status === undefined ? {} : { status }) });
}

function integer(value, name, min, max) {
  if (!Number.isInteger(value) || value < min || value > max) {
    throw failure('LLM_CONFIG', `${name} must be an integer between ${min} and ${max}.`);
  }
}

function endpoint(baseUrl) {
  let url;
  try { url = new URL(baseUrl); } catch { throw failure('LLM_CONFIG', 'Invalid LLM base URL.'); }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) || url.username || url.password || url.search || url.hash) {
    throw failure('LLM_CONFIG', 'LLM URL must use HTTPS (HTTP only for localhost), without credentials, query, or fragment.');
  }
  url.pathname = `${url.pathname.replace(/\/+$/, '')}/chat/completions`;
  return url.toString();
}

function aborted() { return failure('LLM_ABORTED', 'LLM request was cancelled.'); }

async function delay(milliseconds, signal) {
  if (signal?.aborted) throw aborted();
  await new Promise((resolve, reject) => {
    const done = () => { signal?.removeEventListener('abort', cancel); resolve(); };
    const timer = setTimeout(done, milliseconds);
    const cancel = () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); reject(aborted()); };
    signal?.addEventListener('abort', cancel, { once: true });
  });
}

function retryDelay(header, attempt) {
  if (header) {
    const seconds = Number(header);
    const milliseconds = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(header) - Date.now();
    if (Number.isFinite(milliseconds)) return Math.max(0, Math.min(milliseconds, 5000));
  }
  return Math.min(250 * 2 ** attempt, 2000);
}

async function readJSON(response) {
  const declaredSize = Number(response.headers.get('content-length'));
  if (declaredSize > MAX_RESPONSE_BYTES) {
    await response.body?.cancel().catch(() => {});
    throw failure('LLM_PROTOCOL', 'LLM response exceeded the size limit.');
  }
  if (!response.body) throw failure('LLM_PROTOCOL', 'LLM response body was empty.');
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_RESPONSE_BYTES) {
        await reader.cancel().catch(() => {});
        throw failure('LLM_PROTOCOL', 'LLM response exceeded the size limit.');
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); }
  catch { throw failure('LLM_PROTOCOL', 'LLM returned invalid JSON.'); }
}

/** A small real HTTP client; it contains no planning, tools, memory, or Agent framework. */
export class ChatClient {
  constructor({ apiKey, provider = 'qwen', baseUrl = provider === 'qwen' ? 'https://dashscope.aliyuncs.com/compatible-mode/v1' : 'https://api.openai.com/v1', model = provider === 'qwen' ? 'qwen-plus' : 'gpt-4.1-mini', timeoutMs = 30000, maxRetries = 2, fetchImpl = globalThis.fetch, maxOutputTokens = 1200 } = {}) {
    if (typeof apiKey !== 'string' || !apiKey.trim() || /[\r\n]/.test(apiKey)) throw failure('LLM_CONFIG', 'A nonempty server-side LLM API key is required.');
    if (typeof model !== 'string' || !model.trim() || model.length > 200) throw failure('LLM_CONFIG', 'A valid LLM model is required.');
    if (!['qwen', 'openai'].includes(provider)) throw failure('LLM_CONFIG', 'LLM provider must be qwen or openai.');
    integer(timeoutMs, 'timeoutMs', 1, 120000);
    integer(maxRetries, 'maxRetries', 0, 5);
    integer(maxOutputTokens, 'maxOutputTokens', 1, 128000);
    if (typeof fetchImpl !== 'function') throw failure('LLM_CONFIG', 'fetchImpl must be a function.');
    // Keep credentials non-enumerable so accidental JSON serialization cannot disclose them.
    Object.defineProperty(this, 'apiKey', { value: apiKey.trim(), enumerable: false });
    Object.assign(this, { endpoint: endpoint(baseUrl), model, provider, timeoutMs, maxRetries, fetchImpl, maxOutputTokens });
  }

  async complete({ messages, tools = [], signal } = {}) {
    if (!Array.isArray(messages) || !messages.length || !Array.isArray(tools)) throw failure('LLM_CONFIG', 'messages must be nonempty and tools must be an array.');
    const body = { model: this.model, messages, stream: false };
    if (this.provider === 'qwen') {
      body.max_tokens = this.maxOutputTokens;
      body.enable_thinking = false;
    } else { body.max_completion_tokens = this.maxOutputTokens; }
    if (tools.length) {
      body.tools = tools.map(tool => {
        if (this.provider !== 'qwen' || !tool?.function) return tool;
        const { strict: _strict, ...functionSchema } = tool.function;
        return { ...tool, function: functionSchema };
      });
      body.tool_choice = 'auto';
      body.parallel_tool_calls = false;
    }
    let encoded;
    try { encoded = JSON.stringify(body); } catch { throw failure('LLM_CONFIG', 'LLM request could not be serialized.'); }
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      if (signal?.aborted) throw aborted();
      const controller = new AbortController();
      let timedOut = false;
      const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, this.timeoutMs);
      const cancel = () => controller.abort();
      signal?.addEventListener('abort', cancel, { once: true });
      let retryAfter;
      let currentError;
      let retryable = false;
      try {
        const response = await this.fetchImpl(this.endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this.apiKey}` },
          body: encoded,
          signal: controller.signal,
          redirect: 'error'
        });
        if (!response.ok) {
          retryAfter = response.headers.get('retry-after');
          await response.body?.cancel().catch(() => {});
          retryable = response.status === 429 || response.status >= 500;
          const code = response.status === 401 || response.status === 403 ? 'LLM_AUTH' : response.status === 429 ? 'LLM_RATE_LIMIT' : 'LLM_HTTP';
          throw failure(code, `LLM request failed with HTTP ${response.status}.`, response.status);
        }
        return await readJSON(response);
      } catch (err) {
        if (signal?.aborted) throw aborted();
        if (timedOut) {
          currentError = failure('LLM_TIMEOUT', 'LLM request timed out.');
          retryable = true;
        } else if (err?.[SAFE_ERROR] === true) {
          currentError = err;
        } else {
          // Never copy a network error message or provider body: either could contain secrets.
          currentError = failure('LLM_NETWORK', 'LLM network request failed.');
          retryable = true;
        }
      } finally {
        clearTimeout(timeout);
        signal?.removeEventListener('abort', cancel);
      }
      if (!retryable || attempt === this.maxRetries) throw currentError;
      await delay(retryDelay(retryAfter, attempt), signal);
    }
  }
}
