# Condor — Agent Instructions

Condor is a Telegram + web trading platform built on the **Hummingbot Backend API**. It includes global analysis **routines**, autonomous **trading agents** (LLM tick loops that trade via executors), and MCP tools for market data and execution.

This file is the project-level guide for AI coding agents. Cursor also loads `.cursor/rules/*.mdc` (see `python-testing.mdc` for pytest/PYTHONPATH rules). For trading-agent work, see `trading_agents/AGENTS.md`.

---

## Repo layout (what lives where)

| Path | Purpose |
|------|---------|
| `condor/` | Core app: web API, trading agent engine, reports, routine store |
| `handlers/` | Telegram command handlers (`/portfolio`, `/trade`, `/routines`, `/agent`, …) |
| `routines/` | **Global** auto-discovered routines (`*.py` at top level + packages) |
| `trading_agents/{slug}/` | One folder per autonomous agent (strategy, sessions, agent-local routines) |
| `scripts/` | One-off CLIs (sweeps, backfills, candle prefetch, winner application) |
| `tests/` | Pytest suite — run from repo root with `.venv/bin/pytest` |
| `mcp_servers/` | MCP tool implementations (Hummingbot API, etc.) |
| `frontend/` | Web dashboard (React) |
| `data/` | Local caches, sweep CSVs, replay snapshots (not always in git) |
| `reports/` | HTML reports from routines and agents |

Top-level Python packages (`condor/`, `routines/`) are **not** installed as site-packages unless you do an editable install. Imports assume the **repo root** is on `PYTHONPATH`.

---

## Build, test, and run

```bash
cd /home/saul/projects/Hummingbot/condor

# Tests (always use project venv from repo root)
.venv/bin/pytest tests/ -q

# Fallback if imports fail
PYTHONPATH=. .venv/bin/pytest tests/ -q

# Run a routine module CLI (example: config sweep)
PYTHONPATH=. .venv/bin/python -m routines.macdbb_scanner_aggressive_hl_replay.config_sweep
```

Do **not** rely on system/Anaconda `pytest` — it will miss `routines` and `condor` modules.

Python **3.12+**. Dev deps: `pytest`, `pytest-asyncio`, `black`, `isort` (see `pyproject.toml`).

---

## Routines

### Global routines

- Location: `routines/{name}.py`
- Discovery name: `{name}` (filename stem)
- Required exports: `Config` (Pydantic model), `async def run(config, context)`
- Optional: `CATEGORY`, `CONTINUOUS = True`, `PRESET_OVERRIDES`
- Docstring on `Config` becomes the UI description

See `.claude/skills/create-routine/SKILL.md` for the full template.

### Agent-local routines

- Location: `trading_agents/{slug}/routines/{name}.py`
- Discovery name: `{slug}/{name}` (prefixed with agent slug)
- Same `Config` + `run` contract as global routines

### Naming convention for agent backtests

When an agent has a replay/backtest stack, name by **agent slug**:

| Role | Pattern | Example |
|------|---------|---------|
| Backtest routine (UI / MCP) | `{slug}_backtest.py` | `macdbb_scanner_aggressive_hl_backtest` |
| Replay library (internal) | `{slug}_replay/` | `macdbb_scanner_aggressive_hl_replay/` |

Invoke backtests:

```text
manage_routines(action="run", name="macdbb_scanner_aggressive_hl_backtest", config={...})
```

Future agents should follow the same `{slug}_backtest` + `{slug}_replay` pattern — do not reuse generic names like `strategy_replay_*`.

---

## Trading agents

Each agent is a directory under `trading_agents/{slug}/`:

```
trading_agents/{slug}/
  agent.md              # YAML frontmatter + LLM system prompt / tick instructions
  learnings.md          # Cross-session lessons (agent-written)
  routines/             # Deterministic helpers scoped to this agent
  sessions/session_N/   # Live run: config.yml, journal.md, snapshots/
  dry_runs/             # One-shot dry-run snapshots
```

- **`agent.md` frontmatter**: `name`, `description`, `agent_key`, `default_config`, `strategy_params`, …
- **Tick engine**: `condor/trading_agent/engine.py` — providers, prompt, MCP, journal, risk
- **Deep dive**: `condor/trading_agent/README.md`
- **Builder workflow**: `.claude/skills/trading-agent-builder/SKILL.md` (5 phases: design → routine → strategy → dry-run → live)

Agents trade **only via executors** (`manage_executors`), tagged with `controller_id == agent_id`. Never use raw `place_order` in agent instructions.

Run modes: `dry_run` (no trading), `run_once`, `loop`.

---

## Shared policy code (live + replay parity)

Deterministic logic shared between live agent routines and replay simulators lives in:

- `condor/trading_agent/policies/macdbb_dynamic.py` — dynamic notional + SL/TP barriers
- `condor/trading_agent/policies/macdbb_metrics.py` — signal metrics helpers

Agent-local routines (`macdbb_signal_metrics`, `macdbb_entry_policy`) wrap these for live ticks. Replay code in `{slug}_replay/` must stay in parity with live behavior — add/update tests when changing either side.

---

## Current agents

| Slug | Description | Backtest routine |
|------|-------------|------------------|
| `macdbb_scanner_aggressive_hl` | Hyperliquid scanner + MACD/BB, dynamic sizing | `macdbb_scanner_aggressive_hl_backtest` |

Key files for this agent:

- Strategy: `trading_agents/macdbb_scanner_aggressive_hl/agent.md`
- Live routines: `macdbb_signal_metrics`, `macdbb_entry_policy` (under agent `routines/`)
- Global routines used at tick time: `hyperliquid_market_scanner`, `macd_bb_analysis`
- Replay library: `routines/macdbb_scanner_aggressive_hl_replay/`
- Sweep CLI: `scripts/run_timeline_mega_sweep.py`, `scripts/run_staged_mega_sweep_v5.py`

Replay modes (`DynamicStrategyReplayConfig.replay_mode`):

- `session_parity` — replay journal tick timestamps against live session data
- `timeline_backtest` — synthetic UTC range, no sessions/journal (parameter sweeps)

---

## Code conventions

- **Minimize scope** — smallest correct diff; don't refactor unrelated code
- **Match existing style** — naming, imports, pydantic patterns, async handlers
- **No drive-by comments** — only explain non-obvious business logic
- **Useful tests only** — test real behavior, not trivial assertions
- **Commits** — only when explicitly requested by the user
- **Secrets** — never commit `.env`, credentials, or API keys

Routine imports use absolute paths from repo root:

```python
from routines.macdbb_scanner_aggressive_hl_replay.simulator import simulate_strategy_session
from condor.trading_agent.policies.macdbb_dynamic import resolve_live_entry_policy
```

---

## Skills (project-local)

| Skill | Path | Use when |
|-------|------|----------|
| Create routine | `.claude/skills/create-routine/SKILL.md` | Adding/editing `routines/*.py` |
| Trading agent builder | `.claude/skills/trading-agent-builder/SKILL.md` | New autonomous agents |
| Python testing | `.cursor/rules/python-testing.mdc` | Running pytest / PYTHONPATH (always applied in Cursor) |

---

## MCP tools (runtime, not codebase)

Agents and assistants use MCP tools at runtime — do not grep the codebase unless implementing handlers:

- `manage_routines` — run/list global and agent-local routines
- `manage_trading_agent` — start/stop agents, run agent routines
- `manage_executors` — create/stop position executors (agents' only trade path)
- `trading_agent_journal_read` / `trading_agent_journal_write` — session memory

Assistant personas: `assistants/condor.md`, `assistants/agent_builder.md`.
