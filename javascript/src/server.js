import { createServer } from 'node:http';
import { pathToFileURL } from 'node:url';
import { createApp } from './app.js';
import { readConfig } from './config.js';

function send(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  res.end(JSON.stringify(body));
}
async function body(req) {
  if (!req.headers['content-type']?.startsWith('application/json')) throw Object.assign(new Error('需要 application/json'), { code: 'BAD_INPUT' });
  const chunks = []; let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 32768) throw Object.assign(new Error('请求体超过 32KB'), { code: 'BAD_INPUT' });
    chunks.push(chunk);
  }
  try {
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error();
    return value;
  } catch { throw Object.assign(new Error('请求体必须为 JSON 对象'), { code: 'BAD_INPUT' }); }
}
export function createAgentServer({ store, runtime, registry }) {
  const server = createServer(async (req, res) => {
    try {
      // CLI-only loopback API: no CORS, no browser-origin mutations or remote binding.
      if (req.headers.origin) return send(res, 403, { error: { code: 'ORIGIN_DENIED', message: '此接口仅用于本地 CLI' } });
      const url = new URL(req.url, 'http://127.0.0.1');
      if (req.method === 'GET' && url.pathname === '/health') return send(res, 200, { ok: true });
      const userId = req.headers['x-user-id'];
      if (typeof userId !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(userId)) return send(res, 400, { error: { code: 'BAD_INPUT', message: '需要有效 X-User-Id' } });
      if (req.method === 'GET' && url.pathname === '/tools') return send(res, 200, { tools: registry.schemas() });
      if (url.pathname === '/sessions') {
        if (req.method === 'GET') return send(res, 200, { sessions: (await store.list(userId)).map(({ id, title, createdAt, updatedAt }) => ({ id, title, createdAt, updatedAt })) });
        if (req.method === 'POST') {
          const data = await body(req);
          return send(res, 201, await store.create(userId, data.title));
        }
      }
      const match = url.pathname.match(/^\/sessions\/([a-zA-Z0-9_-]{1,100})(\/messages)?$/);
      if (match && req.method === 'GET' && !match[2]) {
        const session = await store.get(userId, match[1]);
        const { completedRequests, ...publicSession } = session;
        return send(res, 200, publicSession);
      }
      if (match?.[2] && req.method === 'POST') {
        const data = await body(req);
        const result = await runtime.run({ userId, sessionId: match[1], input: data.input, requestId: data.requestId });
        return send(res, 200, result);
      }
      return send(res, 404, { error: { code: 'NOT_FOUND', message: '接口不存在' } });
    } catch (err) {
      const code = err.code || 'INTERNAL';
      const status = ['BAD_INPUT', 'INVALID_ID', 'INVALID_TITLE'].includes(code) ? 400 : code === 'SESSION_NOT_FOUND' ? 404 : code === 'REQUEST_CONFLICT' ? 409 : 500;
      const message = status < 500 ? err.message : '服务端执行失败，请检查本地数据与配置。';
      send(res, status, { error: { code, message } });
    }
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const config = readConfig();
    const server = createAgentServer(createApp(config));
    server.on('error', err => { console.error(`服务启动失败：${err.code || 'UNKNOWN'}`); process.exitCode = 1; });
    server.listen(config.port, '127.0.0.1', () => console.log(`Agent 已启动 http://127.0.0.1:${config.port} | ${config.provider}/${config.model}\n另开终端运行 npm run chat -- --user A`));
    for (const event of ['SIGINT', 'SIGTERM']) process.on(event, () => server.close());
  } catch (err) { console.error(err.message); process.exitCode = 1; }
}
