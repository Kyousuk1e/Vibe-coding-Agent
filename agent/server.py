"""Small loopback HTTP server on one asyncio loop shared by all sessions."""

import asyncio
import re
from http import HTTPStatus
from urllib.parse import urlsplit

from .errors import AgentError
from .jsonutil import dumps, loads


async def dispatch(app, method, path, headers, raw_body):
    if "origin" in headers:
        return 403, {"error": {"code": "ORIGIN_DENIED", "message": "此接口仅用于本地 CLI"}}
    path = urlsplit(path).path
    if method == "GET" and path == "/health":
        return 200, {"ok": True}
    user_id = headers.get("x-user-id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", user_id):
        raise AgentError("BAD_INPUT", "需要有效 X-User-Id")

    def body():
        if not headers.get("content-type", "").lower().startswith("application/json"):
            raise AgentError("BAD_INPUT", "需要 application/json")
        try:
            value = loads(raw_body.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise AgentError("BAD_INPUT", "请求体必须为 JSON 对象") from None

    if method == "GET" and path == "/tools":
        return 200, {"tools": app.registry.schemas()}
    if path == "/sessions":
        if method == "GET":
            sessions = await app.store.list(user_id)
            return 200, {"sessions": [{k: s[k] for k in ("id", "title", "createdAt", "updatedAt")} for s in sessions]}
        if method == "POST":
            return 201, await app.store.create(user_id, body().get("title", "New conversation"))
    match = re.fullmatch(r"/sessions/([a-zA-Z0-9_-]{1,100})(/messages)?", path)
    if match and method == "GET" and not match[2]:
        session = await app.store.get(user_id, match[1])
        return 200, {k: v for k, v in session.items() if k != "completedRequests"}
    if match and match[2] and method == "POST":
        data = body()
        if "requestId" in data and not isinstance(data["requestId"], str):
            raise AgentError("BAD_INPUT", "requestId 必须为字符串，未指定时应省略该字段")
        result = await app.runtime.run(user_id=user_id, session_id=match[1],
                                       input=data.get("input"), request_id=data.get("requestId"))
        return 200, result
    return 404, {"error": {"code": "NOT_FOUND", "message": "接口不存在"}}


def error_response(exc):
    code = getattr(exc, "code", "INTERNAL")
    status = (400 if code in {"BAD_INPUT", "INVALID_ID", "INVALID_TITLE"} else
              404 if code == "SESSION_NOT_FOUND" else 409 if code == "REQUEST_CONFLICT" else 500)
    message = str(exc) if status < 500 else "服务端执行失败，请检查本地数据与配置。"
    return status, {"error": {"code": code, "message": message}}


async def handle_connection(app, reader, writer):
    try:
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            if len(header) > 32768:
                raise AgentError("BAD_INPUT", "请求头过大")
            lines = header.decode("latin-1").split("\r\n")
            pieces = lines[0].split()
            if len(pieces) != 3 or pieces[2] not in {"HTTP/1.0", "HTTP/1.1"}:
                raise AgentError("BAD_INPUT", "无效 HTTP 请求")
            method, path, _ = pieces
            headers = {}
            for line in lines[1:]:
                if not line:
                    continue
                name, colon, value = line.partition(":")
                name = name.lower()
                if not colon or not re.fullmatch(r"[a-z0-9-]+", name) or name in headers:
                    raise AgentError("BAD_INPUT", "无效或重复 HTTP 请求头")
                headers[name] = value.strip()
            if "transfer-encoding" in headers:
                raise AgentError("BAD_INPUT", "仅支持 Content-Length 请求体")
            length = headers.get("content-length", "0")
            if not re.fullmatch(r"[0-9]{1,8}", length) or int(length) > 32768:
                raise AgentError("BAD_INPUT", "请求体超过 32KB 或长度无效")
            raw_body = await asyncio.wait_for(reader.readexactly(int(length)), 15)
            status, result = await dispatch(app, method, path, headers, raw_body)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            status, result = error_response(AgentError("BAD_INPUT", "请求不完整或超时"))
        except Exception as exc:
            status, result = error_response(exc)
        encoded = dumps(result).encode("utf-8")
        response_header = (f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\n"
                           "Content-Type: application/json; charset=utf-8\r\n"
                           "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n"
                           f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n")
        writer.write(response_header.encode("ascii") + encoded)
        await writer.drain()
    except (ConnectionError, OSError):
        # A disconnected client can replay the saved request using its original ID.
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def start_server(app, port=8787, host="127.0.0.1"):
    if host != "127.0.0.1":
        raise AgentError("CONFIG", "本地演示服务只允许监听 127.0.0.1")
    return await asyncio.start_server(lambda r, w: handle_connection(app, r, w), host, port, limit=32768)


async def serve(config):
    from .app import create_app
    server = await start_server(create_app(config), config.port)
    print(f"Agent 已启动 http://127.0.0.1:{config.port} | {config.provider}/{config.model}\n"
          f"另开终端运行 python -m agent chat --user A --url http://127.0.0.1:{config.port}", flush=True)
    async with server:
        await server.serve_forever()
