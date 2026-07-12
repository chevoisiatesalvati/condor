import { describe, expect, it } from "vitest";

import { candleMaxDistForInterval, candlePriceAt } from "@/lib/chart-utils";

describe("candlePriceAt", () => {
  const baseTs = 1_700_000_000;
  const candles = [{ timestamp: baseTs, close: 100 }];

  it("accepts nearby candle within default tolerance", () => {
    expect(candlePriceAt(baseTs + 300, candles)).toBe(100);
  });

  it("rejects candle beyond default 600s tolerance", () => {
    expect(candlePriceAt(baseTs + 3600, candles)).toBe(0);
  });

  it("accepts 1h-apart candle when maxDistSec allows it", () => {
    expect(candlePriceAt(baseTs + 1800, candles, 1800)).toBe(100);
  });

  it("rejects when tolerance is too small for coarse interval", () => {
    expect(candlePriceAt(baseTs + 3600, candles, 600)).toBe(0);
  });
});

describe("candleMaxDistForInterval", () => {
  it("returns half the candle period, at least 600s", () => {
    expect(candleMaxDistForInterval("1m")).toBe(600);
    expect(candleMaxDistForInterval("1h")).toBe(1800);
    expect(candleMaxDistForInterval("1d")).toBe(43200);
  });

  it("falls back to 600 for unknown intervals", () => {
    expect(candleMaxDistForInterval("bogus")).toBe(600);
  });
});
