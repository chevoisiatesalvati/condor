import type { QueryClient } from "@tanstack/react-query";
import type { RoutineInfo } from "@/lib/api";

// ── Config persistence ──

export const ROUTINE_CONFIG_KEY_PREFIX = "routine_config:";

export function loadSavedConfig(
  routineName: string,
): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(ROUTINE_CONFIG_KEY_PREFIX + routineName);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveConfig(
  routineName: string,
  values: Record<string, unknown>,
): void {
  try {
    localStorage.setItem(
      ROUTINE_CONFIG_KEY_PREFIX + routineName,
      JSON.stringify(values),
    );
  } catch {
    // storage full or unavailable
  }
}

export function buildConfigValues(
  routine: RoutineInfo,
): Record<string, unknown> {
  const saved = loadSavedConfig(routine.name);
  const values: Record<string, unknown> = {};
  for (const [key, field] of Object.entries(routine.fields)) {
    if (saved && key in saved) {
      values[key] = saved[key];
    } else {
      values[key] = field.default;
    }
  }
  return applyPresetOverrides(values, routine.preset_overrides);
}

/** Merge preset override values when a named preset is active (not custom). */
export function applyPresetOverrides(
  values: Record<string, unknown>,
  presetOverrides?: Record<string, Record<string, unknown>>,
): Record<string, unknown> {
  const preset = values.preset;
  if (!preset || preset === "custom" || !presetOverrides) {
    return values;
  }
  const overrides = presetOverrides[String(preset)];
  if (!overrides) {
    return values;
  }
  return { ...values, ...overrides };
}

function configValuesEqual(a: unknown, b: unknown): boolean {
  if (a === b) {
    return true;
  }
  if (typeof a === "boolean" && typeof b === "boolean") {
    return a === b;
  }
  if (typeof a === "number" && typeof b === "number") {
    return a === b;
  }
  const stringA = String(a ?? "");
  const stringB = String(b ?? "");
  if (stringA === stringB) {
    return true;
  }
  const numberA = Number(a);
  const numberB = Number(b);
  if (
    stringA !== "" &&
    stringB !== "" &&
    !Number.isNaN(numberA) &&
    !Number.isNaN(numberB)
  ) {
    return numberA === numberB;
  }
  return false;
}

/** Apply a single field change; when preset changes, refresh overridden fields. */
export function updateConfigValues(
  prev: Record<string, unknown>,
  key: string,
  value: unknown,
  presetOverrides?: Record<string, Record<string, unknown>>,
): Record<string, unknown> {
  if (key === "preset") {
    const next = { ...prev, preset: value };
    if (value !== "custom") {
      return applyPresetOverrides(next, presetOverrides);
    }
    return next;
  }

  const next = { ...prev, [key]: value };
  const activePreset = prev.preset;
  if (
    activePreset &&
    activePreset !== "custom" &&
    !configValuesEqual(value, prev[key])
  ) {
    next.preset = "custom";
  }
  return next;
}

// ── Formatters ──

export function formatRoutineName(name: string): string {
  const display = name.includes("/") ? name.split("/").pop()! : name;
  return display.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// Format a schedule interval (in seconds) into a compact label, e.g. 86400 -> "1d"
export function formatInterval(sec: number): string {
  if (sec % 604800 === 0) return `${sec / 604800}w`;
  if (sec % 86400 === 0) return `${sec / 86400}d`;
  if (sec % 3600 === 0) return `${sec / 3600}h`;
  if (sec % 60 === 0) return `${sec / 60}m`;
  return `${sec}s`;
}

// ── Query invalidation ──

export function invalidateRoutineQueries(
  qc: QueryClient,
  routineName?: string,
): void {
  qc.invalidateQueries({ queryKey: ["routine-instances"] });
  qc.invalidateQueries({ queryKey: ["reports-grouped"] });
  qc.invalidateQueries({ queryKey: ["routines"] });
  if (routineName) {
    qc.invalidateQueries({ queryKey: ["routine-reports", routineName] });
  }
}
