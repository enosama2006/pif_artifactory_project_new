"""ADK agent package — `adk web` discovers this via `from . import agent`.

The sys.path insertion lets every internal module use absolute `from app...`
imports whether loaded as the `agent` package (ADK) or with `agent/` as the
working directory (pytest, run_local.py).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from . import agent  # noqa: E402,F401
