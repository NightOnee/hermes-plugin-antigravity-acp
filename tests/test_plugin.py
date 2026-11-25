"""Backend tests run against the fake ACP agent in tests/fake_agent.py."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "dashboard"))

import agy_backend  # noqa: E402

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
    env = agy_backend.build_env()
    assert "GEMINI_API_KEY" not in env
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "GOOGLE_CLOUD_PROJECT" not in env
    assert env["GEMINI_HOME"] == str(agy_backend.gemini_home())
    assert env["AGY_ACP_FORCE_FILE_STORAGE"] == "1"
    assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "1"


def test_argv_shape(fake_env):
    argv = agy_backend.acp_argv()
    assert "acp" in argv
    assert "--gemini-dir" in argv
    assert str(agy_backend.gemini_home()) in argv


def _start_session(tmp_path):
    session = agy_backend.ChatSession("test-handle", str(tmp_path))
    return session


def test_session_start_prompt_streaming(fake_env, tmp_path):
    async def scenario():
        session = _start_session(tmp_path)
        try:
            await session.start()
            assert session.agent_session_id == "fake-session-1"
            assert session.meta["authenticated"] is True

            await session.prompt("hello antigravity")
            # wait for the turn to finish
            deadline = asyncio.get_running_loop().time() + 10
            while session.turn_active and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)

            text = "".join(e.get("text", "") for e in session.events if e["kind"] == "text")
            assert "echo: hello antigravity" in text
            kinds = [e["kind"] for e in session.events]
            assert "ready" in kinds
            assert "user" in kinds
            assert "turn_end" in kinds
        finally:
            await session.close()

    run(scenario())


def test_events_long_poll(fake_env, tmp_path):
    async def scenario():
        session = _start_session(tmp_path)
        try:
            await session.start()
            ready_events, next_seq = await session.events_since(0, wait=0)
            assert any(e["kind"] == "ready" for e in ready_events)

            await session.prompt("stream test")
            collected = []
            deadline = asyncio.get_running_loop().time() + 10
            since = next_seq
            while asyncio.get_running_loop().time() < deadline:
                fresh, since = await session.events_since(since, wait=1)
                collected.extend(fresh)
                if any(e["kind"] == "turn_end" for e in collected):
                    break
            text = "".join(e.get("text", "") for e in collected if e["kind"] == "text")
            assert "echo: stream test" in text
        finally:
            await session.close()

    run(scenario())


def test_permission_answer_selected(fake_env, tmp_path):
    async def scenario():
        session = _start_session(tmp_path)
        try:
            await session.start()
            await session.prompt("please permission-test this")

            # wait for the permission request to appear, then answer it
            deadline = asyncio.get_running_loop().time() + 10
            request_id = None
            while asyncio.get_running_loop().time() < deadline:
                for event in session.events:
                    if event["kind"] == "permission_request":
                        request_id = event["requestId"]
                        break
                if request_id:
                    break
                await asyncio.sleep(0.05)
            assert request_id, "permission request never arrived"

            assert session.answer_permission(request_id, "allow") is True

            deadline = asyncio.get_running_loop().time() + 10
            while session.turn_active and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
            text = "".join(e.get("text", "") for e in session.events if e["kind"] == "text")
            assert "permission outcome: selected" in text
        finally:
            await session.close()

    run(scenario())


def test_permission_dismissed(fake_env, tmp_path):
    async def scenario():
        session = _start_session(tmp_path)
        try:
            await session.start()
            await session.prompt("permission-test")
            deadline = asyncio.get_running_loop().time() + 10
            request_id = None
            while asyncio.get_running_loop().time() < deadline:
                for event in session.events:
                    if event["kind"] == "permission_request":
                        request_id = event["requestId"]
                        break
                if request_id:
                    break
                await asyncio.sleep(0.05)
            assert request_id
            assert session.answer_permission(request_id, None) is True
            deadline = asyncio.get_running_loop().time() + 10
            while session.turn_active and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.05)
            text = "".join(e.get("text", "") for e in session.events if e["kind"] == "text")
            assert "permission outcome: cancelled" in text
        finally:
            await session.close()

    run(scenario())


def test_session_manager(fake_env, tmp_path):
    async def scenario():
        manager = agy_backend.SessionManager()
        session = manager.create(str(tmp_path))
        assert manager.get(session.handle) is session
        try:
            await session.start()
        finally:
            assert await manager.drop(session.handle) is True
        assert manager.get(session.handle) is None
        assert session.closed is True

    run(scenario())


def test_register_no_tools_no_hooks():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "antigravity_acp_pkg",
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    calls = {"tools": 0, "hooks": 0, "cli": 0}

    class FakeCtx:
        def register_tool(self, **kwargs):
            calls["tools"] += 1

        def register_hook(self, *args, **kwargs):
            calls["hooks"] += 1

        def register_cli_command(self, name, **kwargs):
            calls["cli"] += 1

        def register_command(self, *args, **kwargs):
            calls["hooks"] += 1

    plugin.register(FakeCtx())
    # Compliance invariant: nothing the agent core or automation can invoke.
    assert calls == {"tools": 0, "hooks": 0, "cli": 0}
