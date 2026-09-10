import { mkdir, appendFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

export class TraceWriter {
  constructor(dataDir) { this.dir = join(dataDir, 'traces'); }
  async write(userId, sessionId, events) {
    await mkdir(this.dir, { recursive: true });
    const name = createHash('sha256').update(JSON.stringify([userId, sessionId])).digest('hex');
    await appendFile(join(this.dir, `${name}.jsonl`), events.map(e => JSON.stringify(e)).join('\n') + '\n', { mode: 0o600 });
  }
}

export function preview(value, limit = 1200) {
  const str = typeof value === 'string' ? value : (JSON.stringify(value) ?? 'null');
  return str.length > limit ? str.slice(0, limit) + '…[truncated]' : str;
}
