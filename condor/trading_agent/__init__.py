"""Backward-compatible alias for ``condor.agents`` (pre-upstream rename)."""

from condor.trading_agent._shim import install

install()

from condor.agents import *  # noqa: F403,E402
from condor.agents import __all__ as __all__
