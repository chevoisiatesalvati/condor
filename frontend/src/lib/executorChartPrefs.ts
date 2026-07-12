import type { ExecutorInfo } from "@/lib/api";
import { isExecutorActive } from "@/lib/formatters";

export const EXECUTOR_CHART_INTERVALS = ["1m", "5m", "30m", "1h", "1d"] as const;
export type ExecutorChartInterval = (typeof EXECUTOR_CHART_INTERVALS)[number];
export type ExecutorChartKind = "live" | "terminated";

const STORAGE_KEY = "condor_executor_chart_intervals";

export const EXECUTOR_CHART_DEFAULTS: Record<ExecutorChartKind, ExecutorChartInterval> = {
  live: "1m",
  terminated: "5m",
};

const INTERVAL_SECONDS: Record<string, number> = {
  "1s": 1,
  "5s": 5,
  "15s": 15,
  "30s": 30,
  "1m": 60,
  "3m": 180,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1h": 3600,
  "2h": 7200,
  "4h": 14400,
  "1d": 86400,
  "1w": 604800,
};

function isValidInterval(value: unknown): value is ExecutorChartInterval {
  return (
    typeof value === "string" &&
    (EXECUTOR_CHART_INTERVALS as readonly string[]).includes(value)
  );
}

export function intervalToSeconds(interval: string): number {
  return INTERVAL_SECONDS[interval] ?? 60;
}

export function loadExecutorChartPrefs(): Record<ExecutorChartKind, ExecutorChartInterval> {
  const merged = { ...EXECUTOR_CHART_DEFAULTS };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return merged;
    const saved = JSON.parse(raw) as Record<string, unknown>;
    if (isValidInterval(saved.live)) merged.live = saved.live;
    if (isValidInterval(saved.terminated)) merged.terminated = saved.terminated;
  } catch {
    // ignore corrupt storage
  }
  return merged;
}

export function saveExecutorChartPref(
  kind: ExecutorChartKind,
  interval: ExecutorChartInterval,
): void {
  const prefs = loadExecutorChartPrefs();
  prefs[kind] = interval;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
}

export function saveExecutorChartPrefs(
  prefs: Record<ExecutorChartKind, ExecutorChartInterval>,
): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
}

export function resolveExecutorChartKind(executors: ExecutorInfo[]): ExecutorChartKind {
  if (executors.length === 0) return "live";
  const anyActive = executors.some((ex) => isExecutorActive(ex.status));
  return anyActive ? "live" : "terminated";
}

export function resolveDefaultInterval(executors: ExecutorInfo[]): ExecutorChartInterval {
  const prefs = loadExecutorChartPrefs();
  return prefs[resolveExecutorChartKind(executors)];
}
