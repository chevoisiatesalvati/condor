import type { ExecutorInfo } from "./api";
import { resolveExecutorConfig } from "./executors";
import { resolveExecutorCloseTimestamp, resolveExecutorSide, resolveExecutorTimestamp } from "./formatters";
import { getThemeColors, pnlHexColor, sideColor } from "./theme-colors";

// ── Overlay Model ──

export interface PriceLine {
  price: number;
  label: string;
  color: string;
  style: "solid" | "dashed" | "dotted";
  lineWidth?: number;
}

export interface ChartMarker {
  time: number;
  price: number;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle";
  color: string;
  text: string;
}

/** A line segment connecting entry → exit on the chart */
export interface ExecutorSegment {
  entryTime: number;
  entryPrice: number;
  exitTime: number;
  exitPrice: number;
  color: string;
}

/** A box representing a grid executor's price range over time */
export interface GridBox {
  startTime: number;
  endTime: number;
  startPrice: number;
  endPrice: number;
  limitPrice?: number;
  color: string;
}

export interface ExecutorOverlay {
  executorId: string;
  type: string;
  side: "buy" | "sell";
  status: string;
  closeType: string;
  pnl: number;
  pnlPct: number;
  volume: number;
  fees: number;
  /** Full-width price lines (only shown for ≤ 1 executor) */
  priceLines: PriceLine[];
  markers: ChartMarker[];
  /** Entry→exit segment line (position/generic executors) */
  segment?: ExecutorSegment;
  /** Grid range box (grid executors) */
  gridBox?: GridBox;
  timeRange: { start: number; end: number };
  /** Original executor config for rich tooltips */
  config?: Record<string, unknown>;
  /** Entry price for display */
  entryPrice?: number;
  /** Exit/current price for display */
  exitPrice?: number;
}

// ── Helpers ──

function normSide(executor: ExecutorInfo): "buy" | "sell" {
  const label = resolveExecutorSide(executor);
  if (label === "SELL") return "sell";
  return "buy";
}

function closeTypeLabel(closeType: string): string {
  const ct = closeType?.toLowerCase() ?? "";
  if (ct.includes("take_profit") || ct.includes("tp")) return "TP";
  if (ct.includes("stop_loss") || ct.includes("sl")) return "SL";
  if (ct.includes("trailing")) return "TS";
  if (ct.includes("time_limit")) return "TL";
  if (ct.includes("early_stop")) return "ES";
  return ct ? ct.replace(/_/g, " ") : "closed";
}

function isActiveStatus(status: string): boolean {
  const s = status?.toLowerCase() ?? "";
  return s === "running" || s === "active_position" || s === "active";
}

function executorOpenTime(executor: ExecutorInfo): number {
  return resolveExecutorTimestamp(executor);
}

function executorCloseTime(executor: ExecutorInfo): number {
  return resolveExecutorCloseTimestamp(executor);
}

// ── Position Executor Overlay ──

function computePositionOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const customInfo = executor.custom_info || {};
  const side = normSide(executor);
  const config = resolveExecutorConfig(executor);
  const tripleBarrier = (() => {
    const raw = config.triple_barrier_config;
    if (!raw) return config;
    if (typeof raw === "string") {
      try {
        const parsed = JSON.parse(raw);
        return typeof parsed === "object" && parsed
          ? { ...config, ...(parsed as Record<string, unknown>) }
          : config;
      } catch {
        return config;
      }
    }
    return typeof raw === "object" ? { ...config, ...(raw as Record<string, unknown>) } : config;
  })();
  const entry =
    Number(customInfo.current_position_average_price) ||
    executor.entry_price ||
    0;
  const closePrice =
    Number(customInfo.close_price) ||
    executor.current_price ||
    0;
  const lines: PriceLine[] = [];
  const markers: ChartMarker[] = [];

  // Entry price line (shown only for single-executor view)
  if (entry > 0) {
    lines.push({
      price: entry,
      label: "Entry",
      color: "#ffffff",
      style: "solid",
      lineWidth: 2,
    });
  }

  // Stop Loss
  const slPct = Number(tripleBarrier.stop_loss ?? config.stop_loss);
  if (entry > 0 && slPct > 0 && slPct !== -1) {
    const slPrice = side === "buy" ? entry * (1 - slPct) : entry * (1 + slPct);
    lines.push({
      price: slPrice,
      label: `SL (${(slPct * 100).toFixed(1)}%)`,
      color: getThemeColors().red,
      style: "dashed",
    });
  }

  // Take Profit
  const tpPct = Number(tripleBarrier.take_profit ?? config.take_profit);
  if (entry > 0 && tpPct > 0 && tpPct !== -1) {
    const tpPrice = side === "buy" ? entry * (1 + tpPct) : entry * (1 - tpPct);
    lines.push({
      price: tpPrice,
      label: `TP (${(tpPct * 100).toFixed(1)}%)`,
      color: getThemeColors().green,
      style: "dashed",
    });
  }

  // Trailing stop
  const tsActivation = Number(config.trailing_stop_activation_price_delta);
  if (entry > 0 && tsActivation > 0) {
    const activationPrice =
      side === "buy" ? entry * (1 + tsActivation) : entry * (1 - tsActivation);
    lines.push({
      price: activationPrice,
      label: "TS Activation",
      color: "#f59e0b",
      style: "dotted",
    });
  }

  // Break-even
  const breakEven = Number(customInfo.break_even_price ?? customInfo.breakeven_price);
  if (breakEven > 0) {
    lines.push({
      price: breakEven,
      label: "Break-even",
      color: "#eab308",
      style: "dotted",
    });
  }

  // Close price line
  if (closePrice > 0 && closePrice !== entry) {
    const pnlPositive = side === "buy" ? closePrice > entry : closePrice < entry;
    lines.push({
      price: closePrice,
      label: "Close",
      color: pnlHexColor(pnlPositive ? 1 : -1),
      style: "dashed",
    });
  }

  // Segment: entry → exit
  let segment: ExecutorSegment | undefined;
  const openTime = executorOpenTime(executor);
  if (entry > 0 && openTime > 0) {
    const exitP = closePrice > 0 ? closePrice : entry;
    const closeTime = executorCloseTime(executor);
    const exitT = closeTime > 0 ? closeTime : Math.floor(Date.now() / 1000);
    segment = {
      entryTime: openTime,
      entryPrice: entry,
      exitTime: exitT,
      exitPrice: exitP,
      color: pnlHexColor(executor.pnl),
    };
  }

  // Entry marker
  if (entry > 0 && openTime > 0) {
    markers.push({
      time: openTime,
      price: entry,
      position: side === "buy" ? "belowBar" : "aboveBar",
      shape: side === "buy" ? "arrowUp" : "arrowDown",
      color: sideColor(side),
      text: side === "buy" ? "BUY" : "SELL",
    });
  }

  // Close marker
  const closeTime = executorCloseTime(executor);
  if (closeTime > 0 && (entry > 0 || closePrice > 0)) {
    const markerPrice = closePrice > 0 ? closePrice : entry;
    markers.push({
      time: closeTime,
      price: markerPrice,
      position: side === "buy" ? "aboveBar" : "belowBar",
      shape: "circle",
      color: segment?.color ?? "#6b7280",
      text: closeTypeLabel(executor.close_type),
    });
  }

  const start = openTime > 0 ? openTime : Math.floor(Date.now() / 1000);
  const end = closeTime > 0 ? closeTime : Math.floor(Date.now() / 1000);

  return {
    executorId: executor.id,
    type: "position",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: lines,
    markers,
    segment,
    timeRange: { start, end },
    config,
    entryPrice: entry,
    exitPrice: closePrice,
  };
}

// ── Grid Executor Overlay ──

function computeGridOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const side = normSide(executor);
  const config = executor.config || {};

  const startPrice = Number(config.start_price);
  const endPrice = Number(config.end_price);
  const limitPrice = Number(config.limit_price);

  const openTime = executorOpenTime(executor);
  const closeTime = executorCloseTime(executor);
  const start = openTime > 0 ? openTime : Math.floor(Date.now() / 1000);
  const end = closeTime > 0 ? closeTime : Math.floor(Date.now() / 1000);

  // Grid box: rectangle from start_price to end_price over the executor lifetime
  let gridBox: GridBox | undefined;
  if (startPrice > 0 && endPrice > 0 && start > 0) {
    const profitable = executor.pnl >= 0;
    gridBox = {
      startTime: start,
      endTime: end,
      startPrice,
      endPrice,
      limitPrice: limitPrice > 0 ? limitPrice : undefined,
      color: pnlHexColor(profitable ? 1 : -1),
    };
  }

  return {
    executorId: executor.id,
    type: "grid",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: [],
    markers: [],
    gridBox,
    timeRange: { start, end },
    config: executor.config,
    entryPrice: startPrice,
    exitPrice: endPrice,
  };
}

// ── Order Executor Overlay ──

function computeOrderOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const customInfo = executor.custom_info || {};
  const config = executor.config || {};
  const side = normSide(executor);
  const lines: PriceLine[] = [];
  const markers: ChartMarker[] = [];

  const isChaser = String(config.execution_strategy ?? "").toUpperCase() === "LIMIT_CHASER";

  const orderPrice =
    (executor.entry_price > 0 ? executor.entry_price : 0) ||
    (Number(config.price) > 0 ? Number(config.price) : 0) ||
    (executor.current_price > 0 ? executor.current_price : 0) ||
    0;
  const closePrice =
    Number(customInfo.close_price) ||
    executor.current_price ||
    0;

  // Build descriptive label: "BUY 0.5" or "SELL 1.2 chasing"
  const amount = Number(config.amount);
  const sideLabel = side.toUpperCase();
  const amountStr = amount > 0 ? ` ${amount}` : "";
  const chaserSuffix = isChaser ? " chasing" : "";
  const descriptiveLabel = `${sideLabel}${amountStr}${chaserSuffix}`;

  const active = isActiveStatus(executor.status);
  const openTime = executorOpenTime(executor);
  const closeTime = executorCloseTime(executor);
  const start = openTime > 0 ? openTime : Math.floor(Date.now() / 1000);
  const end = closeTime > 0 ? closeTime : Math.floor(Date.now() / 1000);

  let segment: ExecutorSegment | undefined;

  if (active && orderPrice > 0) {
    // Active/running: horizontal line at order price
    segment = {
      entryTime: start,
      entryPrice: orderPrice,
      exitTime: Math.floor(Date.now() / 1000),
      exitPrice: orderPrice,
      color: sideColor(side),
    };

    if (orderPrice > 0) {
      lines.push({
        price: orderPrice,
        label: descriptiveLabel,
        color: sideColor(side),
        style: isChaser ? "dotted" : "solid",
        lineWidth: 2,
      });
    }
  } else if (!active && orderPrice > 0) {
    // Finished: triangle marker at execution point
    const fillPrice = closePrice > 0 ? closePrice : orderPrice;

    // Entry marker
    markers.push({
      time: start,
      price: orderPrice,
      position: side === "buy" ? "belowBar" : "aboveBar",
      shape: side === "buy" ? "arrowUp" : "arrowDown",
      color: sideColor(side),
      text: side === "buy" ? "BUY" : "SELL",
    });

    // Close marker (triangle)
    if (closeTime > 0) {
      markers.push({
        time: closeTime,
        price: fillPrice,
        position: side === "buy" ? "aboveBar" : "belowBar",
        shape: side === "buy" ? "arrowDown" : "arrowUp",
        color: pnlHexColor(executor.pnl),
        text: closeTypeLabel(executor.close_type),
      });
    }

    // Short segment from entry to close
    if (closeTime > 0) {
      segment = {
        entryTime: start,
        entryPrice: orderPrice,
        exitTime: closeTime,
        exitPrice: fillPrice,
        color: pnlHexColor(executor.pnl),
      };
    }
  }

  return {
    executorId: executor.id,
    type: "order",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: lines,
    markers,
    segment,
    timeRange: { start, end },
    config: executor.config,
    entryPrice: orderPrice,
    exitPrice: closePrice,
  };
}

// ── Generic Executor Overlay (fallback) ──

