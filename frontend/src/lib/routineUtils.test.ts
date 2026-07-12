import { describe, expect, it } from "vitest";

import {
  applyPresetOverrides,
  getDisabledSelectOptions,
  reconcileReplayConfigValues,
  shouldDemotePresetToCustom,
  updateConfigValues,
} from "@/lib/routineUtils";

const PRESET_OVERRIDES = {
  hl_dynamic_session_parity: {
    enable_dynamic_sizing: true,
    formal_notional_quote: 500,
    sl_pct: 1.8,
    replay_mode: "session_parity",
    data_source: "reports_only",
  },
  hl_dynamic_timeline_refine_v5_winner_binance_1y: {
    replay_mode: "timeline_backtest",
    data_source: "snapshots",
    snapshot_dir: "data/replay_snapshots_binance_1y",
    sl_pct: 3.8,
    tp_pct: 5.0,
  },
};

const PRESET_OVERRIDES_WITH_SESSION = {
  ...PRESET_OVERRIDES,
  hl_dynamic_session_parity: {
    ...PRESET_OVERRIDES.hl_dynamic_session_parity,
    session_nums: "all",
  },
};

describe("applyPresetOverrides", () => {
  it("merges preset fields when a named preset is active", () => {
    const values = {
      preset: "hl_dynamic_session_parity",
      enable_dynamic_sizing: false,
      formal_notional_quote: 200,
      session_nums: "58",
    };
    expect(applyPresetOverrides(values, PRESET_OVERRIDES)).toEqual({
      preset: "hl_dynamic_session_parity",
      enable_dynamic_sizing: true,
      formal_notional_quote: 500,
      session_nums: "58",
      sl_pct: 1.8,
      replay_mode: "session_parity",
      data_source: "reports_only",
      tick_schedule: "journal_ticks",
      range_start_utc: null,
      range_end_utc: null,
      snapshot_dir: null,
      use_journal_barriers: false,
    });
  });

  it("applies timeline preset defaults on preset change", () => {
    const next = applyPresetOverrides(
      {
        preset: "hl_dynamic_timeline_refine_v5_winner_binance_1y",
        snapshot_dir: "data/replay_snapshots_hl_2d",
        sl_pct: 4.5,
      },
      PRESET_OVERRIDES,
      { fromPresetChange: true },
    );
    expect(next.snapshot_dir).toBe("data/replay_snapshots_binance_1y");
    expect(next.sl_pct).toBe(3.8);
    expect(next.replay_mode).toBe("timeline_backtest");
  });

  it("leaves values unchanged for custom preset", () => {
    const values = {
      preset: "custom",
      enable_dynamic_sizing: false,
      formal_notional_quote: 200,
    };
    expect(applyPresetOverrides(values, PRESET_OVERRIDES)).toEqual({
      ...values,
      tick_schedule: "journal_ticks",
      range_start_utc: null,
      range_end_utc: null,
      snapshot_dir: null,
    });
  });
});

