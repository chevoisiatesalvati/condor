import { describe, expect, it } from "vitest";

import {
  dedupeExecutorsById,
  enrichExecutorForTerminatedDisplay,
  mergeExecutorConfigs,
  mergeExecutorOverlay,
  mergeExecutorPagesWithWs,
  mergeSessionConfigSources,
  parseControllerSession,
  sessionConfigHasDefaults,
  type ExecutorPage,
} from "@/lib/executors";
import type { ExecutorInfo } from "@/lib/api";

describe("dedupeExecutorsById", () => {
  it("keeps the first occurrence of each id", () => {
    const a = { id: "a", pnl: 1 } as ExecutorInfo;
    const b = { id: "b", pnl: 2 } as ExecutorInfo;
    const aDup = { id: "a", pnl: 9 } as ExecutorInfo;
    expect(dedupeExecutorsById([a, b, aDup])).toEqual([a, b]);
  });
});

describe("mergeExecutorPagesWithWs", () => {
  it("patches executors on later pages by id", () => {
    const pages: ExecutorPage[] = [
      { executors: [{ id: "x", pnl: 0 }], next_cursor: "c1" },
      { executors: [{ id: "active", pnl: 1, status: "running" }], next_cursor: null },
    ];
    const ws = [{ id: "active", pnl: 5, status: "running" }];
    const merged = mergeExecutorPagesWithWs(pages, ws);
    expect(merged[1].executors[0]).toMatchObject({ id: "active", pnl: 5, status: "running" });
    expect(merged[0].executors[0]).toEqual({ id: "x", pnl: 0 });
  });

  it("prepends executors that only exist in ws to page 0", () => {
    const pages: ExecutorPage[] = [
      { executors: [{ id: "old", pnl: 0 }], next_cursor: null },
    ];
    const ws = [{ id: "new", pnl: 2, status: "running" }];
    const merged = mergeExecutorPagesWithWs(pages, ws);
    expect(merged[0].executors.map((ex) => (ex as { id: string }).id)).toEqual([
      "new",
      "old",
    ]);
  });

  it("does not let mid-flight WS wipe persisted open+close fill prices", () => {
    const pages: ExecutorPage[] = [
      {
        executors: [
          {
            id: "cashcat",
            status: "terminated",
            pnl: -7.55,
            entry_price: 0.070362,
            current_price: 0.065692,
            custom_info: {
              current_position_average_price: 0.070362,
              close_price: 0.065692,
              order_ids: ["open", "close"],
            },
          },
        ],
        next_cursor: null,
      },
    ];
    const ws = [
      {
        id: "cashcat",
        status: "terminated",
        pnl: -7.55,
        entry_price: 0.07016,
        current_price: 0.07016,
        custom_info: {
          current_position_average_price: 0.07016,
          close_price: 0.07016,
          order_ids: ["open"],
        },
      },
    ];
    const merged = mergeExecutorPagesWithWs(pages, ws);
    const ex = merged[0].executors[0] as ExecutorInfo;
    expect(ex.custom_info?.current_position_average_price).toBe(0.070362);
    expect(ex.custom_info?.close_price).toBe(0.065692);
    expect(ex.entry_price).toBe(0.070362);
    expect(ex.current_price).toBe(0.065692);
    expect(ex.custom_info?.order_ids).toEqual(["open", "close"]);
  });
});

describe("mergeExecutorOverlay fill preference", () => {
  it("keeps richer REST fills when overlay is a flat mid snapshot", () => {
    const rest = {
      id: "x",
      status: "terminated",
      entry_price: 0.070362,
      current_price: 0.065692,
      custom_info: {
        current_position_average_price: 0.070362,
        close_price: 0.065692,
        order_ids: ["a", "b"],
      },
    } as unknown as ExecutorInfo;
    const ws = {
      id: "x",
      status: "terminated",
      entry_price: 0.07016,
      current_price: 0.07016,
      custom_info: {
        current_position_average_price: 0.07016,
        close_price: 0.07016,
        order_ids: ["a"],
      },
    } as unknown as ExecutorInfo;
    const merged = mergeExecutorOverlay(rest, ws);
    expect(merged.entry_price).toBe(0.070362);
    expect(merged.current_price).toBe(0.065692);
    expect(merged.custom_info?.close_price).toBe(0.065692);
  });
});

