"""Backward-compatible wrapper for session 60 parity debugging."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.debug_session_parity import main


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(["--session-nums", "60"])))
