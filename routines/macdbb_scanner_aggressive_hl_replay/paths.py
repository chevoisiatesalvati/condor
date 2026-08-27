import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = Path(os.environ.get("CONDOR_REPORTS_DIR", str(ROOT_DIR / "reports")))
# Leftover root catalog. Live indexes are reports/<routine>/reports_index.json.
REPORTS_INDEX_PATH = REPORTS_DIR / "reports_index.json"
DEFAULT_SNAPSHOT_DIR = ROOT_DIR / "data" / "replay_snapshots"


def strategy_data_dir(strategy_slug: str, agent_slug: str | None = None) -> Path:
    """Operational strategy root (sessions/, learnings.md, etc.).

    Deterministic Strategies (MACDBB) persist under ``data/strategy_runs/{slug}/``.
    Prefer that when present so session_parity matches live journals.
    """
    runs_root = ROOT_DIR / "data" / "strategy_runs" / strategy_slug
    if (runs_root / "sessions").is_dir():
        return runs_root
    from condor.agents.strategy_paths import resolve_strategy_data_dir

    return resolve_strategy_data_dir(agent_slug or strategy_slug, strategy_slug)


def strategy_sessions_dir(strategy_slug: str, agent_slug: str | None = None) -> Path:
    return strategy_data_dir(strategy_slug, agent_slug) / "sessions"
