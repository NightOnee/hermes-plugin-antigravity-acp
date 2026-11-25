"""`hermes antigravity` command tree."""

from __future__ import annotations

import asyncio
import os

try:
    from . import authflow
    from .repl import run_chat
except ImportError:
    import authflow
    from repl import run_chat


def setup_cli(parser) -> None:
    sub = parser.add_subparsers(dest="antigravity_action")

    chat = sub.add_parser(
        "chat",
        help="Open an interactive, user-driven chat with Antigravity",
        description=(
            "Interactive ACP chat. You type prompts directly; Antigravity owns "
            "its agent loop, history and tools. Hermes never generates prompts here."
        ),
    )
    chat.add_argument("--cwd", default=os.getcwd(), help="Working directory for the session")
    chat.add_argument("--session", help="Resume a previous session id")
    chat.add_argument("--mode", help="Permission mode id advertised by the agent")
    chat.add_argument("--auth-method", default="oauth-personal")

    login = sub.add_parser("login", help="Sign in with your Google account (native OAuth)")
    login.add_argument("--method", default="oauth-personal")

    sub.add_parser("status", help="Show binary and sign-in status")
    sub.add_parser("logout", help="Remove the stored sign-in for this plugin's profile")


def handle_cli(args) -> None:
    action = getattr(args, "antigravity_action", None) or "chat"
    if action == "chat":
        code = asyncio.run(
            run_chat(
                cwd=args.cwd,
                session_id=args.session,
                mode=args.mode,
                auth_method=args.auth_method,
            )
        )
    elif action == "login":
        code = authflow.login(cwd=os.getcwd(), method=args.method)
    elif action == "status":
        code = authflow.status(cwd=os.getcwd())
    elif action == "logout":
        code = authflow.logout()
    else:
        code = 2
    if code:
        raise SystemExit(code)