describe("updateConfigValues", () => {
  const base = {
    preset: "hl_dynamic_session_parity",
    enable_dynamic_sizing: true,
    formal_notional_quote: 500,
    sl_pct: 1.8,
    session_nums: "58",
    replay_mode: "session_parity",
    data_source: "reports_only",
    tick_schedule: "journal_ticks",
    range_start_utc: null,
    range_end_utc: null,
    snapshot_dir: null,
    use_journal_barriers: false,
  };

  it("applies preset overrides when preset selection changes", () => {
    const next = updateConfigValues(
      { preset: "custom", enable_dynamic_sizing: false },
      "preset",
      "hl_dynamic_session_parity",
      PRESET_OVERRIDES,
    );
    expect(next.preset).toBe("hl_dynamic_session_parity");
    expect(next.enable_dynamic_sizing).toBe(true);
    expect(next.formal_notional_quote).toBe(500);
  });

  it("switches to custom when a non-preset field changes", () => {
    const next = updateConfigValues(
      base,
      "enable_dynamic_sizing",
      false,
      PRESET_OVERRIDES,
    );
    expect(next.preset).toBe("custom");
    expect(next.enable_dynamic_sizing).toBe(false);
    expect(next.formal_notional_quote).toBe(500);
  });

  it("keeps named preset when value is unchanged", () => {
    const next = updateConfigValues(
      base,
      "sl_pct",
      1.8,
      PRESET_OVERRIDES,
    );
    expect(next.preset).toBe("hl_dynamic_session_parity");
    expect(next.sl_pct).toBe(1.8);
  });

  it("switches to custom when a preset-owned field diverges", () => {
    const next = updateConfigValues(
      base,
      "session_nums",
      "37,58",
      PRESET_OVERRIDES_WITH_SESSION,
    );
    expect(next.preset).toBe("custom");
    expect(next.session_nums).toBe("37,58");
  });

  it("keeps named preset when timeline range changes", () => {
    const timelineBase = {
      ...base,
      preset: "hl_dynamic_timeline_refine_v5_winner_binance_1y",
      replay_mode: "timeline_backtest",
      data_source: "snapshots",
      range_start_utc: "2026-01-01T00:00:00Z",
      range_end_utc: "2026-06-01T00:00:00Z",
    };
    const next = updateConfigValues(
      timelineBase,
      "range_start_utc",
      "2026-03-01T00:00:00Z",
      PRESET_OVERRIDES,
    );
    expect(next.preset).toBe("hl_dynamic_timeline_refine_v5_winner_binance_1y");
    expect(next.range_start_utc).toBe("2026-03-01T00:00:00Z");
  });

  it("keeps named preset when infra fields outside preset overrides change", () => {
    const next = updateConfigValues(
      {
        ...base,
        preset: "hl_dynamic_timeline_refine_v5_winner_binance_1y",
        replay_mode: "timeline_backtest",
        data_source: "snapshots",
        max_auto_snapshot_days: 14,
      },
      "max_auto_snapshot_days",
      21,
      PRESET_OVERRIDES,
    );
    expect(next.preset).toBe("hl_dynamic_timeline_refine_v5_winner_binance_1y");
    expect(next.max_auto_snapshot_days).toBe(21);
  });

  it("does not demote preset for fields absent from overrides", () => {
    expect(
      shouldDemotePresetToCustom(
        "max_auto_snapshot_days",
        21,
        "hl_dynamic_session_parity",
        PRESET_OVERRIDES,
      ),
    ).toBe(false);
  });

  it("does not re-apply overrides when switching to custom", () => {
    const next = updateConfigValues(
      base,
      "preset",
      "custom",
      PRESET_OVERRIDES,
    );
    expect(next).toEqual({ ...base, preset: "custom" });
  });
});

describe("reconcileReplayConfigValues", () => {
  it("clears session fields when timeline backtest is selected", () => {
    const next = reconcileReplayConfigValues({
      replay_mode: "timeline_backtest",
      data_source: "journal_first",
      session_nums: "58",
      tick_schedule: "journal_ticks",
      compare_journal_flags: true,
      use_journal_barriers: true,
    });
    expect(next.session_nums).toBeUndefined();
    expect(next.data_source).toBe("snapshots");
    expect(next.tick_schedule).toBe("date_range");
    expect(next.compare_journal_flags).toBe(false);
    expect(next.use_journal_barriers).toBe(false);
    expect(next.config_source).toBe("preset");
  });

  it("clears timeline fields when session parity is selected", () => {
    const next = reconcileReplayConfigValues({
      replay_mode: "session_parity",
      data_source: "snapshots",
      range_start_utc: "2026-01-01T00:00:00Z",
      range_end_utc: "2026-06-01T00:00:00Z",
      snapshot_dir: "data/replay_snapshots_binance_1y",
      tick_schedule: "date_range",
    });
    expect(next.range_start_utc).toBeNull();
    expect(next.range_end_utc).toBeNull();
    expect(next.snapshot_dir).toBeNull();
    expect(next.data_source).toBe("journal_first");
    expect(next.tick_schedule).toBe("journal_ticks");
  });

  it("preserves user snapshot_dir when applying timeline preset overrides", () => {
    const next = applyPresetOverrides(
      {
        preset: "hl_dynamic_timeline_refine_v5_winner_binance_1y",
        snapshot_dir: "data/replay_snapshots_binance_1y",
        session_nums: "58",
      },
      PRESET_OVERRIDES,
    );
    expect(next.snapshot_dir).toBe("data/replay_snapshots_binance_1y");
    expect(next.session_nums).toBeUndefined();
    expect(next.replay_mode).toBe("timeline_backtest");
  });

  it("promotes timeline when user selects snapshots", () => {
    const next = reconcileReplayConfigValues(
      {
        replay_mode: "session_parity",
        data_source: "snapshots",
      },
      "data_source",
    );
    expect(next.replay_mode).toBe("timeline_backtest");
    expect(next.data_source).toBe("snapshots");
  });
});

describe("getDisabledSelectOptions", () => {
  it("disables journal sources during timeline backtest", () => {
    const disabled = getDisabledSelectOptions("data_source", {
      replay_mode: "timeline_backtest",
      data_source: "snapshots",
    });
    expect(disabled.has("journal_first")).toBe(true);
    expect(disabled.has("snapshots")).toBe(false);
  });

  it("disables snapshots during session parity", () => {
    const disabled = getDisabledSelectOptions("data_source", {
      replay_mode: "session_parity",
      data_source: "journal_first",
    });
    expect(disabled.has("snapshots")).toBe(true);
    expect(disabled.has("journal_first")).toBe(false);
  });
});
