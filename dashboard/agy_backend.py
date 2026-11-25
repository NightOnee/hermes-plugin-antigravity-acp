"""Self-contained ACP client + session manager for the dashboard backend.

Zero third-party dependencies beyond what the host provides: newline-delimited
JSON-RPC over the agent's stdin/stdout, exactly as the ACP spec defines. The
agent binary is the unmodified official `agy`/`antigravity` executable; this
module only ever forwards text the user typed in the Desktop UI.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import shlex
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

_log = logging.getLogger("antigravity_acp.backend")

COMMAND_ENV = "ANTIGRAVITY_ACP_COMMAND"
HOME_ENV = "ANTIGRAVITY_ACP_HOME"

STRIP_ENV_VARS = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_QUOTA_PROJECT",
    "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA", "GEMINI_HOME",
    "AGY_ACP_FORCE_FILE_STORAGE", "AGY_CLI_DISABLE_AUTO_UPDATE",
)

INSTALL_HINT = (
    "Antigravity CLI not found on the server. Install Google Antigravity so `agy` "
    f"or `antigravity` is on PATH, or set {COMMAND_ENV}."
)

PROTOCOL_VERSION = 1
CLIENT_INFO = {"name": "hermes-antigravity-acp", "title": "Hermes Antigravity ACP Plugin", "version": "0.2.0"}


class BackendError(RuntimeError):
    pass


def find_command() -> list[str]:
    override = os.environ.get(COMMAND_ENV, "").strip()
    if override:
        return shlex.split(override)
    for name in ("agy", "antigravity"):
        path = shutil.which(name)
        if path:
            return [path]
    raise BackendError(INSTALL_HINT)


def state_home() -> Path:
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        base = Path(get_hermes_home())
    except Exception:
        base = Path(os.environ.get("HERMES_HOME", "") or Path.home() / ".hermes")
    return base / "plugins-data" / "antigravity-acp"


def gemini_home() -> Path:
    return state_home() / "gemini-home"


def build_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV_VARS}
    home = gemini_home()
    home.mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)
    except OSError:
        pass
    env["GEMINI_HOME"] = str(home)
    env["AGY_ACP_FORCE_FILE_STORAGE"] = "1"
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "1"
    return env


def acp_argv() -> list[str]:
    home = gemini_home()
    return [*find_command(), "acp", "--gemini-dir", str(home), "--app-data-dir", "antigravity-acp"]


class JsonRpcProcess:
    """One spawned agent process with a newline-delimited JSON-RPC channel."""

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str) -> None:
        self.argv = argv
        self.env = env
        self.cwd = cwd
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._notification_handler = None
        self._request_handler = None
        self._reader_task: asyncio.Task | None = None
        self.stderr_lines: list[str] = []

    async def start(
        self,
        on_notification,
        on_request,
    ) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            cwd=self.cwd,
        )
        self._notification_handler = on_notification
        self._request_handler = on_request
        self._reader_task = asyncio.create_task(self._read_loop())
        asyncio.create_task(self._stderr_loop())

    async def _stderr_loop(self) -> None:
        assert self.process and self.process.stderr
        try:
            async for raw in self.process.stderr:
                line = raw.decode(errors="replace").rstrip()
                self.stderr_lines.append(line)
                if len(self.stderr_lines) > 200:
                    self.stderr_lines = self.stderr_lines[-200:]
        except Exception:
            pass

    async def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        try:
            async for raw in self.process.stdout:
                try:
                    message = json.loads(raw.decode(errors="replace"))
                except json.JSONDecodeError:
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(BackendError(str(message["error"].get("message", message["error"]))))
                        else:
                            future.set_result(message.get("result"))
                elif "id" in message and "method" in message:
                    # Incoming request from the agent (e.g. session/request_permission).
                    asyncio.create_task(self._answer_request(message))
                elif "method" in message:
                    if self._notification_handler:
                        try:
                            self._notification_handler(message["method"], message.get("params") or {})
                        except Exception:
                            _log.exception("notification handler failed")
        except Exception:
            _log.exception("agent read loop ended")
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(BackendError("agent process exited"))

    async def _answer_request(self, message: dict) -> None:
        result: Any = None
        error: dict | None = None
        try:
            if self._request_handler:
                result = await self._request_handler(message["method"], message.get("params") or {})
            else:
                raise BackendError("no request handler")
        except Exception as exc:
            error = {"code": -32603, "message": str(exc)}
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": message["id"]}
        if error:
            response["error"] = error
        else:
            response["result"] = result
        await self._write(response)

    async def _write(self, message: dict) -> None:
        assert self.process and self.process.stdin
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        await self.process.stdin.drain()

    async def call(self, method: str, params: dict | None = None, timeout: float = 60) -> Any:
        request_id = next(self._next_id)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        return await asyncio.wait_for(future, timeout)

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self._reader_task:
            self._reader_task.cancel()


class ChatSession:
    """One interactive user-driven chat: process + ACP session + event log."""

    def __init__(self, handle: str, cwd: str) -> None:
        self.handle = handle
        self.cwd = cwd
        self.rpc = JsonRpcProcess(acp_argv(), build_env(), cwd)
        self.agent_session_id: str | None = None
        self.events: list[dict] = []
        self._seq = itertools.count(1)
        self._changed = asyncio.Event()
        self._permission_waiters: dict[str, asyncio.Future] = {}
        self.turn_active = False
        self.closed = False
        self.modes: list[dict] = []
        self.created_at = time.time()
        self.meta: dict[str, Any] = {}

    def emit(self, kind: str, **data: Any) -> None:
        self.events.append({"seq": next(self._seq), "kind": kind, "time": time.time(), **data})
        if len(self.events) > 4000:
            self.events = self.events[-4000:]
        self._changed.set()

    # -- ACP incoming traffic -------------------------------------------------

    def _on_notification(self, method: str, params: dict) -> None:
        if method != "session/update":
            return
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = (update.get("content") or {}).get("text")
            if text:
                self.emit("text", text=text)
        elif kind == "agent_thought_chunk":
            text = (update.get("content") or {}).get("text")
            if text:
                self.emit("thought", text=text)
        elif kind in ("tool_call", "tool_call_update"):
            self.emit(
                "tool",
                toolCallId=update.get("toolCallId"),
                title=update.get("title"),
                toolKind=update.get("kind"),
                status=update.get("status"),
            )
        elif kind == "plan":
            entries = [{"content": e.get("content"), "status": e.get("status")} for e in update.get("entries") or []]
            self.emit("plan", entries=entries)
        elif kind == "current_mode_update":
            self.emit("mode", modeId=update.get("currentModeId"))

    async def _on_request(self, method: str, params: dict) -> Any:
        if method == "session/request_permission":
            request_id = uuid.uuid4().hex
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._permission_waiters[request_id] = future
            options = params.get("options") or []
            tool_call = params.get("toolCall") or {}
            self.emit(
                "permission_request",
                requestId=request_id,
                title=tool_call.get("title"),
                toolKind=tool_call.get("kind"),
                options=[{"optionId": o.get("optionId"), "name": o.get("name"), "kind": o.get("kind")} for o in options],
            )
            try:
                option_id = await asyncio.wait_for(future, timeout=600)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                option_id = None
            finally:
                self._permission_waiters.pop(request_id, None)
            self.emit("permission_resolved", requestId=request_id, optionId=option_id)
            if option_id is None:
                return {"outcome": {"outcome": "cancelled"}}
            return {"outcome": {"outcome": "selected", "optionId": option_id}}
        if method in ("fs/read_text_file", "fs/write_text_file") or method.startswith("terminal/"):
            raise BackendError(f"client capability not advertised: {method}")
        raise BackendError(f"unsupported client method: {method}")

    # -- Lifecycle ------------------------------------------------------------

    async def start(self, auth_method: str = "oauth-personal", resume_id: str | None = None) -> None:
        await self.rpc.start(self._on_notification, self._on_request)
        initialized = await self.rpc.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
                "clientInfo": CLIENT_INFO,
            },
        )
        self.meta["agentInfo"] = initialized.get("agentInfo")
        auth_methods = [m.get("id") for m in initialized.get("authMethods") or []]
        self.meta["authMethods"] = auth_methods
        if auth_method in auth_methods:
            await self.rpc.call("authenticate", {"methodId": auth_method}, timeout=300)
            self.meta["authenticated"] = True
        else:
            self.meta["authenticated"] = not auth_methods  # nothing required
        params: dict[str, Any] = {"cwd": self.cwd, "mcpServers": []}
        if resume_id and (initialized.get("agentCapabilities") or {}).get("loadSession"):
            params["sessionId"] = resume_id
            result = await self.rpc.call("session/load", params)
        else:
            result = await self.rpc.call("session/new", params)
        self.agent_session_id = result.get("sessionId")
        modes = (result.get("modes") or {}).get("availableModes") or []
        self.modes = [{"id": m.get("id"), "name": m.get("name")} for m in modes]
        self.emit("ready", agentSessionId=self.agent_session_id, modes=self.modes, meta=self.meta)

    async def prompt(self, text: str) -> None:
        if self.turn_active:
            raise BackendError("a turn is already running")
        if not self.agent_session_id:
            raise BackendError("session not ready")
        self.turn_active = True
        self.emit("user", text=text)

        async def run() -> None:
            try:
                result = await self.rpc.call(
                    "session/prompt",
                    {"sessionId": self.agent_session_id, "prompt": [{"type": "text", "text": text}]},
                    timeout=3600,
                )
                self.emit("turn_end", stopReason=(result or {}).get("stopReason"))
            except Exception as exc:
                self.emit("error", message=str(exc))
                self.emit("turn_end", stopReason="error")
            finally:
                self.turn_active = False

        asyncio.create_task(run())

    async def cancel(self) -> None:
        if self.agent_session_id:
            await self.rpc.notify("session/cancel", {"sessionId": self.agent_session_id})

    async def set_mode(self, mode_id: str) -> None:
        if self.agent_session_id:
            await self.rpc.call(
                "session/set_mode", {"sessionId": self.agent_session_id, "modeId": mode_id}
            )
            self.emit("mode", modeId=mode_id)

    def answer_permission(self, request_id: str, option_id: str | None) -> bool:
        future = self._permission_waiters.get(request_id)
        if not future or future.done():
            return False
        future.set_result(option_id)
        return True

    async def events_since(self, since: int, wait: float = 0) -> tuple[list[dict], int]:
        deadline = time.monotonic() + wait
        while True:
            fresh = [e for e in self.events if e["seq"] > since]
            if fresh or self.closed or time.monotonic() >= deadline:
                next_seq = self.events[-1]["seq"] if self.events else since
                return fresh, next_seq
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        self.closed = True
        for future in self._permission_waiters.values():
            if not future.done():
                future.set_result(None)
        await self.rpc.close()
        self._changed.set()


class SessionManager:
    """Process-wide registry of live chat sessions."""

    def __init__(self) -> None:
        self.sessions: dict[str, ChatSession] = {}

    def create(self, cwd: str) -> ChatSession:
        handle = uuid.uuid4().hex[:12]
        session = ChatSession(handle, cwd)
        self.sessions[handle] = session
        return session

    def get(self, handle: str) -> ChatSession | None:
        return self.sessions.get(handle)

    async def drop(self, handle: str) -> bool:
        session = self.sessions.pop(handle, None)
        if not session:
            return False
        await session.close()
        return True


MANAGER = SessionManager()


def binary_status() -> dict[str, Any]:
    try:
        argv = find_command()
        found = True
    except BackendError as exc:
        argv = []
        found = False
    home = gemini_home()
    signed_in = False
    if home.exists():
        signed_in = any(
            list(home.glob(pattern))
            for pattern in ("antigravity-acp/*token*", "antigravity-acp/*oauth*")
        )
    return {
        "binaryFound": found,
        "command": " ".join(argv) if argv else None,
        "profile": str(home),
        "credentialFilesPresent": signed_in,
        "hint": None if found else INSTALL_HINT,
    }
