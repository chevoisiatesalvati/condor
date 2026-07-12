---
name: Macdbb Scanner Aggressive HL
description: Hyperliquid-native scanner + MACD/BB
agent_key: cursor:default
skills: []
default_trading_context: ''
created_by: 1089320799
created_at: '2026-05-20T00:00:00+00:00'
default_config:
  server_name: local
  model_base_url: ''
  total_amount_quote: 500
  frequency_sec: 1800
  execution_mode: loop
  max_ticks: 0
  digest_interval_ticks: 0
  bot_name: ''
  risk_limits:
    max_open_executors: 10
    max_drawdown_pct: -2
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