describe("mergeExecutorConfigs", () => {
  it("keeps the richer config and fills gaps from the sparser one", () => {
    const sparse = { connector_name: "hl", trading_pair: "BTC-USD" };
    const rich = {
      connector_name: "hl",
      trading_pair: "BTC-USD",
      leverage: 10,
      total_amount_quote: 100,
      stop_loss: 0.02,
      take_profit: 0.04,
    };
    expect(mergeExecutorConfigs(sparse, rich)).toEqual(rich);
    expect(mergeExecutorConfigs(rich, sparse)).toEqual(rich);
  });
});

describe("parseControllerSession", () => {
  it("parses agent strategy session from controller_id", () => {
    expect(
      parseControllerSession("macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_3"),
    ).toEqual({
      slug: "macdbb_scanner_aggressive_hl",
      sslug: "macdbb_scanner_aggressive_hl",
      sessionNum: 3,
    });
  });

  it("parses short controller_id without dot prefix", () => {
    expect(parseControllerSession("macdbb_scanner_aggressive_hl_78")).toEqual({
      slug: "macdbb_scanner_aggressive_hl",
      sslug: "macdbb_scanner_aggressive_hl",
      sessionNum: 78,
    });
  });

  it("returns null for non-session controller ids", () => {
    expect(parseControllerSession("manual_controller")).toBeNull();
  });
});

describe("enrichExecutorForTerminatedDisplay", () => {
  it("fills sparse config from session defaults and journal side", () => {
    const sparse = {
      id: "ex1",
      type: "position",
      connector: "hl",
      trading_pair: "LINK-USD",
      side: "",
      status: "terminated",
      close_type: "",
      pnl: 1,
      volume: 10,
      cum_fees_quote: 0,
      entry_price: 0,
      current_price: 0,
      timestamp: 1,
      controller_id: "a.b_1",
      net_pnl_pct: 0,
      close_timestamp: 0,
      config: { connector_name: "hl", trading_pair: "LINK-USD", controller_id: "a.b_1" },
      custom_info: {},
    } as ExecutorInfo;

    const journalById = new Map([
      ["ex1", { id: "ex1", side: "BUY", amount: 50 }],
    ]);
    const sessionConfig = {
      leverage: 5,
      total_amount_quote: 50,
      strategy_params: { sl_pct: 2, tp_pct: 4 },
    };

    const enriched = enrichExecutorForTerminatedDisplay(sparse, {
      journalById,
      sessionConfig,
    });
    expect(enriched.side).toBe("BUY");
    expect(enriched.config?.leverage).toBe(5);
    expect(enriched.config?.total_amount_quote).toBe(50);
    expect(enriched.config?.stop_loss).toBe(0.02);
    expect(enriched.config?.take_profit).toBe(0.04);
  });
});

describe("mergeSessionConfigSources", () => {
  it("merges strategy defaults under session overrides", () => {
    const merged = mergeSessionConfigSources(
      { total_amount_quote: 250, strategy_params: { sl_pct: 3 } },
      { total_amount_quote: 500, leverage: 5, strategy_params: { tp_pct: 4, sl_pct: 2 } },
    );
    expect(merged?.total_amount_quote).toBe(250);
    expect(merged?.leverage).toBe(5);
    expect((merged?.strategy_params as Record<string, unknown>).sl_pct).toBe(3);
    expect((merged?.strategy_params as Record<string, unknown>).tp_pct).toBe(4);
  });
});

describe("sessionConfigHasDefaults", () => {
  it("returns false for empty objects", () => {
    expect(sessionConfigHasDefaults({})).toBe(false);
  });

  it("detects strategy_params sl/tp", () => {
    expect(sessionConfigHasDefaults({ strategy_params: { sl_pct: 2 } })).toBe(true);
  });
});
