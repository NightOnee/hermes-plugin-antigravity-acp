"""Tests run against the fake ACP agent in tests/fake_agent.py."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
from client import HermesAcpClient, spawn_session  # noqa: E402
from process import build_env, acp_argv, gemini_home  # noqa: E402

FAKE_AGENT = str(PLUGIN_ROOT / "tests" / "fake_agent.py")


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTIGRAVITY_ACP_COMMAND", f"{sys.executable} {FAKE_AGENT}")
    monkeypatch.setenv("ANTIGRAVITY_ACP_HOME", str(tmp_path / "state"))
    return tmp_path


def run(coro):
    return asyncio.run(coro)


def test_env_isolation(fake_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "leak-me-not")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/somewhere.json")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ambient-project")
    env = build_env()
    assert "GEMINI_API_KEY" not in env
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "GOOGLE_CLOUD_PROJECT" not in env
    assert env["GEMINI_HOME"] == str(gemini_home())
    assert env["AGY_ACP_FORCE_FILE_STORAGE"] == "1"
    assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "1"


def test_argv_shape(fake_env):
    argv = acp_argv()
    joined = " ".join(argv)
    assert "acp" in argv
    assert "--gemini-dir" in argv
    assert str(gemini_home()) in argv
    assert "antigravity-acp" in joined


def test_initialize_auth_prompt_echo(fake_env, tmp_path):
    updates = []

    async def scenario():
        client = HermesAcpClient(
            on_update=lambda sid, update: updates.append(update),
            on_permission=_deny_permission,
        )
        async with spawn_session(client, str(tmp_path)) as agent:
            info = agent.initialize_response
            assert info.agent_info.name == "Fake Antigravity ACP Server"
            method_ids = [m.id for m in agent.auth_methods]
            assert "oauth-personal" in method_ids

            await agent.authenticate("oauth-personal")
            session = await agent.new_session(cwd=str(tmp_path))
            sid = session.session_id
            assert sid == "fake-session-1"

            response = await agent.prompt(sid, "hello antigravity")
            assert response.stop_reason == "end_turn"
        return sid

    run(scenario())
    text = "".join(getattr(u.content, "text", "") for u in updates if hasattr(u, "content"))
    assert "echo: hello antigravity" in text


def test_permission_flow_selected(fake_env, tmp_path):
    seen_options = []

    async def allow_first(session_id, tool_call, options):
        seen_options.extend(options)
        return options[0].option_id

    async def scenario():
        updates = []
        client = HermesAcpClient(
            on_update=lambda sid, update: updates.append(update),
            on_permission=allow_first,
        )
        async with spawn_session(client, str(tmp_path)) as agent:
            await agent.authenticate("oauth-personal")
            session = await agent.new_session(cwd=str(tmp_path))
            response = await agent.prompt(session.session_id, "please permission-test this")
            assert response.stop_reason == "end_turn"
            return updates

    updates = run(scenario())
    assert len(seen_options) == 2
    assert seen_options[0].name == "Allow once"
    text = "".join(getattr(u.content, "text", "") for u in updates if hasattr(u, "content"))
    assert "permission outcome: selected" in text


def test_permission_flow_cancelled(fake_env, tmp_path):
    async def scenario():
        updates = []
        client = HermesAcpClient(
            on_update=lambda sid, update: updates.append(update),
            on_permission=_deny_permission,
        )
        async with spawn_session(client, str(tmp_path)) as agent:
            await agent.authenticate("oauth-personal")
            session = await agent.new_session(cwd=str(tmp_path))
            await agent.prompt(session.session_id, "permission-test")
            return updates

    updates = run(scenario())
    text = "".join(getattr(u.content, "text", "") for u in updates if hasattr(u, "content"))
    assert "permission outcome: cancelled" in text


def test_register_only_cli_command():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "antigravity_acp_pkg",
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    calls = {"tools": 0, "hooks": 0, "cli": []}

    class FakeCtx:
        def register_tool(self, **kwargs):
            calls["tools"] += 1

        def register_hook(self, *args, **kwargs):
            calls["hooks"] += 1

        def register_cli_command(self, name, **kwargs):
            calls["cli"].append(name)

        def register_command(self, *args, **kwargs):
            calls["hooks"] += 1

    plugin.register(FakeCtx())
    assert calls["cli"] == ["antigravity"]
    # Compliance invariant: no model tools, no automation hooks, ever.
    assert calls["tools"] == 0
    assert calls["hooks"] == 0


async def _deny_permission(session_id, tool_call, options):
    return None
