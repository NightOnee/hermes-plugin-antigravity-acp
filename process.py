"""Locate and spawn the official Antigravity ACP agent with an isolated profile.

The plugin never patches, wraps or re-implements the agent binary. It spawns the
unmodified executable the user installed (or one explicitly configured) and talks
ACP over stdio. Credentials live in the plugin's own GEMINI_HOME, isolated per
profile directory, and ambient Google credentials from the parent environment are
stripped so the instance only ever uses the account the user signed in with here.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

COMMAND_ENV = "ANTIGRAVITY_ACP_COMMAND"
HOME_ENV = "ANTIGRAVITY_ACP_HOME"

# Ambient Google/Gemini credentials must never leak into the agent process: the
# instance uses exactly the account the user signed in with for this profile.
STRIP_ENV_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_CLOUD_QUOTA_PROJECT",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_GENAI_USE_GCA",
    "GEMINI_HOME",
    "AGY_ACP_FORCE_FILE_STORAGE",
    "AGY_CLI_DISABLE_AUTO_UPDATE",
)

INSTALL_HINT = (
    "Antigravity CLI not found. Install Google Antigravity (the `agy` or "
    "`antigravity` command must be on PATH), or set "
    f"{COMMAND_ENV}=/path/to/agy."
)


class AntigravityNotFound(RuntimeError):
    pass


def find_command() -> list[str]:
    """Resolve the agent executable as an argv prefix."""
    override = os.environ.get(COMMAND_ENV, "").strip()
    if override:
        return shlex.split(override)
    for name in ("agy", "antigravity"):
        path = shutil.which(name)
        if path:
            return [path]
    raise AntigravityNotFound(INSTALL_HINT)


def state_home() -> Path:
    """Plugin-owned state directory (credentials, sessions)."""
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    base = Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"
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
    return [
        *find_command(),
        "acp",
        "--gemini-dir",
        str(home),
        "--app-data-dir",
        "antigravity-acp",
    ]
