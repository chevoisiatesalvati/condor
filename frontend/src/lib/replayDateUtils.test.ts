import { describe, expect, it } from "vitest";

import { lastDaysRangeIso } from "@/lib/replayDateUtils";

describe("lastDaysRangeIso", () => {
  it("anchors last 7 days to snapshot end when provided", () => {
    const range = lastDaysRangeIso(7, "2026-06-19T21:00:00Z");
    expect(range.end).toBe("2026-06-19T21:00:00Z");
    expect(range.start).toBe("2026-06-12T00:00:00Z");
  });

  it("uses end-of-day UTC when no anchor is provided", () => {
    const range = lastDaysRangeIso(7);
    expect(range.end.endsWith("Z")).toBe(true);
    expect(range.start.endsWith("Z")).toBe(true);
  });
});
