import { describe, expect, it } from "vitest";

import {
  dedupeExecutorsById,
  mergeExecutorPagesWithWs,
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
    expect(merged[1].executors[0]).toEqual({ id: "active", pnl: 5, status: "running" });
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
});
