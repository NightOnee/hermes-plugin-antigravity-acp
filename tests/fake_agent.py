"""Fake ACP agent over stdio, used by the test suite in place of `agy`.

Implements just enough of the agent side of the protocol to exercise the
plugin's client: initialize/authenticate/session/prompt streaming/permission.
"""

from __future__ import annotations

import asyncio
import sys

import acp
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AuthMethodAgent,
    AuthenticateResponse,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    TextContentBlock,
    ToolCallUpdate,
)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class FakeAgent:
    def __init__(self) -> None:
        self.conn = None
        self.client_info = None

    def on_connect(self, conn) -> None:
        self.conn = conn

    async def initialize(self, protocol_version: int, client_capabilities=None, client_info=None, **kwargs):
        self.client_info = client_info
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(name="Fake Antigravity ACP Server", version="0.0.0-test"),
            auth_methods=[
                AuthMethodAgent(id="oauth-personal", name="Google account", description="fake"),
                AuthMethodAgent(id="gemini-api-key", name="Gemini API key", description="fake"),
            ],
        )

    async def authenticate(self, method_id: str, **kwargs):
        log(f"authenticate:{method_id}")
        return AuthenticateResponse()

    async def new_session(self, cwd: str, mcp_servers=None, **kwargs):
        return NewSessionResponse(session_id="fake-session-1")

    async def load_session(self, cwd: str, session_id: str, mcp_servers=None, **kwargs):
        return NewSessionResponse(session_id=session_id)

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs):
        return None

    async def cancel(self, session_id: str, **kwargs):
        return None

    async def prompt(self, prompt: list, session_id: str, **kwargs):
        text = "".join(getattr(block, "text", "") for block in prompt)

        if "permission-test" in text:
            options = [
                PermissionOption(kind="allow_once", name="Allow once", option_id="allow"),
                PermissionOption(kind="reject_once", name="Reject", option_id="reject"),
            ]
            tool_call = ToolCallUpdate(
                tool_call_id="tc-1",
                title="Run fake command",
                kind="execute",
                session_update="tool_call_update",
            )
            response = await self.conn.request_permission(
                options=options, session_id=session_id, tool_call=tool_call
            )
            outcome = getattr(response.outcome, "outcome", "?")
            await self._chunk(session_id, f"permission outcome: {outcome}")

        for piece in ("echo: ", text):
            await self._chunk(session_id, piece)
        return PromptResponse(stop_reason="end_turn")

    async def _chunk(self, session_id: str, text: str) -> None:
        await self.conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
            ),
        )


if __name__ == "__main__":
    asyncio.run(acp.run_agent(FakeAgent()))
