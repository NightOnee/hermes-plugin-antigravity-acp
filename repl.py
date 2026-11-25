"""Interactive, user-driven chat with the Antigravity ACP agent.

Every prompt sent to the agent is typed here by the user, in the foreground.
Hermes (the agent core) is not involved in this loop at all: there is no system
prompt injection, no foreign history replay and no automated delegation. That is
what keeps consumer Google sign-in within the boundary Google described for
interactive ACP clients.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from typing import Any

from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AgentPlanUpdate,
    AvailableCommandsUpdate,
    CurrentModeUpdate,
    ToolCallProgress,
    ToolCallStart,
    UsageUpdate,
)

try:
    from .client import HermesAcpClient, spawn_session
except ImportError:
    from client import HermesAcpClient, spawn_session

AUTH_URL_MARKERS = ("accounts.google.com", "antigravity.google")


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


class StdinReader:
    """Background thread feeding stdin lines into an asyncio queue."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        def pump() -> None:
            while True:
                line = sys.stdin.readline()
                if line == "":
                    loop.call_soon_threadsafe(self.queue.put_nowait, None)
                    return
                loop.call_soon_threadsafe(self.queue.put_nowait, line.rstrip("\n"))

        threading.Thread(target=pump, daemon=True).start()

    async def next_line(self) -> str | None:
        return await self.queue.get()


def render_update(session_id: str, update: Any) -> None:
    if isinstance(update, AgentMessageChunk):
        content = update.content
        text = getattr(content, "text", None)
        if text:
            print(text, end="", flush=True)
    elif isinstance(update, AgentThoughtChunk):
        text = getattr(update.content, "text", "")
        if text and os.environ.get("ANTIGRAVITY_ACP_SHOW_THOUGHTS"):
            _eprint(f"\x1b[2m{update.content.text}\x1b[0m")
    elif isinstance(update, ToolCallStart):
        title = getattr(update, "title", "") or "tool call"
        kind = getattr(update, "kind", "") or ""
        _eprint(f"\n[tool] {title} ({kind})")
    elif isinstance(update, ToolCallProgress):
        status = getattr(update, "status", None)
        if status in ("completed", "failed"):
            title = getattr(update, "title", "") or "tool"
            _eprint(f"[tool:{status}] {title}")
    elif isinstance(update, AgentPlanUpdate):
        entries = getattr(update, "entries", None) or []
        if entries and os.environ.get("ANTIGRAVITY_ACP_SHOW_PLAN"):
            _eprint("[plan] " + "; ".join(getattr(e, "content", "") for e in entries))
    elif isinstance(update, (CurrentModeUpdate, AvailableCommandsUpdate, UsageUpdate)):
        pass


HELP = """\
Commands:
  /quit, /exit     end the session
  /new             start a fresh Antigravity session
  /cancel          cancel the running turn
  /mode <id>       switch permission mode (use the ids the agent advertised)
  /help            this help
Anything else is sent verbatim to Antigravity as your prompt.\
"""


async def _permission_prompt(reader: StdinReader, session_id: str, tool_call: Any, options: list) -> str | None:
    title = getattr(tool_call, "title", "") or "action"
    kind = getattr(tool_call, "kind", "") or ""
    _eprint(f"\n[permission] Antigravity asks to run: {title} ({kind})")
    for i, opt in enumerate(options, 1):
        _eprint(f"  {i}. {getattr(opt, 'name', '')} [{getattr(opt, 'kind', '')}]")
    _eprint("Pick a number, or press Enter to reject: ")
    line = await reader.next_line()
    if line is None:
        return None
    line = line.strip()
    if line.isdigit() and 1 <= int(line) <= len(options):
        return getattr(options[int(line) - 1], "option_id", None)
    for opt in options:
        if line and line == getattr(opt, "option_id", None):
            return line
    return None


async def run_chat(
    cwd: str,
    session_id: str | None = None,
    mode: str | None = None,
    auth_method: str = "oauth-personal",
) -> int:
    loop = asyncio.get_running_loop()
    reader = StdinReader()
    reader.start(loop)

    permission_in_progress = False

    async def on_permission(sid: str, tool_call: Any, options: list) -> str | None:
        nonlocal permission_in_progress
        if permission_in_progress:
            return None
        permission_in_progress = True
        try:
            return await _permission_prompt(reader, sid, tool_call, options)
        finally:
            permission_in_progress = False

    client = HermesAcpClient(on_update=render_update, on_permission=on_permission)

    try:
        async with spawn_session(client, cwd) as agent:
            stderr_task = asyncio.create_task(_relay_stderr(agent))

            methods = [getattr(m, "id", "") for m in agent.auth_methods]
            if auth_method in methods:
                _eprint(f"[auth] authenticating with {auth_method} (completes in your browser if needed)…")
                try:
                    await agent.authenticate(auth_method)
                except Exception as exc:
                    _eprint(f"[auth] failed: {exc}")
                    _eprint("[auth] run `hermes antigravity login` to sign in interactively first.")
                    return 2
            elif methods:
                _eprint(f"[auth] note: {auth_method} not offered; agent offers: {', '.join(methods)}")

            try:
                if session_id:
                    session = await agent.load_session(session_id=session_id, cwd=cwd)
                else:
                    session = await agent.new_session(cwd=cwd)
            except Exception as exc:
                _eprint(f"[session] failed to start: {exc}")
                return 2

            sid = getattr(session, "session_id", None) or getattr(session, "id", None)
            modes = getattr(session, "modes", None)
            available = getattr(modes, "available_modes", None) or []
            if available:
                ids = ", ".join(getattr(m, "id", "?") for m in available)
                _eprint(f"[session] {sid} — modes: {ids}")
            else:
                _eprint(f"[session] {sid}")
            if mode:
                try:
                    await agent.set_mode(sid, mode)
                except Exception as exc:
                    _eprint(f"[mode] failed: {exc}")

            _eprint(HELP)
            current_session = sid

            while True:
                print("\nhermes-agy> ", end="", flush=True)
                line = await reader.next_line()
                if line is None:
                    _eprint("\n[bye]")
                    return 0
                text = line.strip()
                if not text:
                    continue
                if text in ("/quit", "/exit"):
                    return 0
                if text == "/help":
                    _eprint(HELP)
                    continue
                if text == "/new":
                    session = await agent.new_session(cwd=cwd)
                    current_session = getattr(session, "session_id", None) or getattr(session, "id", None)
                    _eprint(f"[session] new: {current_session}")
                    continue
                if text.startswith("/mode "):
                    try:
                        await agent.set_mode(current_session, text.split(None, 1)[1])
                        _eprint(f"[mode] set to {text.split(None, 1)[1]}")
                    except Exception as exc:
                        _eprint(f"[mode] failed: {exc}")
                    continue
                if text == "/cancel":
                    await agent.cancel(current_session)
                    continue

                print()  # move off the prompt line before streaming
                try:
                    response = await agent.prompt(current_session, text)
                except Exception as exc:
                    _eprint(f"\n[error] {exc}")
                    continue
                stop = getattr(response, "stop_reason", None)
                print()
                if stop and stop != "end_turn":
                    _eprint(f"[turn ended: {stop}]")

    except Exception as exc:
        _eprint(f"[fatal] {exc}")
        return 2


async def _relay_stderr(agent: Any) -> None:
    try:
        async for line in agent.read_stderr_lines():
            if any(marker in line for marker in AUTH_URL_MARKERS) or "http" in line:
                _eprint(f"[agent] {line}")
            elif os.environ.get("ANTIGRAVITY_ACP_DEBUG"):
                _eprint(f"[agent:stderr] {line}")
    except Exception:
        pass
