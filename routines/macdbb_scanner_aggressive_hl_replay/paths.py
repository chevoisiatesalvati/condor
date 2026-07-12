import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = Path(os.environ.get("CONDOR_REPORTS_DIR", str(ROOT_DIR / "reports")))
REPORTS_INDEX_PATH = REPORTS_DIR / "reports_index.json"
DEFAULT_SNAPSHOT_DIR = ROOT_DIR / "data" / "replay_snapshots"


def strategy_data_dir(strategy_slug: str, agent_slug: str | None = None) -> Path:
    """Operational strategy root (sessions/, learnings.md, etc.)."""
    from condor.agents.strategy_paths import resolve_strategy_data_dir

    return resolve_strategy_data_dir(agent_slug or strategy_slug, strategy_slug)


def strategy_sessions_dir(strategy_slug: str, agent_slug: str | None = None) -> Path:
    return strategy_data_dir(strategy_slug, agent_slug) / "sessions"