function computeGenericOverlay(executor: ExecutorInfo): ExecutorOverlay {
  const customInfo = executor.custom_info || {};
  const side = normSide(executor);
  const lines: PriceLine[] = [];
  const markers: ChartMarker[] = [];
  const entryPrice =
    executor.entry_price ||
    Number(customInfo.current_position_average_price) ||
    0;
  const closePrice =
    executor.current_price ||
    Number(customInfo.close_price) ||
    0;

  if (entryPrice > 0) {
    lines.push({ price: entryPrice, label: "Entry", color: "#ffffff", style: "solid", lineWidth: 2 });
  }
  if (closePrice > 0 && closePrice !== entryPrice) {
    const pnlPositive = side === "buy" ? closePrice > entryPrice : closePrice < entryPrice;
    lines.push({ price: closePrice, label: "Close", color: pnlHexColor(pnlPositive ? 1 : -1), style: "dashed" });
  }

  // Segment
  let segment: ExecutorSegment | undefined;
  const openTime = executorOpenTime(executor);
  const closeTime = executorCloseTime(executor);
  if (entryPrice > 0 && openTime > 0) {
    const exitP = closePrice > 0 ? closePrice : entryPrice;
    const exitT = closeTime > 0 ? closeTime : Math.floor(Date.now() / 1000);
    segment = {
      entryTime: openTime,
      entryPrice: entryPrice,
      exitTime: exitT,
      exitPrice: exitP,
      color: pnlHexColor(executor.pnl),
    };
  }

  if (entryPrice > 0 && openTime > 0) {
    markers.push({
      time: openTime,
      price: entryPrice,
      position: side === "buy" ? "belowBar" : "aboveBar",
      shape: side === "buy" ? "arrowUp" : "arrowDown",
      color: sideColor(side),
      text: side.toUpperCase(),
    });
  }

  if (closeTime > 0 && (entryPrice > 0 || closePrice > 0)) {
    markers.push({
      time: closeTime,
      price: closePrice > 0 ? closePrice : entryPrice,
      position: side === "buy" ? "aboveBar" : "belowBar",
      shape: "circle",
      color: segment?.color ?? "#6b7280",
      text: closeTypeLabel(executor.close_type),
    });
  }

  const start = openTime > 0 ? openTime : Math.floor(Date.now() / 1000);
  const end = closeTime > 0 ? closeTime : Math.floor(Date.now() / 1000);

  return {
    executorId: executor.id,
    type: executor.type?.toLowerCase() || "unknown",
    side,
    status: executor.status,
    closeType: executor.close_type,
    pnl: executor.pnl,
    pnlPct: executor.net_pnl_pct,
    volume: executor.volume,
    fees: executor.cum_fees_quote,
    priceLines: lines,
    markers,
    segment,
    timeRange: { start, end },
    config: executor.config,
    entryPrice: entryPrice,
    exitPrice: closePrice,
  };
}

// ── Public API ──

export function computeExecutorOverlay(executor: ExecutorInfo): ExecutorOverlay {
  switch (executor.type?.toLowerCase()) {
    case "position":
      return computePositionOverlay(executor);
    case "grid":
      return computeGridOverlay(executor);
    case "order":
      return computeOrderOverlay(executor);
    default:
      return computeGenericOverlay(executor);
  }
}

/** PnL-based color: green for profit, red for loss */
export function getExecutorColor(_index: number, pnl?: number): string {
  return pnlHexColor(pnl ?? 0);
}

export function computeMultiOverlays(executors: ExecutorInfo[]): ExecutorOverlay[] {
  const result = executors.map((ex) => computeExecutorOverlay(ex));
  // #region agent log
  for (const o of result) {
    fetch("http://127.0.0.1:7313/ingest/66e6cf39-e791-4256-8122-105d89ec429b", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "644d7b" },
      body: JSON.stringify({
        sessionId: "644d7b",
        hypothesisId: "H1-H3",
        location: "executor-overlays.ts:computeMultiOverlays",
        message: "Overlay segment computed",
        data: {
          executorId: o.executorId,
          type: o.type,
          status: o.status,
          hasSegment: !!o.segment,
          segment: o.segment
            ? {
                entryTime: o.segment.entryTime,
                exitTime: o.segment.exitTime,
                entryPrice: o.segment.entryPrice,
                exitPrice: o.segment.exitPrice,
              }
            : null,
          entryPrice: o.entryPrice,
          exitPrice: o.exitPrice,
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
  }
  // #endregion
  return result;
}

function toSeconds(ts: number): number {
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

export function getOverlayTimeRange(overlays: ExecutorOverlay[]): { start: number; end: number } {
  if (overlays.length === 0) {
    const now = Math.floor(Date.now() / 1000);
    return { start: now - 3600, end: now };
  }
  let start = Infinity;
  let end = -Infinity;
  for (const o of overlays) {
    const s = toSeconds(o.timeRange.start);
    const e = toSeconds(o.timeRange.end);
    if (s < start) start = s;
    if (e > end) end = e;
  }
  return { start, end };
}

/**
 * Group executors by `connector:trading_pair` for per-market charts.
 * Executors without a `trading_pair` are skipped. Insertion order is preserved.
 */
export function groupExecutorsByMarket(
  executors: ExecutorInfo[],
): [string, ExecutorInfo[]][] {
  const groups = new Map<string, ExecutorInfo[]>();
  for (const ex of executors) {
    if (!ex.trading_pair) continue;
    const key = `${ex.connector}:${ex.trading_pair}`;
    const arr = groups.get(key);
    if (arr) arr.push(ex);
    else groups.set(key, [ex]);
  }
  return Array.from(groups.entries());
}
