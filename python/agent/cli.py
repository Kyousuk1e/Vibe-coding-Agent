"""Interactive HTTP client; /retry retains the original request ID."""

import uuid
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .jsonutil import dumps, loads


def chat(*, user="A", session_id=None, title="New conversation", base_url="http://127.0.0.1:8787"):
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("CLI 仅连接 http://127.0.0.1 上的本地 Agent 服务")

    def api(path, body=None):
        request = Request(base_url.rstrip("/") + path,
                          data=None if body is None else dumps(body).encode("utf-8"),
                          headers={"X-User-Id": user, "Content-Type": "application/json"})
        try:
            # The server bounds each model call and the number of calls.
            with urlopen(request, timeout=3600) as response:
                return loads(response.read(8 * 1024 * 1024))
        except HTTPError as exc:
            result = loads(exc.read(32768))
            raise ValueError(result.get("error", {}).get("message", f"HTTP {exc.code}")) from None

    session = api(f"/sessions/{session_id}") if session_id else api("/sessions", {"title": title})
    print(f"用户 {user} | Session {session['id']}\n续聊：python -m agent chat --user {user} --session {session['id']} --url {base_url.rstrip('/')}")
    print("/sessions 列表 | /use ID 切换 | /new 标题 新建 | /todos 待办 | /trace 日志 | /retry 重试 | /exit 退出")
    pending, last_trace = None, []
    while True:
        try:
            text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/exit":
            break
        try:
            if text == "/help":
                print("/retry 复用上次网络失败请求的 requestId；普通消息创建新请求。")
                continue
            if text == "/sessions":
                print(dumps(api("/sessions")["sessions"]))
                continue
            if text.startswith("/use "):
                session = api(f"/sessions/{text[5:].strip()}")
                pending, last_trace = None, []
                print(f"已切换 {session['id']}")
                continue
            if text == "/new" or text.startswith("/new "):
                session = api("/sessions", {"title": text[5:].strip() or "New conversation"})
                pending, last_trace = None, []
                print(f"已新建 {session['id']}")
                continue
            if text == "/todos":
                print(dumps(api(f"/sessions/{session['id']}")["todos"]))
                continue
            if text == "/trace":
                print(dumps(last_trace))
                continue
            if text == "/retry" and pending is None:
                print("没有网络失败的待重试请求。")
                continue
            if text != "/retry":
                pending = {"input": text, "requestId": str(uuid.uuid4())}
            result = api(f"/sessions/{session['id']}/messages", pending)
            pending, last_trace = None, result["trace"]
            replay = "; 幂等重放" if result["replayed"] else ""
            print(f"Agent > {result['answer']}\n[{result['status']}; {result['steps']} 次模型调用{replay}]")
        except Exception as exc:
            print(f"错误：{exc}")
