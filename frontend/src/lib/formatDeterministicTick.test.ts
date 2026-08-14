import { describe, expect, it } from "vitest";

import {
  formatDeterministicTick,
  humanizeHoldReason,
  parseJournalKvChips,
} from "@/lib/formatDeterministicTick";

describe("humanizeHoldReason", () => {
  it("maps known reasons", () => {
    expect(humanizeHoldReason("no_entry_candidate")).toBe("no entry candidate");
    expect(humanizeHoldReason("at_max_open_executors")).toBe("at max open positions");
  });

  it("falls back to underscored text", () => {
    expect(humanizeHoldReason("custom_gate")).toBe("custom gate");
  });
});

describe("formatDeterministicTick", () => {
  it("describes a hold with a reason", () => {
    const formatted = formatDeterministicTick({
      id: "1:2",
      hold_reason: "no_entry_candidate",
      creates: 0,
      stops: 0,
      apply_ok: true,
    });
    expect(formatted.title).toBe("Held — no entry candidate");
    expect(formatted.isError).toBe(false);
  });

  it("describes opens with pair chips from decide.scores", () => {
    const formatted = formatDeterministicTick({
      id: "1:3",
      creates: 1,
      stops: 0,
      apply_ok: true,
      raw: { decide: { creates: 1, scores: { "BTC-USD": 1.2 } } },
    });
    expect(formatted.title).toBe("Opened 1 position");
    expect(formatted.pairs).toEqual(["BTC-USD"]);
  });

  it("describes barrier closes with pnl", () => {
    const formatted = formatDeterministicTick({
      id: "1:4",
      creates: 0,
      stops: 1,
      apply_ok: true,
      raw: {
        barrier_closes: [{ pair: "ETH-USD", close_type: "STOP_LOSS", pnl: -12.5 }],
      },
    });
    expect(formatted.title).toBe("Closed 1 position");
    expect(formatted.closes).toEqual([
      { pair: "ETH-USD", closeType: "stop loss", pnl: -12.5 },
    ]);
  });

  it("surfaces apply failures", () => {
    const formatted = formatDeterministicTick({
      id: "1:5",
      creates: 1,
      apply_ok: false,
      raw: { apply: { ok: false, error: "insufficient margin" } },
    });
    expect(formatted.isError).toBe(true);
    expect(formatted.title).toContain("insufficient margin");
  });
});

describe("parseJournalKvChips", () => {
  it("splits k=v reasoning into chips and skips empty values", () => {
    expect(
      parseJournalKvChips(
        "entry_class=hold hold_reason=no_entry_candidate armed_pairs= best_score=0.0",
      ),
    ).toEqual([
      { key: "entry class", value: "hold" },
      { key: "hold reason", value: "no_entry_candidate" },
      { key: "best score", value: "0.0" },
    ]);
  });
});
