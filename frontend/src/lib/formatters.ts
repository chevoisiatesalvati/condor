// ── Centralized Formatters ──

/** Humanize a tool-call name: strip the `mcp__<server>__` prefix and underscores.
 *  e.g. "mcp__condor__manage_routines" → "manage routines", "ToolSearch" → "ToolSearch". */
export function formatToolName(title: string): string {
  const name = title.includes("__") ? title.split("__").pop()! : title;
  return name.replace(/_/g, " ");
}

/** Escape a string for safe interpolation into innerHTML. */
export function escapeHtml(val: string): string {
  return val
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function formatCurrency(val: number, symbol = "$") {
  if (Math.abs(val) >= 1_000_000) return symbol + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return symbol + (val / 1_000).toFixed(1) + "K";
  if (symbol === "$") {
    return val.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
    });
  }
  // Adaptive precision for small values (e.g. BTC)
  if (Math.abs(val) < 0.01 && val !== 0) return symbol + val.toPrecision(4);
  return symbol + val.toFixed(2);
}

/**
 * Compact USD for chart tooltips / stat strips: `>=1M → "$N.NNM"`, `>=10K → "$N.NK"`,
 * else plain `"$" + toFixed(2)` (no locale grouping). Kept distinct from `formatCurrency`,
 * which uses `Intl` grouping/sign placement below 10K — switching would change rendered values.
 */
export function formatCompactUsd(val: number): string {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return "$" + val.toFixed(2);
}

export function formatCurrencyVolume(val: number, symbol = "$") {
  if (Math.abs(val) >= 1_000_000) return symbol + (val / 1_000_000).toFixed(1) + "M";
  if (Math.abs(val) >= 1_000) return symbol + (val / 1_000).toFixed(1) + "K";
  return symbol + val.toFixed(Math.abs(val) < 100 ? 2 : 0);
}

export function formatCurrencyPnl(val: number, symbol = "$") {
  const prefix = val >= 0 ? "+" : "";
  return prefix + formatCurrency(val, symbol);
}

export function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

// Backward-compatible aliases (default to $)
export function formatUsd(val: number) {
  return formatCurrency(val);
}

export function formatVolume(val: number) {
  return formatCurrencyVolume(val);
}

export function formatPnl(val: number) {
  return formatCurrencyPnl(val);
}

