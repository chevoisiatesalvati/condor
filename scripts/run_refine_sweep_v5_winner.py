#!/usr/bin/env python3
"""Deprecated wrapper — use scripts/run_refine_sweep.py instead."""

from __future__ import annotations

import asyncio
import sys
import warnings

warnings.warn(
    "run_refine_sweep_v5_winner.py is deprecated; use run_refine_sweep.py",
    DeprecationWarning,
    stacklevel=1,
)

from scripts.run_refine_sweep import main


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
