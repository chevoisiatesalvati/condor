import { describe, expect, it } from "vitest";

import {
  applyPresetOverrides,
  updateConfigValues,
} from "@/lib/routineUtils";

const PRESET_OVERRIDES = {
  hl_dynamic_mega_sweep_best: {
    enable_dynamic_sizing: true,
    formal_notional_quote: 500,
    sl_pct: 1.8,
  },
};

describe("applyPresetOverrides", () => {
  it("merges preset fields when a named preset is active", () => {
    const values = {
      preset: "hl_dynamic_mega_sweep_best",
      enable_dynamic_sizing: false,
      formal_notional_quote: 200,
      session_nums: "58",
    };
    expect(applyPresetOverrides(values, PRESET_OVERRIDES)).toEqual({
      preset: "hl_dynamic_mega_sweep_best",
      enable_dynamic_sizing: true,
      formal_notional_quote: 500,
      session_nums: "58",
      sl_pct: 1.8,
    });
  });

  it("leaves values unchanged for custom preset", () => {
    const values = {
      preset: "custom",
      enable_dynamic_sizing: false,
      formal_notional_quote: 200,
    };
    expect(applyPresetOverrides(values, PRESET_OVERRIDES)).toEqual(values);
  });
});

describe("updateConfigValues", () => {
  const base = {
    preset: "hl_dynamic_mega_sweep_best",
    enable_dynamic_sizing: true,
    formal_notional_quote: 500,
    sl_pct: 1.8,
    session_nums: "58",
  };

  it("applies preset overrides when preset selection changes", () => {
    const next = updateConfigValues(
      { preset: "custom", enable_dynamic_sizing: false },
      "preset",
      "hl_dynamic_mega_sweep_best",
      PRESET_OVERRIDES,
    );
    expect(next.preset).toBe("hl_dynamic_mega_sweep_best");
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
    expect(next.preset).toBe("hl_dynamic_mega_sweep_best");
    expect(next.sl_pct).toBe(1.8);
  });

  it("switches to custom for preset-owned fields outside overrides", () => {
    const next = updateConfigValues(
      base,
      "session_nums",
      "37,58",
      PRESET_OVERRIDES,
    );
    expect(next.preset).toBe("custom");
    expect(next.session_nums).toBe("37,58");
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
