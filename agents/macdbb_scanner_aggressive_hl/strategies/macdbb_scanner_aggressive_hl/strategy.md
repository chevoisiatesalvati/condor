---
name: Macdbb Scanner Aggressive HL
description: Hyperliquid-native scanner + MACD/BB — live playbook in strategies/ submodule
agent_key: null
skills: []
default_config:
  server_name: local
  total_amount_quote: 100
  frequency_sec: 3600
  execution_mode: loop
  max_ticks: 0
  digest_interval_ticks: 0
  risk_limits:
    max_open_executors: 1
    max_drawdown_pct: -5
  model_base_url: ''
  strategy_params:
    sl_pct: 2.0
    tp_pct: 4.0
    leverage: 1
default_trading_context: ''
created_by: 0
created_at: '2026-01-01T00:00:00+00:00'
---

## Objective

Public stub for UI discovery. Replace with your private `strategies/macdbb_scanner_aggressive_hl/agent.md`
for the live playbook and tuned defaults.

## Each tick

1. Check open executors for this agent.
2. Decide whether to hold, open, or close — **only** via `manage_executors` (create/stop position executors).
3. Log decisions with `trading_agent_journal_write`.

## Rules

- Do not call `place_order` directly.
- Respect risk limits from config.
- Skip trading if data or connectivity is unclear; journal why.
