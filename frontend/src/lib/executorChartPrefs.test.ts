import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ExecutorInfo } from "@/lib/api";
import {
  EXECUTOR_CHART_DEFAULTS,
  intervalToSeconds,
  loadExecutorChartPrefs,
  resolveDefaultInterval,
  resolveExecutorChartKind,
  saveExecutorChartPref,
  saveExecutorChartPrefs,
} from "@/lib/executorChartPrefs";

const STORAGE_KEY = "condor_executor_chart_intervals";

function createStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

describe("executorChartPrefs", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", createStorage());
  });

  it("returns defaults when storage is empty", () => {
    expect(loadExecutorChartPrefs()).toEqual(EXECUTOR_CHART_DEFAULTS);
  });

  it("loads valid saved prefs", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ live: "5m", terminated: "1h" }),
    );
    expect(loadExecutorChartPrefs()).toEqual({ live: "5m", terminated: "1h" });
  });

  it("falls back to defaults for invalid saved values", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ live: "15m", terminated: "bogus" }),
    );
    expect(loadExecutorChartPrefs()).toEqual(EXECUTOR_CHART_DEFAULTS);
  });

  it("saveExecutorChartPref updates one kind", () => {
    saveExecutorChartPref("terminated", "1d");
    expect(loadExecutorChartPrefs()).toEqual({
      live: "1m",
      terminated: "1d",
    });
  });

  it("saveExecutorChartPrefs round-trips both kinds", () => {
    saveExecutorChartPrefs({ live: "30m", terminated: "1h" });
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toEqual({
      live: "30m",
      terminated: "1h",
    });
  });

  it("ignores corrupt storage", () => {
    localStorage.setItem(STORAGE_KEY, "{not json");
    expect(loadExecutorChartPrefs()).toEqual(EXECUTOR_CHART_DEFAULTS);
  });

  describe("intervalToSeconds", () => {
    it("maps supported intervals", () => {
      expect(intervalToSeconds("30m")).toBe(1800);
      expect(intervalToSeconds("1h")).toBe(3600);
      expect(intervalToSeconds("1d")).toBe(86400);
    });

    it("falls back to 60 for unknown intervals", () => {
      expect(intervalToSeconds("unknown")).toBe(60);
    });
  });

  describe("resolveExecutorChartKind", () => {
    it("returns live when any executor is active", () => {
      const executors = [
        { id: "a", status: "stopped" },
        { id: "b", status: "running" },
      ] as ExecutorInfo[];
      expect(resolveExecutorChartKind(executors)).toBe("live");
    });

    it("returns terminated when all executors are inactive", () => {
      const executors = [{ id: "a", status: "stopped" }] as ExecutorInfo[];
      expect(resolveExecutorChartKind(executors)).toBe("terminated");
    });
  });

  describe("resolveDefaultInterval", () => {
    it("uses live pref for active executors", () => {
      saveExecutorChartPrefs({ live: "5m", terminated: "1h" });
      const executors = [{ id: "a", status: "running" }] as ExecutorInfo[];
      expect(resolveDefaultInterval(executors)).toBe("5m");
    });

    it("uses terminated pref for stopped executors", () => {
      saveExecutorChartPrefs({ live: "5m", terminated: "1h" });
      const executors = [{ id: "a", status: "stopped" }] as ExecutorInfo[];
      expect(resolveDefaultInterval(executors)).toBe("1h");
    });
  });
});