/** Normalize a timestamp to seconds (ms timestamps > 1e12 are divided by 1000). */
export function tsToSeconds(ts: number): number {
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

/** Resolve executor open time from REST/WS shapes (top-level, config, created_at). */
export function resolveExecutorTimestamp(
  executor: {
    timestamp?: number;
    created_at?: string | number;
    config?: Record<string, unknown>;
  },
): number {
  const direct = Number(executor.timestamp) || 0;
  if (direct > 0) return tsToSeconds(direct);

  const cfg = executor.config || {};
  const cfgTs = Number(cfg.timestamp) || 0;
  if (cfgTs > 0) return tsToSeconds(cfgTs);

  for (const raw of [executor.created_at, cfg.created_at]) {
    if (raw == null || raw === "") continue;
    if (typeof raw === "number" && raw > 0) return tsToSeconds(raw);
    if (typeof raw === "string") {
      const ms = Date.parse(raw.replace(" ", "T"));
      if (!Number.isNaN(ms)) return Math.floor(ms / 1000);
      const num = Number(raw);
      if (num > 0) return tsToSeconds(num);
    }
  }
  return 0;
}

/** Resolve executor close time from REST/WS shapes. */
export function resolveExecutorCloseTimestamp(
  executor: {
    close_timestamp?: number;
    closed_at?: string | number;
    config?: Record<string, unknown>;
  },
): number {
  const direct = Number(executor.close_timestamp) || 0;
  if (direct > 0) return tsToSeconds(direct);

  for (const raw of [executor.closed_at, executor.config?.closed_at]) {
    if (raw == null || raw === "") continue;
    if (typeof raw === "number" && raw > 0) return tsToSeconds(raw);
    if (typeof raw === "string") {
      const ms = Date.parse(raw.replace(" ", "T"));
      if (!Number.isNaN(ms)) return Math.floor(ms / 1000);
      const num = Number(raw);
      if (num > 0) return tsToSeconds(num);
    }
  }
  return 0;
}

/** Normalize a timestamp (seconds or ms, number or ISO string) to epoch ms. */
export function toMs(ts: string | number): number {
  if (typeof ts === "number") return ts > 1e12 ? ts : ts * 1000;
  const parsed = Date.parse(ts);
  return isNaN(parsed) ? 0 : parsed;
}

/** Format an epoch-ms timestamp as a 24h `HH:MM` time label. */
export function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

/** Format an epoch-ms timestamp as a `Mon D HH:MM` (24h) date-time label. */
export function formatDateTime(ms: number): string {
  const d = new Date(ms);
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" })}`;
}

export function formatAge(timestamp: number): string {
  if (!timestamp) return "\u2014";
  try {
    const now = Date.now();
    const diffMs = now - timestamp * 1000;
    if (diffMs < 0) return "\u2014";
    const days = Math.floor(diffMs / 86400000);
    const hours = Math.floor((diffMs % 86400000) / 3600000);
    if (days > 0) return `${days}d ${hours}h`;
    const mins = Math.floor((diffMs % 3600000) / 60000);
    if (hours > 0) return `${hours}h ${mins}m`;
    if (mins > 0) return `${mins}m`;
    return "<1m";
  } catch {
    return "\u2014";
  }
}

/**
 * Format a timestamp as a relative "Ns/m/h/d ago" label.
 * Accepts epoch-seconds (number), or a Date/ISO string. Numeric ms timestamps
 * (> 1e12) are normalized to seconds. When the value is null/undefined/empty,
 * returns `fallback` (default "" — pass "never" to match instance-style labels).
 */
export function formatRelativeTime(
  value: number | string | Date | null | undefined,
  fallback = "",
): string {
  if (value == null || value === "") return fallback;
  let seconds: number;
  if (typeof value === "number") {
    seconds = tsToSeconds(value);
  } else {
    const ms = value instanceof Date ? value.getTime() : new Date(value).getTime();
    if (Number.isNaN(ms)) return fallback;
    seconds = ms / 1000;
  }
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function formatPrice(val: number): string {
  if (!val) return "\u2014";
  if (val >= 1000) return val.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (val >= 1) return val.toFixed(4);
  return val.toPrecision(4);
}

export function formatPct(val: number): string {
  if (!val) return "\u2014";
  return (val >= 0 ? "+" : "") + (val * 100).toFixed(2) + "%";
}

export function normalizeExecutorType(raw: string): string {
  const label = raw
    .toLowerCase()
    .replace("_executor", "")
    .replace("executor", "")
    .replace(/^_+|_+$/g, "");
  return label || raw;
}

export function isExecutorActive(status: string) {
  const s = (status || "").toLowerCase();
  return s === "active" || s === "running" || s === "active_position";
}

export function executorSideRaw(ex: {
  side?: string;
  config?: { side?: unknown };
  custom_info?: { side?: unknown };
}): unknown {
  return ex.side || ex.config?.side || ex.custom_info?.side;
}

export function formatExecutorSide(side: unknown): "BUY" | "SELL" | "" {
  if (side === null || side === undefined || side === "") return "";
  if (typeof side === "string") {
    const upper = side.toUpperCase();
    if (side === "1" || upper === "BUY" || side === "TradeType.BUY" || upper === "LONG") {
      return "BUY";
    }
    if (side === "2" || upper === "SELL" || side === "TradeType.SELL" || upper === "SHORT") {
      return "SELL";
    }
  }
  if (typeof side === "number") {
    if (side === 1) return "BUY";
    if (side === 2) return "SELL";
    return "";
  }
  const label = String(side).toUpperCase();
  if (label === "LONG") return "BUY";
  if (label === "SHORT") return "SELL";
  if (label === "BUY") return "BUY";
  if (label === "SELL") return "SELL";
  return "";
}

/** Resolve side from executor row, config, and custom_info (never guess SELL). */
export function resolveExecutorSide(executor: {
  side?: unknown;
  config?: Record<string, unknown>;
  custom_info?: Record<string, unknown>;
}): "BUY" | "SELL" | "" {
  for (const candidate of [
    executor.side,
    executor.config?.side,
    executor.custom_info?.side,
  ]) {
    const label = formatExecutorSide(candidate);
    if (label) return label;
  }
  return "";
}
