"""hermes-plugin-antigravity-acp — thin interactive ACP client for Google Antigravity.

Surface: a native Desktop page (``desktop/plugin.js``) backed by dashboard API
routes (``dashboard/plugin_api.py``) that spawn the unmodified ``agy acp`` binary.

Deliberately absent: no model provider (Hermes must not use Antigravity as a
model backend on a consumer sign-in), no tools (Hermes must not auto-invoke it),
no hooks, no CLI automation surface. The user opens the page and types prompts
directly; the official agent binary owns auth, history, tools, planning and quota.
"""

from __future__ import annotations

import sys


def register(ctx) -> None:  # noqa: ARG001 — dashboard/desktop-only plugin
    # Evict the cached backend on reload so existing routes pick up the current
    # file on their next request, without remounting routes.
    sys.modules.pop("hermes_dashboard_plugin_antigravity_acp_backend", None)
    return None


__all__ = ["register"]
