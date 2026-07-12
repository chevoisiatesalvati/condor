---
name: Macdbb Scanner Aggressive HL
description: Hyperliquid-native scanner + MACD/BB
agent_key: cursor:default
skills: []
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
  strategy_params:
    adaptive_activation_hours: 0.0
    min_tradeable_for_adaptive: 1
    adaptive_skip_4h_filter: true
    adaptive_requires_flat: false
    sl_symbol_cooldown_hours: 1.0
    adaptive_long_bb_pos_max: 86.0
    adaptive_short_bb_pos_min: 92.0
    adaptive_strong_long_bb_pos_max: 20.0
    adaptive_strong_short_bb_pos_min: 86.0
    adaptive_min_macd_gap_ratio: 0.02
    adaptive_min_hist_ratio: 0.3
    adaptive_score_open_min: 1.0
    adaptive_score_open_min_extreme: 1.5
    adaptive_hist_sign_bonus: 0.5
    adaptive_hist_sign_penalty: 0.5
    adaptive_momentum_bonus: 0.38
    adaptive_momentum_penalty: 0.22
    adaptive_tiebreak_score_delta: 0.15
    bb_proximity_epsilon_pct: 0.22
    scanner_top_n: 30
    scanner_lookback_hours: 6
    scanner_min_volume_usd: 2000000
    scanner_mature_count: 8
    scanner_degen_count: 8
    scanner_exclude_hip3: false
    min_scanner_analyzed: 3
    natr_floor_mature_pct: 0.08
    natr_floor_degen_pct: 0.1
    macd_queue_primary_size: 8
    macd_primary_review_count: 5
    macd_queue_pass2_min: 8
    macd_queue_pass2_max: 12
    macd_queue_total_cap: 20
    sl_pct: 3.8
    tp_pct: 5.0
    leverage: 30
    create_max_retries: 2
    thesis_decay_exit_hours: 22.0
    thesis_bb_drift_pts: 28.0
    flip_cooldown_hours: 0.5
    enable_dynamic_sizing: true
    enable_dynamic_barriers: true
    min_notional_quote: 125.0
    max_notional_quote: 760.0
    min_conviction_mult: 0.85
    max_conviction_mult: 2.15
    strength_mult_per_unit: 0.26
    extreme_displacement_mult: 1.65
    activation_streak_mult_per_tick: 0.0
    thin_universe_mult: 0.88
    mature_tape_low_vol_mult: 0.92
    vol_inverse_sizing: true
    min_vol_mult: 0.42
    max_vol_mult: 1.05
    ref_volatility_pct: 3.5
    sl_vol_exponent: 1.25
    tp_vol_exponent: 1.6
    sl_min_pct: 2.6
    sl_max_pct: 6.5
    tp_min_pct: 7.5
    tp_max_pct: 22.0
    volatility_source: auto
  strategy_preset: hl_dynamic_timeline_sweep_lead_013
default_trading_context: ''
created_by: 1089320799
created_at: '2026-05-20T00:00:00+00:00'
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
