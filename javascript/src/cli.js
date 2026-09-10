import { createInterface } from 'node:readline/promises';
import { stdin, stdout } from 'node:process';
import { parseArgs } from 'node:util';
import { randomUUID } from 'node:crypto';

async function main() {
  const { values } = parseArgs({ options: { user: { type: 'string', default: 'A' }, session: { type: 'string' }, title: { type: 'string', default: '新会话' }, url: { type: 'string', default: `http://127.0.0.1:${process.env.PORT || 8787}` } } });
  async function api(path, data) {
    const res = await fetch(values.url + path, { method: data ? 'POST' : 'GET', headers: { 'X-User-Id': values.user, 'Content-Type': 'application/json' }, ...(data ? { body: JSON.stringify(data) } : {}) });
    const result = await res.json();
    if (!res.ok) throw new Error(result.error?.message || `HTTP ${res.status}`);
    return result;
  }
  let session = values.session ? await api(`/sessions/${values.session}`) : await api('/sessions', { title: values.title });
  console.log(`用户 ${values.user} | Session ${session.id}\n续聊命令：npm run chat -- --user ${values.user} --session ${session.id}`);
  console.log('/help 帮助 | /sessions 列表 | /use ID 切换 | /new 标题 新建 | /todos 待办 | /trace 日志 | /retry 重试网络请求 | /exit 退出');
  let lastTrace = [], pending = null;
  const rl = createInterface({ input: stdin, output: stdout });
  try {
    while (true) {
      const input = (await rl.question('你 > ')).trim();
      if (!input) continue;
      if (input === '/exit') break;
      try {
        if (input === '/help') { console.log('每个窗口默认新建 session；用 --session ID 或 /use ID 续聊。/retry 重发上次网络失败请求，复用 requestId 防止重复待办。/trace 显示最近调用日志。'); continue; }
        if (input === '/sessions') { console.log(JSON.stringify((await api('/sessions')).sessions, null, 2)); continue; }
        if (input.startsWith('/use ')) { session = await api(`/sessions/${input.slice(5).trim()}`); pending = null; lastTrace = []; console.log(`已切换 ${session.id}`); continue; }
        if (input === '/new' || input.startsWith('/new ')) { session = await api('/sessions', { title: input.slice(5).trim() || '新会话' }); pending = null; lastTrace = []; console.log(`已新建 ${session.id}`); continue; }
        if (input === '/todos') { console.log(JSON.stringify((await api(`/sessions/${session.id}`)).todos, null, 2)); continue; }
        if (input === '/trace') { console.log(JSON.stringify(lastTrace, null, 2)); continue; }
        if (input === '/retry' && !pending) { console.log('没有网络失败的待重试请求。'); continue; }
        if (input !== '/retry') pending = { input, requestId: randomUUID() };
        const result = await api(`/sessions/${session.id}/messages`, pending);
        pending = null; lastTrace = result.trace;
        console.log(`Agent > ${result.answer}\n[${result.status}; ${result.steps} 次模型调用${result.replayed ? '; 幂等重放' : ''}]`);
      } catch (err) { console.error(`错误：${err.message}`); }
    }
  } finally { rl.close(); }
}
main().catch(err => { console.error(`无法连接或读取会话：${err.message}\n请先在另一终端运行 npm start。`); process.exitCode = 1; });
