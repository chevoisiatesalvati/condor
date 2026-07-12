---
name: MACD+BB Scanner Aggressive HL
description: Hyperliquid-native scanner + MACD/BB momentum strategy with dynamic sizing
  and adaptive entry filters.
agent_key: cursor:default
tools:
- manage_executors
- manage_routines
- trading_agent_journal_read
- trading_agent_journal_write
- send_notification
server_required: true
created_by: 0
created_at: '2026-01-01T00:00:00+00:00'
---

# MACD+BB Scanner Aggressive HL

Autonomous trading agent for Hyperliquid perpetuals. Each tick scans the market,
evaluates MACD/Bollinger Band signals, and manages positions via executors only.

Operational playbook and tuned parameters live in the private `strategies/` submodule;
the public repo ships the replay machinery and agent-local routines under `agents/`.
