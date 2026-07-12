import type { QueryClient } from "@tanstack/react-query";
import type { RoutineInfo } from "@/lib/api";

// ── Config persistence ──

export const ROUTINE_CONFIG_KEY_PREFIX = "routine_config:";

const JOURNAL_DATA_SOURCES = new Set(["journal_first", "journal_recompute", "html_only"]);

const USER_WINS_AFTER_PRESET_KEYS = [
  "snapshot_dir",
  "hl_cache_dir",
  "range_start_utc",
  "range_end_utc",
] as const;

/** Options that cannot be selected until driver fields are changed first. */
export function getDisabledSelectOptions(
  fieldKey: string,
  values: Record<string, unknown>,
): Set<string> {
  const disabled = new Set<string>();
  const replayMode = String(values.replay_mode ?? "session_parity");

  if (fieldKey === "data_source") {
    if (replayMode === "timeline_backtest") {
      for (const source of JOURNAL_DATA_SOURCES) {
        disabled.add(source);
      }
    }
    if (replayMode === "session_parity") {
      disabled.add("snapshots");
    }
  }

  return disabled;
}

export function loadSavedConfig(routineName: string): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(ROUTINE_CONFIG_KEY_PREFIX + routineName);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveConfig(routineName: string, values: Record<string, unknown>): void {
  try {
    localStorage.setItem(ROUTINE_CONFIG_KEY_PREFIX + routineName, JSON.stringify(values));
  } catch {
    // storage full or unavailable
  }
}

/** Keep replay driver fields consistent when modes conflict. */
export function reconcileReplayConfigValues(
  values: Record<string, unknown>,
  changedKey?: string,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...values };

  if (changedKey === "data_source") {
    if (next.data_source === "snapshots") {
      next.replay_mode = "timeline_backtest";
    } else if (JOURNAL_DATA_SOURCES.has(String(next.data_source ?? ""))) {
      next.replay_mode = "session_parity";
    }
  } else if (changedKey === "replay_mode") {
    if (next.replay_mode === "timeline_backtest") {
      if (JOURNAL_DATA_SOURCES.has(String(next.data_source ?? ""))) {
        next.data_source = "snapshots";
      }
    } else if (next.replay_mode === "session_parity" && next.data_source === "snapshots") {
      next.data_source = "journal_first";
    }
  } else if (changedKey === "tick_schedule") {
    if (next.tick_schedule === "date_range") {
      next.replay_mode = "timeline_backtest";
    } else if (next.tick_schedule === "journal_ticks") {
      next.replay_mode = "session_parity";
    }
  }

  const replayMode = String(next.replay_mode ?? "session_parity");

  if (replayMode === "timeline_backtest") {
    delete next.session_nums;
    next.compare_journal_flags = false;
    next.config_source = "preset";
    next.use_journal_barriers = false;
    next.tick_schedule = "date_range";
    if (JOURNAL_DATA_SOURCES.has(String(next.data_source ?? ""))) {
      next.data_source = "snapshots";
    }
    if (next.data_source === "snapshots") {
      next.price_source = "reports";
    }
  } else {
    next.range_start_utc = null;
    next.range_end_utc = null;
    next.snapshot_dir = null;
    next.tick_schedule = "journal_ticks";
    if (next.data_source === "snapshots" && changedKey !== "data_source") {
      next.data_source = "journal_first";
    }
  }

  if (next.data_source === "reports_only") {
    next.use_journal_barriers = false;
  }

  return next;
}

export function buildConfigValues(routine: RoutineInfo): Record<string, unknown> {
  const saved = loadSavedConfig(routine.name);
  const values: Record<string, unknown> = {};
  for (const [key, field] of Object.entries(routine.fields)) {
    if (saved && key in saved) {
      values[key] = saved[key];
    } else {
      values[key] = field.default;
    }
  }
  return reconcileReplayConfigValues(applyPresetOverrides(values, routine.preset_overrides));
}

/** Merge preset override values when a named preset is active (not custom). */
export function applyPresetOverrides(
  values: Record<string, unknown>,
  presetOverrides?: Record<string, Record<string, unknown>>,
  options?: { fromPresetChange?: boolean },
): Record<string, unknown> {
  const preset = values.preset;
  if (!preset || preset === "custom" || !presetOverrides) {
    return reconcileReplayConfigValues(values);
  }
  const overrides = presetOverrides[String(preset)];
  if (!overrides) {
    return reconcileReplayConfigValues(values);
  }
  const next = { ...values, ...overrides };
  if (!options?.fromPresetChange) {
    for (const key of USER_WINS_AFTER_PRESET_KEYS) {
      const userVal = values[key];
      if (userVal != null && userVal !== "" && userVal !== overrides[key]) {
        next[key] = userVal;
      }
    }
  }
  return reconcileReplayConfigValues(next);
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
  if (stringA !== "" && stringB !== "" && !Number.isNaN(numberA) && !Number.isNaN(numberB)) {
    return numberA === numberB;
  }
  return false;
}

const USER_WINS_AFTER_PRESET_KEY_SET = new Set<string>(USER_WINS_AFTER_PRESET_KEYS);

/** True when a field edit should drop the active named preset to custom. */
export function shouldDemotePresetToCustom(
  key: string,
  value: unknown,
  activePreset: unknown,
  presetOverrides?: Record<string, Record<string, unknown>>,
): boolean {
  if (!activePreset || activePreset === "custom" || key === "preset") {
    return false;
  }
  if (USER_WINS_AFTER_PRESET_KEY_SET.has(key)) {
    return false;
  }
  const overrides = presetOverrides?.[String(activePreset)];
  if (!overrides || !(key in overrides)) {
    return false;
  }
  return !configValuesEqual(value, overrides[key]);
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
      return applyPresetOverrides(next, presetOverrides, { fromPresetChange: true });
    }
    return reconcileReplayConfigValues(next);
  }

  const next = reconcileReplayConfigValues({ ...prev, [key]: value }, key);
  if (shouldDemotePresetToCustom(key, value, prev.preset, presetOverrides)) {
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

export function invalidateRoutineQueries(qc: QueryClient, routineName?: string): void {
  qc.invalidateQueries({ queryKey: ["routine-instances"] });
  qc.invalidateQueries({ queryKey: ["reports-grouped"] });
  qc.invalidateQueries({ queryKey: ["routines"] });
  if (routineName) {
    qc.invalidateQueries({ queryKey: ["routine-reports", routineName] });
  }
}
