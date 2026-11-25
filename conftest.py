"""Test bootstrap: flat module path like the official Hermes plugins use."""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

collect_ignore = [
    "__init__.py",
    "cli.py",
    "client.py",
    "process.py",
    "repl.py",
    "authflow.py",
    "tests/fake_agent.py",
]
