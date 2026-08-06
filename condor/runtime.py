"""Deprecated shim: ``process_started_at`` lives in ``condor.runtime.lifecycle``.

The ``condor.runtime`` *package* shadows this module on import. Prefer::

    from condor.runtime import process_started_at
"""

from __future__ import annotations

from condor.runtime.lifecycle import process_started_at

__all__ = ["process_started_at"]
