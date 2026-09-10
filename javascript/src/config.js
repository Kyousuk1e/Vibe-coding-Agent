import { resolve } from 'node:path';

function integer(env, name, fallback, min, max) {
  const value = Number(env[name] ?? fallback);
  if (!Number.isInteger(value) || value < min || value > max) throw new Error(`${name} 必须在 ${min}–${max} 之间`);
  return value;
}
export function readConfig(env = process.env) {
  const provider = env.LLM_PROVIDER || 'qwen';
  if (!['qwen', 'openai'].includes(provider)) throw new Error('LLM_PROVIDER 应为 qwen 或 openai');
  const apiKey = provider === 'qwen' ? env.DASHSCOPE_API_KEY : env.OPENAI_API_KEY;
  if (!apiKey?.trim()) throw new Error(`请先在 .env 中配置 ${provider === 'qwen' ? 'DASHSCOPE_API_KEY' : 'OPENAI_API_KEY'}。不会回退到 mock LLM。`);
  return {
    provider, apiKey,
    baseUrl: env.LLM_BASE_URL || (provider === 'qwen' ? 'https://dashscope.aliyuncs.com/compatible-mode/v1' : 'https://api.openai.com/v1'),
    model: env.LLM_MODEL || (provider === 'qwen' ? 'qwen-plus' : 'gpt-4.1-mini'),
    port: integer(env, 'PORT', 8787, 1, 65535),
    dataDir: resolve(env.DATA_DIR || './data'),
    maxSteps: integer(env, 'MAX_STEPS', 8, 1, 30),
    maxContextChars: integer(env, 'MAX_CONTEXT_CHARS', 24000, 10000, 200000),
    timeoutMs: integer(env, 'LLM_TIMEOUT_MS', 30000, 100, 120000),
    maxRetries: integer(env, 'LLM_MAX_RETRIES', 2, 0, 3),
    maxOutputTokens: integer(env, 'MAX_OUTPUT_TOKENS', 1200, 64, 8192)
  };
}
