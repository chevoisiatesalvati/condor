# Trading Agents — Agent Instructions

Instructions for working on autonomous trading agents under `trading_agents/`. Parent guide: `../AGENTS.md`.

---

## Folder contract

Each agent **slug** (directory name) is the stable identifier everywhere: MCP, replay `strategy_slug`, session paths, prefixed routine names.

```
trading_agents/{slug}/
  agent.md                 # Strategy definition — YAML frontmatter + markdown body
  learnings.md             # Execution notes injected each tick (agent-maintained)
  learnings_archive.md     # Human-only archive — not sent to the agent
  routines/                # Agent-scoped deterministic Python (optional)
    {routine_name}.py      # Discovered as {slug}/{routine_name}
  sessions/
    session_{N}/
      config.yml           # Frozen runtime config for that run
      journal.md           # Tick log, decisions, action lines
      snapshots/           # Full prompt + tool calls per tick
  dry_runs/
    experiment_{N}.md      # Single-tick dry-run / run-once captures
```

**Do not** rename an agent slug without updating replay configs, scripts, and tests that reference it.

---

## `agent.md` structure

1. **YAML frontmatter** — machine-readable metadata:
   - `name`, `description`, `agent_key`, `default_config`, `strategy_params`
   - `default_config.strategy_params` holds tunable thresholds (scanner, adaptive, sizing, barriers)
2. **Markdown body** — the LLM system prompt for each tick:
   - Objective, step-by-step tick workflow
   - Which routines to call (`manage_routines`) and how to interpret output
   - Entry/exit/monitor rules, journal format, risk overrides
   - Explicit executor config schema (agents cannot fetch schemas mid-tick)

When changing strategy behavior, update **both** `agent.md` instructions and any deterministic routines/policies the agent relies on.

---

## Agent-local routines vs global routines

| | Agent-local | Global |
|---|-------------|--------|
| Path | `trading_agents/{slug}/routines/` | `routines/` |
| MCP name | `{slug}/{name}` | `{name}` |
| Use for | Logic unique to one strategy | Shared market data, scanners, backtests |

Example (`macdbb_scanner_aggressive_hl`):

- **Agent-local**: `macdbb_signal_metrics`, `macdbb_entry_policy` — deterministic signal + sizing the LLM must not recompute
- **Global**: `hyperliquid_market_scanner`, `macd_bb_analysis` — shared Hyperliquid data prep
- **Backtest**: `macdbb_scanner_aggressive_hl_backtest` (global routine, agent-specific name)

New agents: add `{slug}_backtest.py` + `{slug}_replay/` package when you need session parity or timeline sweeps.

---

## Live tick workflow (macdbb_scanner_aggressive_hl)

Reference implementation — other agents may differ but should follow the same split (deterministic routines + LLM orchestration):

1. Scanner → `hyperliquid_market_scanner`
2. Per-pair MACD/BB → `macd_bb_analysis` (1h; 4h for entry filter)
3. Signal metrics → `{slug}/macdbb_signal_metrics`
4. Entry sizing/barriers → `{slug}/macdbb_entry_policy` (wraps `condor/trading_agent/policies/macdbb_dynamic.py`)
5. Create/manage → `manage_executors(action="create"|"stop")` with `position_executor`
6. Journal → `trading_agent_journal_write` every tick

The LLM must use routine output **verbatim** for formal/adaptive flags, notional, and SL/TP — not re-derive them.

---

## Backtest and tuning

Backtest routine: `manage_routines(name="macdbb_scanner_aggressive_hl_backtest", ...)`

Library: `routines/macdbb_scanner_aggressive_hl_replay/` (re-exports presets from the agent)

| Module | Role |
|--------|------|
| `simulator.py` | Session/timeline simulation engine |
| `models.py` | `DynamicStrategyReplayConfig`, tick/trade models |
| `trading_agents/.../presets.py` | Public preset framework + yaml loader (winners in private `strategies/`) |
| `config_sweep.py` | Grid/random sweeps over sessions |
| `timeline_sweep.py` | Timeline mega-sweeps on snapshot data |

Common scripts:

```bash
PYTHONPATH=. .venv/bin/python scripts/run_timeline_mega_sweep.py sweep ...
PYTHONPATH=. .venv/bin/python scripts/debug_session_parity.py
PYTHONPATH=. .venv/bin/python scripts/restart_trading_agent.py --slug macdbb_scanner_aggressive_hl
```

After changing `strategy_params` in `agent.md` or policies, run session parity tests:

```bash
.venv/bin/pytest tests/routines/test_macdbb_scanner_aggressive_hl_replay_parity.py tests/routines/test_session_barrier_parity.py -q
```

Historical sweep CSVs under `data/strategy_replay_sweeps/` may use old filename stems — do not rename on-disk artifacts; new sweeps use `macdbb_scanner_aggressive_hl_backtest_*` stems.

---

## Private strategies (`strategies/` submodule)

Proprietary agent definitions and tuned presets live in a **private repo**, checked out at `strategies/`:

| Private file | Purpose |
|--------------|---------|
| `strategies/{slug}/agent.md` | Live strategy frontmatter + LLM instructions |
| `strategies/{slug}/presets.yaml` | Sweep winners, preset labels, `current_winner_preset` |

Public repo ships `agent.example.md`, `presets.private.example.yaml`, and framework code in `presets.py`. Init:

```bash
./scripts/init_strategies.sh
```

After a sweep win, apply scripts write to `strategies/{slug}/`; commit and push in that repo, then bump the submodule pointer in Condor.

---

## Adding a new agent

1. Follow `.claude/skills/trading-agent-builder/SKILL.md` (user must approve design before coding)
2. Create `trading_agents/{slug}/agent.example.md` (public template) and private `strategies/{slug}/agent.md`
3. Add agent-local routines under `trading_agents/{slug}/routines/` if needed
4. If backtesting is required: `{slug}_backtest.py` + `{slug}_replay/` (copy/adapt from macdbb stack)
5. Dry-run → run-once → loop; inspect `sessions/` and `journal.md` before live

Keep slug short, lowercase, underscore-separated (e.g. `river_scalper`, `macdbb_scanner_aggressive_hl`).
