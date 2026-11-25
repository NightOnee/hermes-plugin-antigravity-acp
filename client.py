"""ACP client core: spawn the official agent and speak JSON-RPC over stdio.

Hermes is a plain interactive ACP client here. The agent owns its history, tools,
planning and quota; this module only ever forwards what the user typed and what
the agent streamed back. There is intentionally no API for injecting system
prompts, replaying foreign history or projecting foreign tool schemas.
"""

from __future__ import annotations

import asyncio
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any, Awaitable, Callable

import acp
from acp.schema import (
    ClientCapabilities,
    FileSystemCapabilities,
    Implementation,
    RequestPermissionResponse,
    AllowedOutcome,
    DeniedOutcome,
)

try:
    from . import process as proc
except ImportError:
    import process as proc

try:
    PLUGIN_VERSION = pkg_version("hermes-plugin-antigravity-acp")
except PackageNotFoundError:
    PLUGIN_VERSION = "0.1.0"

# Honest identity. Never impersonate another client: model access is decided by
# Google for the account, not by what we call ourselves.
CLIENT_INFO = Implementation(
    name="hermes-antigravity-acp",
    title="Hermes Antigravity ACP Plugin",
    version=PLUGIN_VERSION,
)

PermissionCallback = Callable[
    [str, Any, list],  # session_id, tool_call, options
    Awaitable[str | None],  # chosen option_id, or None to dismiss
]
UpdateCallback = Callable[[str, Any], None]  # session_id, update


class HermesAcpClient:
    """Client-side protocol handler: surfaces agent events to the UI layer."""

    def __init__(
        self,
        on_update: UpdateCallback,
        on_permission: PermissionCallback,
    ) -> None:
        self._on_update = on_update
        self._on_permission = on_permission

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self._on_update(session_id, update)

    async def request_permission(
        self, options: list, session_id: str, tool_call: Any, **kwargs: Any
    ) -> RequestPermissionResponse:
        chosen = await self._on_permission(session_id, tool_call, options)
        if chosen is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=chosen)
        )


class AcpAgentSession:
    """One spawned `agy acp` process plus its JSON-RPC connection."""

    def __init__(self, client: HermesAcpClient, argv: list[str], env: dict[str, str], cwd: str) -> None:
        self._client = client
        self._argv = argv
        self._env = env
        self._cwd = cwd
        self.process: asyncio.subprocess.Process | None = None
        self.conn: Any = None
        self.initialize_response: Any = None

    async def __aenter__(self) -> "AcpAgentSession":
        self.process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        assert self.process.stdin and self.process.stdout
        self.conn = acp.connect_to_agent(self._client, self.process.stdin, self.process.stdout)
        self.initialize_response = await self.conn.initialize(
            protocol_version=acp.PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(
                fs=FileSystemCapabilities(read_text_file=False, write_text_file=False),
                terminal=False,
            ),
            client_info=CLIENT_INFO,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self.process is None:
            return
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

    @property
    def auth_methods(self) -> list:
        return list(getattr(self.initialize_response, "auth_methods", None) or [])

    async def authenticate(self, method_id: str = "oauth-personal") -> None:
        await self.conn.authenticate(method_id=method_id)

    async def new_session(self, cwd: str, mcp_servers: list | None = None) -> Any:
        return await self.conn.new_session(cwd=cwd, mcp_servers=mcp_servers or [])

    async def load_session(self, session_id: str, cwd: str, mcp_servers: list | None = None) -> Any:
        return await self.conn.load_session(session_id=session_id, cwd=cwd, mcp_servers=mcp_servers or [])

    async def prompt(self, session_id: str, text: str) -> Any:
        return await self.conn.prompt(session_id=session_id, prompt=[acp.text_block(text)])

    async def cancel(self, session_id: str) -> None:
        await self.conn.cancel(session_id=session_id)

    async def set_mode(self, session_id: str, mode_id: str) -> None:
        await self.conn.set_session_mode(mode_id=mode_id, session_id=session_id)

    async def read_stderr_lines(self):
        assert self.process and self.process.stderr
        async for line in self.process.stderr:
            yield line.decode(errors="replace").rstrip()


def spawn_session(client: HermesAcpClient, cwd: str) -> AcpAgentSession:
    return AcpAgentSession(client, proc.acp_argv(), proc.build_env(), cwd)
