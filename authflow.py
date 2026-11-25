"""Sign-in management for the plugin's isolated Antigravity profile.

The OAuth flow is the agent's own native one: we spawn the unmodified binary,
authenticate over ACP, and surface whatever sign-in URL it prints. The plugin
never reads, stores or proxies tokens itself - they stay inside the agent's own
credential storage under the plugin's GEMINI_HOME.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

try:
    from .client import HermesAcpClient, spawn_session
    from .process import find_command, gemini_home, AntigravityNotFound
except ImportError:
    from client import HermesAcpClient, spawn_session
    from process import find_command, gemini_home, AntigravityNotFound


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


async def _with_agent(cwd: str, fn) -> int:
    client = HermesAcpClient(on_update=lambda s, u: None, on_permission=lambda *a: asyncio.sleep(0, result=None))
    try:
        async with spawn_session(client, cwd) as agent:
            stderr_task = asyncio.create_task(_echo_auth_lines(agent))
            try:
                return await fn(agent)
            finally:
                stderr_task.cancel()
    except Exception as exc:
        _eprint(f"[error] {exc}")
        return 2


async def _echo_auth_lines(agent: Any) -> None:
    try:
        async for line in agent.read_stderr_lines():
            if "http" in line:
                _eprint(f"[agent] {line}")
    except Exception:
        pass


def login(cwd: str, method: str = "oauth-personal") -> int:
    async def _do(agent: Any) -> int:
        methods = [getattr(m, "id", "") for m in agent.auth_methods]
        if method not in methods:
            _eprint(f"[auth] agent does not offer {method}; offered: {', '.join(methods) or '(none)'}")
            return 2
        _eprint("[auth] starting Google sign-in. If a browser does not open, use the URL printed above.")
        await agent.authenticate(method)
        _eprint("[auth] signed in. Credentials stay in the agent's own storage under:")
        _eprint(f"        {gemini_home()}")
        return 0

    return asyncio.run(_with_agent(cwd, _do))


def status(cwd: str) -> int:
    try:
        argv = find_command()
    except AntigravityNotFound as exc:
        print(f"antigravity-acp: {exc}")
        return 1
    home = gemini_home()
    print(f"antigravity-acp: command: {' '.join(argv)}")
    print(f"  profile: {home} ({'exists' if home.exists() else 'not created yet'})")
    token_marks = list(home.glob("antigravity-acp/*oauth*")) + list(home.glob("antigravity-acp/*token*")) if home.exists() else []
    print(f"  sign-in: {'credential files present' if token_marks else 'no stored sign-in found - run: hermes antigravity login'}")
    return 0


def logout() -> int:
    home = gemini_home()
    removed = False
    if home.exists():
        for pattern in ("antigravity-acp/*oauth*", "antigravity-acp/*token*", "antigravity-acp/acp_token.json"):
            for path in home.glob(pattern):
                path.unlink()
                removed = True
    print("Signed out of Antigravity for this profile." if removed else "No stored sign-in found.")
    return 0
