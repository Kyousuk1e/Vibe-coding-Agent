import { SessionStore } from './store.js';
import { ContextManager } from './context.js';
import { ChatClient } from './llm.js';
import { createTools } from './tools.js';
import { TraceWriter } from './trace.js';
import { AgentRuntime } from './runtime.js';

export function createApp(config, overrides = {}) {
  const store = overrides.store ?? new SessionStore({ dataDir: config.dataDir });
  const client = overrides.client ?? new ChatClient(config);
  const registry = overrides.registry ?? createTools({ maxResultChars: 16000 });
  const context = overrides.context ?? new ContextManager({ maxContextChars: config.maxContextChars });
  const runtime = new AgentRuntime({ store, client, registry, context, traceWriter: new TraceWriter(config.dataDir), maxSteps: config.maxSteps });
  return { store, runtime, registry };
}
