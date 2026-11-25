"""hermes-plugin-antigravity-acp — thin interactive ACP client for Google Antigravity.

Deliberately minimal surface: one CLI command (`hermes antigravity`) and nothing
else. No model provider (Hermes must not use Antigravity as a model backend on a
consumer sign-in), no tools (Hermes must not auto-invoke it), no hooks (no
automation may drive it). The user opens a session and types prompts directly;
the official `agy acp` agent owns auth, history, tools, planning and quota.
"""

from __future__ import annotations

# Dual import: the Hermes loader imports this directory as a package; the flat test path does not.
try:
    from .cli import handle_cli, setup_cli
except ImportError:
    from cli import handle_cli, setup_cli


def register(ctx) -> None:
    """Plugin entry point called by the Hermes plugin loader."""
    ctx.register_cli_command(
        name="antigravity",
        help="Interactive Antigravity chat, sign-in and status",
        description=(
            "Thin interactive ACP client for Google Antigravity. Chat sessions are "
            "user-driven: you type every prompt yourself and the official agent "
            "binary owns the agent loop."
        ),
        setup_fn=setup_cli,
        handler_fn=handle_cli,
    )


__all__ = ["register"]
