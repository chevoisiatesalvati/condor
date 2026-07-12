import { describe, expect, it } from "vitest";

import { dedupCandlesByTime, dedupSortedByTime } from "@/lib/chart-utils";

describe("dedupSortedByTime", () => {
  it("keeps last value for duplicate timestamps", () => {
    const input = [
      { time: 100, value: 1 },
      { time: 100, value: 2 },
      { time: 101, value: 3 },
    ];
    expect(dedupSortedByTime(input)).toEqual([
      { time: 100, value: 2 },
      { time: 101, value: 3 },
    ]);
  });

  it("sorts out-of-order input", () => {
    const input = [
      { time: 102, value: 3 },
      { time: 100, value: 1 },
      { time: 101, value: 2 },
    ];
    expect(dedupSortedByTime(input).map((p) => p.time)).toEqual([100, 101, 102]);
  });
});

describe("dedupCandlesByTime", () => {
  it("merges OHLC for duplicate candle times", () => {
    const input = [
      { time: 1782883500, open: 10, high: 12, low: 9, close: 11 },
      { time: 1782883500, open: 11, high: 13, low: 8, close: 12 },
      { time: 1782883560, open: 12, high: 14, low: 11, close: 13 },
    ];
    expect(dedupCandlesByTime(input)).toEqual([
      { time: 1782883500, open: 10, high: 13, low: 8, close: 12 },
      { time: 1782883560, open: 12, high: 14, low: 11, close: 13 },
    ]);
  });
});
