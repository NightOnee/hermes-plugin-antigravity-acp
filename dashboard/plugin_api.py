"""Antigravity ACP dashboard plugin — backend API routes.

Mounted by the Hermes web server at ``/api/plugins/antigravity-acp/`` once the
plugin is enabled. The Desktop page (``desktop/plugin.js``) talks to these
routes through ``ctx.rest`` — every prompt they carry was typed by the user in
that page. Nothing here is callable by the Hermes agent core: no tools, no
hooks, no model-provider surface.

Routes:
    GET  /status                              binary + profile status
    POST /sessions        {cwd?}              spawn agent, start session  -> {handle, meta}
    GET  /sessions                            live session handles
    POST /sessions/{h}/messages   {text}      queue one user-typed turn
    POST /sessions/{h}/cancel                 cancel the running turn
    POST /sessions/{h}/mode       {modeId}    switch a native mode the agent advertised
    POST /sessions/{h}/permissions {requestId, optionId?}   answer a permission prompt
    GET  /sessions/{h}/events?since=&wait=    long-poll event stream
    DELETE /sessions/{h}                      terminate the agent process
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_log = logging.getLogger("antigravity_acp.api")
router = APIRouter()

_BACKEND_MODULE = "hermes_dashboard_plugin_antigravity_acp_backend"
_LOAD_LOCK = threading.Lock()


def _backend():
    mod = sys.modules.get(_BACKEND_MODULE)
    if mod is not None:
        return mod
    with _LOAD_LOCK:
        mod = sys.modules.get(_BACKEND_MODULE)
        if mod is not None:
            return mod
        path = Path(__file__).with_name("agy_backend.py")
        spec = importlib.util.spec_from_file_location(_BACKEND_MODULE, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load backend from {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_BACKEND_MODULE] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(_BACKEND_MODULE, None)
            raise
        return mod


class NewSessionBody(BaseModel):
    cwd: str | None = None
    authMethod: str = "oauth-personal"
    resumeSessionId: str | None = None


class MessageBody(BaseModel):
    text: str


class ModeBody(BaseModel):
    modeId: str


class PermissionBody(BaseModel):
    requestId: str
    optionId: str | None = None


@router.get("/status")
async def status() -> dict:
    backend = _backend()
    return backend.binary_status()


@router.post("/sessions")
async def create_session(body: NewSessionBody) -> dict:
    backend = _backend()
    cwd = body.cwd or os.getcwd()
    session = backend.MANAGER.create(cwd)
    try:
        await session.start(auth_method=body.authMethod, resume_id=body.resumeSessionId)
    except Exception as exc:
        await backend.MANAGER.drop(session.handle)
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "handle": session.handle,
        "agentSessionId": session.agent_session_id,
        "modes": session.modes,
        "meta": session.meta,
    }


@router.get("/sessions")
async def list_sessions() -> dict:
    backend = _backend()
    return {
        "sessions": [
            {
                "handle": s.handle,
                "cwd": s.cwd,
                "agentSessionId": s.agent_session_id,
                "turnActive": s.turn_active,
                "createdAt": s.created_at,
            }
            for s in backend.MANAGER.sessions.values()
            if not s.closed
        ]
    }


def _require_session(backend, handle: str):
    session = backend.MANAGER.get(handle)
    if session is None or session.closed:
        raise HTTPException(status_code=404, detail="unknown or closed session")
    return session


@router.post("/sessions/{handle}/messages")
async def post_message(handle: str, body: MessageBody) -> dict:
    backend = _backend()
    session = _require_session(backend, handle)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="empty prompt")
    try:
        await session.prompt(text)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.post("/sessions/{handle}/cancel")
async def cancel(handle: str) -> dict:
    backend = _backend()
    session = _require_session(backend, handle)
    await session.cancel()
    return {"ok": True}


@router.post("/sessions/{handle}/mode")
async def set_mode(handle: str, body: ModeBody) -> dict:
    backend = _backend()
    session = _require_session(backend, handle)
    try:
        await session.set_mode(body.modeId)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"ok": True}


@router.post("/sessions/{handle}/permissions")
async def answer_permission(handle: str, body: PermissionBody) -> dict:
    backend = _backend()
    session = _require_session(backend, handle)
    answered = session.answer_permission(body.requestId, body.optionId)
    if not answered:
        raise HTTPException(status_code=404, detail="no pending permission with that id")
    return {"ok": True}


@router.get("/sessions/{handle}/events")
async def events(handle: str, since: int = 0, wait: float = 20) -> dict:
    backend = _backend()
    session = _require_session(backend, handle)
    wait = max(0.0, min(wait, 25.0))  # stay under the hosted-request timeout
    fresh, next_seq = await session.events_since(since, wait=wait)
    return {"events": fresh, "next": next_seq, "closed": session.closed}


@router.delete("/sessions/{handle}")
async def delete_session(handle: str) -> dict:
    backend = _backend()
    dropped = await backend.MANAGER.drop(handle)
    return {"ok": dropped}
