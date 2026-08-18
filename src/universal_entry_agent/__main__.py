from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys

from .agent import UniversalEntryAgent
from .config import load_config


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="universal-entry-agent")
    parser.add_argument("--config", default=None, help="Path to config.json")
    sub = parser.add_subparsers(dest="command")

    ask = sub.add_parser("ask", help="Run one request")
    ask.add_argument("--session", default="default", help="Conversation session id")
    ask.add_argument("text", nargs="+", help="User request")

    chat = sub.add_parser("chat", help="Interactive chat mode")
    chat.add_argument("--session", default="default", help="Conversation session id")

    web = sub.add_parser("web", help="Start web chat server")
    web.add_argument("--host", default="127.0.0.1", help="Web server host")
    web.add_argument("--port", default=8765, type=int, help="Web server port")

    return parser


def _ask_quietly(
    agent: UniversalEntryAgent,
    text: str,
    session_id: str,
) -> str:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO(),
    ):
        return agent.ask(text, session_id=session_id)


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command not in {"ask", "chat", "web"}:
        parser.print_help()
        return 2

    config = load_config(args.config)
    if args.command == "web":
        import uvicorn

        from .web import create_app

        os.environ["UNIVERSAL_AGENT_WEB_BASE_URL"] = (
            f"http://{args.host}:{args.port}"
        )
        uvicorn.run(
            create_app(config),
            host=args.host,
            port=args.port,
            log_level="warning",
        )
        return 0

    agent = UniversalEntryAgent(config)
    try:
        if args.command == "ask":
            print(_ask_quietly(agent, " ".join(args.text), args.session))
            return 0

        if args.command == "chat":
            print(
                f"意图路由 Agent 已启动。session={args.session}。输入 exit 退出。",
            )
            while True:
                try:
                    text = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return 0
                if text.lower() in {"exit", "quit"}:
                    return 0
                if text:
                    print(_ask_quietly(agent, text, args.session))
            return 0
    finally:
        agent.close()

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
