"""Run `python -m agent serve` or `python -m agent chat --user A`."""

import argparse
import asyncio
import sys


def main():
    parser = argparse.ArgumentParser(description="从零实现的 Python Agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="启动本地 HTTP 服务")
    cli = sub.add_parser("chat", help="打开一个聊天窗口")
    cli.add_argument("--user", default="A")
    cli.add_argument("--session", default=None)
    cli.add_argument("--title", default="New conversation")
    cli.add_argument("--url", default=None)
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .config import read_config
            from .server import serve
            asyncio.run(serve(read_config()))
        else:
            from .cli import chat
            from .config import read_client_url
            chat(user=args.user, session_id=args.session, title=args.title, base_url=args.url or read_client_url())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Config and startup exceptions expose only their safe code at this boundary.
        from .errors import AgentError
        print(str(exc) if isinstance(exc, AgentError) else "启动失败，请检查配置、端口和服务状态。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
