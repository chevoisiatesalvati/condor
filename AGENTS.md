# Condor — Agent Instructions

Condor is a Telegram + web trading platform built on the **Hummingbot Backend API**. It includes global analysis **routines**, autonomous **trading agents** (LLM tick loops that trade via executors), and MCP tools for market data and execution.

This file is the project-level guide for AI coding agents. Cursor also loads `.cursor/rules/*.mdc` (see `python-testing.mdc` for pytest/PYTHONPATH rules).

---

## Repo layout (what lives where)

| Path | Purpose |
|------|---------|
| `condor/` | Core app: web API, trading agent engine, reports, routine store |
| `handlers/` | Telegram command handlers (`/portfolio`, `/trade`, `/routines`, `/agent`, …) |
| `routines/` | **Global** auto-discovered routines (`*.py` at top level + packages) |
| `agents/{slug}/` | Canonical agent tree: metadata, routines, strategies, and sessions |
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

- Location: `agents/{slug}/routines/{name}.py`
- Discovery name: `{slug}/{name}` (prefixed with agent slug)
- Same `Config` + `run` contract as global routines

### Naming convention for agent backtests

When an agent has a replay/backtest stack, name by **agent slug**:

| Role | Pattern | Example |
|------|---------|---------|
| Backtest routine | `{slug}_backtest.py` | `macdbb_scanner_aggressive_hl_backtest` |
| Replay library (internal) | `{slug}_replay/` | `macdbb_scanner_aggressive_hl_replay/` |
| Shared presets (agent-owned) | `agents/{slug}/presets.py` | live + backtest |

Invoke backtests:

```text
manage_routines(action="run", name="macdbb_scanner_aggressive_hl_backtest", config={...})
```

Future agents should follow the same `{slug}_backtest` + `{slug}_replay` pattern — do not reuse generic names like `strategy_replay_*`.

---

## Trading agents

Each agent is a directory under `agents/{slug}/`:

```
agents/{slug}/
  AGENT.md                        # public agent metadata
  presets.py                      # shared public preset logic
  routines/                       # deterministic helpers scoped to this agent
  strategies/{strategy_slug}/
    strategy.md                   # public strategy stub / fallback playbook
    sessions/session_N/           # live run: config.yml, journal.md, snapshots/
```

- **`strategy.md` / private `agent.md` frontmatter**: `name`, `description`, `agent_key`, `default_config`, `strategy_params`, …
- **Tick engine**: `condor/trading_agent/engine.py` — providers, prompt, MCP, journal, risk
- **Deep dive**: `condor/trading_agent/README.md`
- **Builder workflow**: `.claude/skills/trading-agent-builder/SKILL.md` (5 phases: design → routine → strategy → dry-run → live)

Agents trade **only via executors** (`manage_executors`), tagged with `controller_id == agent_id`. Never use raw `place_order` in agent instructions.

Run modes: `dry_run` (no trading), `run_once`, `loop`.

---

## Shared MACDBB engine (live Strategies + replay parity)

Deterministic logic lives under `condor/strategy_runners/macdbb/`:

- `engine.py` / `decide()` — live DeterministicRunner + replay bridge
- `dynamic.py` — dynamic notional + SL/TP barriers
- `metrics.py` — signal metrics helpers
- `presets.py` / `params.py` — Strategies presets and param schema

Live runs use the **Strategies** tab (`DeterministicRunner`), not Agents chat.
Runs/journals: `data/strategy_runs/macdbb_scanner_aggressive_hl/`.
Defaults: `strategies/macdbb_scanner_aggressive_hl/strategy.yaml`.
Replay library: `routines/macdbb_scanner_aggressive_hl_replay/` (imports strategy_runners).
Sweep CLI: `scripts/run_timeline_mega_sweep.py`, `scripts/run_staged_mega_sweep_v5.py`, `scripts/run_refine_sweep.py`.

---

## Current Strategies (deterministic)

| Slug | Description | Backtest routine |
|------|-------------|------------------|
| `macdbb_scanner_aggressive_hl` | Hyperliquid scanner + MACD/BB, dynamic sizing | `macdbb_scanner_aggressive_hl_backtest` |

Replay modes (`DynamicStrategyReplayConfig.replay_mode`):

- `session_parity` — replay journal tick timestamps against live session data
- `timeline_backtest` — synthetic UTC range, no sessions/journal (parameter sweeps)

**Snapshot data** — timeline mode with `data_source=snapshots` reads parquet under `data/replay_snapshots_*`. Build or extend with `scripts/build_replay_snapshots.py` (see root `README.md` → *Replay snapshots*). Backtests can auto-update via `auto_update_snapshots`; sweeps assume snapshots are already current.

**Tick frequency** — sweep stores default to `frequency_sec=1800`. For live-parity minute backtests use a separate store (e.g. `data/replay_snapshots_binance_60s`) and preset `hl_dynamic_timeline_refine_lead_013_60s` (or override `frequency_sec=60` on the 1800s preset). `resolve_config_with_preset` rescales duration `*_ticks` to preserve wall-clock cooldowns/decay.

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
from condor.strategy_runners.macdbb.dynamic import resolve_live_entry_policy
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

---

## Cursor Cloud specific instructions

Repo root is `/workspace` (ignore the `/home/saul/...` paths in the Build section — run everything from `/workspace`). Dependencies are refreshed automatically by the startup update script (`uv sync --extra dev` + `frontend` `npm install`); `uv` lives at `~/.local/bin/uv` and `node`/`npm` are already on PATH. `make setup` / `setup-environment.sh` is an **interactive wizard** (prompts on `/dev/tty`, installs system tooling, optionally deploys Docker) — do not run it here.

**Test suite:** `.venv/bin/pytest tests/ -q` from `/workspace`. The core suite passes, but ~19 tests under `tests/routines/` and `tests/trading_agent/` fail on a clean checkout because their fixtures (session journals/snapshots under `trading_agents/*/sessions/…`) live in the **private `strategies/` git submodule** (`condor-strategies`), which needs SSH access to a private repo and is not checked out. These failures are expected without that submodule — not an environment problem.

**Lint:** `make lint` runs `black .` + `isort .` in **auto-format** mode (not `--check`). Running `black --check .` / `isort --check-only .` and `frontend` `npm run lint` currently report many **pre-existing** findings; treat those as repo state, not a broken environment.

**Running the app:** the full process (`make run` / `make dev` → `python main.py`) requires a real `TELEGRAM_TOKEN` (it long-polls Telegram) and a reachable Hummingbot Backend API on `:8000` (external Docker; not in this repo) for any trading data. Neither is available by default in cloud.

**Running just the web dashboard (no Telegram bot, no Hummingbot API):** the FastAPI app `condor.web.app:create_app()` runs standalone and does not need Telegram polling. Put a dummy `TELEGRAM_TOKEN` (any `digits:string` value — it only seeds the JWT secret) and an `ADMIN_USER_ID` in `.env` (both are gitignored; `config.yml` is auto-created and grants `ADMIN_USER_ID` the admin role). Serve `create_app()` via uvicorn on `:8088` with `CONDOR_DEV=1`, and run `npm run dev` in `frontend/` (Vite `:5173` proxies `/api`, `/reports`, `/ws` → `:8088`). Dashboard auth: mint a one-time login token with `condor.web.auth.create_login_token(admin_id)` **in the same process that serves requests** (the token store is an in-process dict, TTL 5 min), then open `http://localhost:5173/login?token=<token>` to exchange it for a JWT. Endpoints that only touch `config.yml` (e.g. `POST /api/v1/settings/servers`) work offline; portfolio/bots data needs a live Hummingbot API.
