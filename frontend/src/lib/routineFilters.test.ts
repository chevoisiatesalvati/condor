import { describe, expect, it } from "vitest";

import { filterRoutinesBySourceType, routineMatchesAgentFilter } from "./routineFilters";

const backtest = {
  name: "macdbb_scanner_aggressive_hl_backtest",
  source: "global",
};

const otherAgentRoutine = {
  name: "example_agent/helper_routine",
  source: "agent:example_agent",
};

const otherGlobal = {
  name: "market_scanner",
  source: "global",
};

describe("routineMatchesAgentFilter", () => {
  it("includes agent-local routines", () => {
    expect(routineMatchesAgentFilter(otherAgentRoutine, "example_agent")).toBe(true);
  });

  it("includes global routines prefixed with agent slug", () => {
    expect(routineMatchesAgentFilter(backtest, "macdbb_scanner_aggressive_hl")).toBe(true);
  });

  it("excludes unrelated global routines", () => {
    expect(routineMatchesAgentFilter(otherGlobal, "macdbb_scanner_aggressive_hl")).toBe(false);
  });
});

describe("filterRoutinesBySourceType", () => {
  const routines = [backtest, otherAgentRoutine, otherGlobal];

  it("keeps agent-linked global backtests under agent slug filter", () => {
    const filtered = filterRoutinesBySourceType(routines, "macdbb_scanner_aggressive_hl");
    expect(filtered.map((r) => r.name)).toEqual([
      "macdbb_scanner_aggressive_hl_backtest",
    ]);
  });
});
