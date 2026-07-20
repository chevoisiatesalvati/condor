import type { AgentExecutorRow, ExecutorInfo, PositionHeld } from "@/lib/api";
import { candleMaxDistForInterval, candlePriceAt } from "@/lib/chart-utils";
import {
  isExecutorActive,
  normalizeExecutorType,
  resolveExecutorCloseTimestamp,
  resolveExecutorSide,
  resolveExecutorTimestamp,
} from "@/lib/formatters";

export type ExecutorPage = { executors: unknown[]; next_cursor: string | null };

function parseTripleBarrier(raw: unknown): Record<string, unknown> {
  if (!raw) return {};
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      return typeof parsed === "object" && parsed ? (parsed as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }
  return typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

/** Merge nested config + triple_barrier fields for chart tooltips and detail panels. */
export function resolveExecutorConfig(executor: ExecutorInfo): Record<string, unknown> {
  const cfg = { ...(executor.config || {}) };
  const tripleBarrier = parseTripleBarrier(cfg.triple_barrier_config);
  for (const key of ["stop_loss", "take_profit", "time_limit", "trailing_stop"] as const) {
    if (tripleBarrier[key] != null && cfg[key] == null) {
      cfg[key] = tripleBarrier[key];
    }
  }
  return cfg;
}

export type ControllerSessionRef = {
  slug: string;
  sslug: string;
  sessionNum: number;
};

/** Parse agent strategy session from HB controller_id (dotted or short form). */
export function parseControllerSession(
  controllerId: string | undefined,
): ControllerSessionRef | null {
  if (!controllerId) return null;
  const dotted = /^([^.]+)\.([^.]+)_(e?)(\d+)$/.exec(controllerId);
  if (dotted) {
    const sessionNum = Number(dotted[4]);
    if (Number.isFinite(sessionNum) && sessionNum > 0) {
      return { slug: dotted[1], sslug: dotted[2], sessionNum };
    }
  }
  const short = /^([a-z0-9_]+)_(\d+)$/i.exec(controllerId);
  if (short) {
    const slug = short[1];
    const sessionNum = Number(short[2]);
    if (Number.isFinite(sessionNum) && sessionNum > 0) {
      return { slug, sslug: slug, sessionNum };
    }
  }
  return null;
}

/** Mirror backend controller_ids_for_lookup for WS filtering. */
export function controllerIdsForLookup(agentId: string): string[] {
  return [agentId];
}

function configRichness(config: Record<string, unknown> | undefined): number {
  if (!config) return 0;
  let score = Object.keys(config).length;
  const tb = config.triple_barrier_config;
  if (tb && typeof tb === "object") {
    score += Object.keys(tb as Record<string, unknown>).length;
  }
  if (config.leverage != null) score += 2;
  if (config.total_amount_quote != null || config.amount != null) score += 2;
  return score;
}

/** Prefer the richer config; overlay fills gaps without dropping sparse base keys. */
export function mergeExecutorConfigs(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): Record<string, unknown> {
  const richA = configRichness(a);
  const richB = configRichness(b);
  const base = richA >= richB ? b : a;
  const overlay = richA >= richB ? a : b;
  return { ...base, ...overlay };
}

/** Count real client order ids in custom_info (fill richness signal). */
export function executorOrderIdCount(customInfo: Record<string, unknown> | undefined): number {
  const ids = customInfo?.order_ids;
  if (!Array.isArray(ids)) return 0;
  return ids.filter((id) => id != null && String(id).length > 0).length;
}

/**
 * Score fill-price quality in custom_info. Higher is better for terminated display.
 * Mid-flight WS snapshots often have 1 order id and entry==close (live mid).
 * Persisted fills have open+close ids and distinct entry/close VWAPs.
 */
export function executorFillInfoQuality(customInfo: Record<string, unknown> | undefined): number {
  const avg = Number(customInfo?.current_position_average_price) || 0;
  const close = Number(customInfo?.close_price) || 0;
  let score = executorOrderIdCount(customInfo) * 10;
  if (avg > 0) score += 1;
  if (close > 0) score += 1;
  if (avg > 0 && close > 0 && Math.abs(avg - close) / Math.max(avg, 1e-18) > 1e-9) {
    score += 5;
  }
  return score;
}

/**
 * Merge custom_info without letting a mid-flight WS snapshot overwrite
 * richer persisted fill prices (open+close order ids / distinct VWAP).
 */
export function mergeExecutorCustomInfo(
  base: Record<string, unknown> | undefined,
  overlay: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const baseInfo = base ?? {};
  const overlayInfo = overlay ?? {};
  if (executorFillInfoQuality(overlayInfo) >= executorFillInfoQuality(baseInfo)) {
    return { ...baseInfo, ...overlayInfo };
  }
  return {
    ...overlayInfo,
    ...baseInfo,
    current_position_average_price:
      baseInfo.current_position_average_price ?? overlayInfo.current_position_average_price,
    close_price: baseInfo.close_price ?? overlayInfo.close_price,
    order_ids:
      executorOrderIdCount(baseInfo) >= executorOrderIdCount(overlayInfo)
        ? (baseInfo.order_ids ?? overlayInfo.order_ids)
        : (overlayInfo.order_ids ?? baseInfo.order_ids),
    open_order_last_update:
      baseInfo.open_order_last_update ?? overlayInfo.open_order_last_update,
  };
}

function pricesFromCustomInfo(customInfo: Record<string, unknown>): {
  entry: number;
  close: number;
} {
  return {
    entry: Number(customInfo.current_position_average_price) || 0,
    close: Number(customInfo.close_price) || 0,
  };
}

/**
 * Merge live WS executor fields onto a REST session row without dropping
 * chart-critical data (timestamps, config, custom_info) when WS is sparse.
 * Prefers richer fill custom_info so mid-flight WS cache cannot flatten
 * terminated entry/close back to the live mid.
 */
export function mergeExecutorOverlay(restEx: ExecutorInfo, wsEx: ExecutorInfo): ExecutorInfo {
  const mergedCustom = mergeExecutorCustomInfo(restEx.custom_info, wsEx.custom_info);
  const preferBaseFills =
    executorFillInfoQuality(restEx.custom_info) > executorFillInfoQuality(wsEx.custom_info);
  const fillPrices = pricesFromCustomInfo(mergedCustom);

  const merged: ExecutorInfo = {
    ...restEx,
    ...wsEx,
    timestamp: wsEx.timestamp || restEx.timestamp,
    entry_price: preferBaseFills
      ? restEx.entry_price || wsEx.entry_price || fillPrices.entry
      : wsEx.entry_price || restEx.entry_price || fillPrices.entry,
    current_price: preferBaseFills
      ? restEx.current_price || wsEx.current_price || fillPrices.close
      : wsEx.current_price || restEx.current_price || fillPrices.close,
    close_timestamp: wsEx.close_timestamp || restEx.close_timestamp,
    connector: wsEx.connector || restEx.connector,
    trading_pair: wsEx.trading_pair || restEx.trading_pair,
    type: wsEx.type || restEx.type,
    controller_id: wsEx.controller_id || restEx.controller_id,
    custom_info: mergedCustom,
    config: mergeExecutorConfigs(restEx.config || {}, wsEx.config || {}),
    cum_fees_quote: wsEx.cum_fees_quote ?? restEx.cum_fees_quote,
  };
  if (isExecutorActive(restEx.status) && !isExecutorActive(wsEx.status)) {
    merged.status = restEx.status;
    merged.close_type = restEx.close_type;
  }
  const side = resolveExecutorSide(merged);
  if (side) merged.side = side;
  return merged;
}

/** Fill missing entry/current/config from positions-held summary for chart overlays. */
export function normalizePositionHeld(raw: Record<string, unknown>): PositionHeld {
  const entry = Number(raw.entry_price || raw.buy_breakeven_price || 0);
  const breakeven = Number(raw.buy_breakeven_price || raw.entry_price || 0);
  return {
    connector_name: String(raw.connector_name || raw.connector || ""),
    trading_pair: String(raw.trading_pair || raw.pair || ""),
    position_side: String(raw.position_side || raw.side || ""),
    side: String(raw.side || raw.position_side || ""),
    net_amount_base: Number(raw.net_amount_base || raw.amount || 0),
    amount: Number(raw.amount || raw.net_amount_base || 0),
    buy_breakeven_price: breakeven,
    entry_price: entry,
    current_price: Number(raw.current_price || 0),
    unrealized_pnl_quote: Number(raw.unrealized_pnl_quote || raw.unrealized_pnl || 0),
    unrealized_pnl: Number(raw.unrealized_pnl || raw.unrealized_pnl_quote || 0),
    leverage: Number(raw.leverage || 0),
    controller_id: String(raw.controller_id || ""),
  };
}

function pairMatchKey(pair: string): string {
  return pair.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export function enrichExecutorFromPositions(
  executor: ExecutorInfo,
  positions: PositionHeld[],
): ExecutorInfo {
  const targetKey = pairMatchKey(executor.trading_pair || "");
  const match = positions.find((p) => pairMatchKey(p.trading_pair || "") === targetKey);
  if (!match) return executor;

  const entry = match.buy_breakeven_price ?? match.entry_price ?? 0;
  const current = match.current_price ?? 0;
  const customInfo = { ...executor.custom_info };
  if (entry > 0 && !Number(customInfo.current_position_average_price)) {
    customInfo.current_position_average_price = entry;
  }
  const config = { ...executor.config };
  if (match.leverage && !config.leverage) {
    config.leverage = match.leverage;
  }

  return {
    ...executor,
    entry_price: executor.entry_price || entry,
    current_price: executor.current_price || current,
    custom_info: customInfo,
    config,
  };
}

export type JournalExecutorHint = {
  id: string;
  side?: string;
  amount?: number;
};

/** Fill side/amount on sparse HB rows from journal executor lines (recorded at creation). */
export function enrichExecutorFromJournal(
  executor: ExecutorInfo,
  journalById: Map<string, JournalExecutorHint>,
): ExecutorInfo {
  const row = journalById.get(executor.id);
  if (!row) return executor;

  const config = { ...(executor.config || {}) };
  let configChanged = false;
  // Journal `amount` is often base size; only fill quote when nothing else exists and
  // the value is already in quote range (handled by session row notional first).
  if (
    row.amount &&
    row.amount > 0 &&
    !config.total_amount_quote &&
    config.amount == null
  ) {
    config.total_amount_quote = row.amount;
    configChanged = true;
  }

  const side =
    resolveExecutorSide({ side: row.side, config, custom_info: executor.custom_info }) ||
    executor.side;

  if (!configChanged && (!side || side === executor.side)) return executor;
  return {
    ...executor,
    side: side || executor.side,
    config: configChanged ? config : executor.config,
  };
}

function pctToDecimal(raw: unknown): number | null {
  const n = Number(raw);
  if (!n || n <= 0 || n === -1) return null;
  return n > 1 ? n / 100 : n;
}

export function sessionConfigHasDefaults(
  sessionConfig: Record<string, unknown> | undefined,
): boolean {
  if (!sessionConfig || Object.keys(sessionConfig).length === 0) return false;
  const sp = (sessionConfig.strategy_params ?? {}) as Record<string, unknown>;
  return !!(
    sessionConfig.total_amount_quote ??
    sessionConfig.leverage ??
    sessionConfig.stop_loss ??
    sessionConfig.take_profit ??
    sp.sl_pct ??
    sp.tp_pct ??
    sp.leverage ??
    sp.formal_notional ??
    sp.min_notional_quote
  );
}

/** Merge strategy defaults with session-frozen config (session wins on overlap). */
export function mergeSessionConfigSources(
  sessionConfig: Record<string, unknown> | undefined,
  strategyDefaults: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!sessionConfig && !strategyDefaults) return undefined;
  const merged = { ...(strategyDefaults ?? {}), ...(sessionConfig ?? {}) };
  const dSp = (strategyDefaults?.strategy_params ?? {}) as Record<string, unknown>;
  const sSp = (sessionConfig?.strategy_params ?? {}) as Record<string, unknown>;
  if (Object.keys(dSp).length || Object.keys(sSp).length) {
    merged.strategy_params = { ...dSp, ...sSp };
  }
  return merged;
}

export function executorInfoFromAgentRow(row: AgentExecutorRow): ExecutorInfo {
  const config = { ...(row.config ?? {}) };
  // Prefer quote notional. Never promote raw base `config.amount` (e.g. 110152 kBONK)
  // into total_amount_quote — that made the UI show Amount $110.2K for a ~$376 position.
  const quoteNotional =
    Number(config.total_amount_quote) ||
    Number(row.notional_quote) ||
    Number(row.amount) ||
    0;
  if (quoteNotional > 0) {
    config.total_amount_quote = quoteNotional;
  }
  const info: ExecutorInfo = {
    id: row.id,
    type: normalizeExecutorType(row.type),
    connector: row.connector || "",
    trading_pair: row.pair,
    side: row.side,
    status: (row.status || "").toLowerCase(),
    close_type: (row.close_type || "").toLowerCase(),
    pnl: row.pnl,
    volume: row.volume,
    timestamp: row.timestamp,
    controller_id: row.controller_id,
    cum_fees_quote: row.fees,
    net_pnl_pct: row.net_pnl_pct ?? 0,
    entry_price: row.entry_price,
    current_price: row.current_price,
    close_timestamp: row.close_timestamp,
    custom_info: row.custom_info ?? {},
    config,
  };
  return {
    ...info,
    timestamp: resolveExecutorTimestamp({ ...info, created_at: row.created_at }),
    close_timestamp: resolveExecutorCloseTimestamp({ ...info, closed_at: row.closed_at }),
    side: resolveExecutorSide({ ...info, config }) || info.side,
  };
}

/** Backfill SL/TP/leverage/amount on terminated rows from session config.yml (local, no HB). */
export function enrichExecutorWithSessionDefaults(
  executor: ExecutorInfo,
  sessionConfig: Record<string, unknown> | undefined,
): ExecutorInfo {
  if (!sessionConfig) return executor;

  const config = { ...(executor.config || {}) };
  const sp = (sessionConfig.strategy_params ?? {}) as Record<string, unknown>;
  let changed = false;

  const fill = (key: string, val: unknown) => {
    if (val == null || val === "" || config[key] != null) return;
    config[key] = val;
    changed = true;
  };

  fill(
    "total_amount_quote",
    sessionConfig.total_amount_quote ?? sp.formal_notional ?? sp.min_notional_quote,
  );
  fill("leverage", sessionConfig.leverage ?? sp.leverage);

  const sl = pctToDecimal(sp.sl_pct ?? sp.stop_loss ?? sessionConfig.stop_loss);
  const tp = pctToDecimal(sp.tp_pct ?? sp.take_profit ?? sessionConfig.take_profit);
  if (sl != null) fill("stop_loss", sl);
  if (tp != null) fill("take_profit", tp);

  if (!changed) return executor;
  return { ...executor, config };
}

/** Infer side from held_position_orders when HB omits top-level side. */
export function enrichExecutorSideFromHeldOrders(executor: ExecutorInfo): ExecutorInfo {
  if (resolveExecutorSide(executor)) return executor;
  const orders = executor.custom_info?.held_position_orders;
  if (!Array.isArray(orders)) return executor;
  for (const order of orders) {
    if (!order || typeof order !== "object") continue;
    const raw = order as Record<string, unknown>;
    const side = resolveExecutorSide({
      side: raw.side ?? raw.trade_type ?? raw.position_side,
      config: {},
      custom_info: {},
    });
    if (side) return { ...executor, side };
  }
  return executor;
}

/** Infer BUY/SELL from entry/exit and realized PnL when HB omits side. */
export function enrichExecutorSideFromPrices(executor: ExecutorInfo): ExecutorInfo {
  if (resolveExecutorSide(executor)) return executor;
  const entry =
    executor.entry_price ||
    Number(executor.custom_info?.current_position_average_price) ||
    0;
  const exit =
    executor.current_price ||
    Number(executor.custom_info?.close_price) ||
    0;
  if (!entry || !exit || executor.pnl === 0) return executor;
  const side = (exit > entry) === (executor.pnl > 0) ? "BUY" : "SELL";
  return { ...executor, side };
}

export type ExecutorEnrichmentContext = {
  sessionConfig?: Record<string, unknown>;
  journalById?: Map<string, JournalExecutorHint>;
  sessionExecutorRow?: AgentExecutorRow;
};

/** Journal + session row + defaults + price-inferred side for sparse terminated rows. */
export function enrichExecutorForTerminatedDisplay(
  executor: ExecutorInfo,
  opts: ExecutorEnrichmentContext,
): ExecutorInfo {
  let row = executor;
  if (opts.sessionExecutorRow) {
    row = mergeExecutorOverlay(row, executorInfoFromAgentRow(opts.sessionExecutorRow));
  }
  if (opts.journalById?.size) row = enrichExecutorFromJournal(row, opts.journalById);
  if (opts.sessionConfig) row = enrichExecutorWithSessionDefaults(row, opts.sessionConfig);
  row = enrichExecutorSideFromHeldOrders(row);
  return enrichExecutorSideFromPrices(row);
}

export function sessionRefKey(ref: ControllerSessionRef): string {
  return `${ref.slug}:${ref.sslug}:${ref.sessionNum}`;
}

/** Apply live mid price when executor current_price is missing (active positions). */
export function enrichExecutorWithLivePrice(
  executor: ExecutorInfo,
  midPrice: number | undefined,
): ExecutorInfo {
  if (!midPrice || midPrice <= 0 || executor.current_price > 0) return executor;
  return { ...executor, current_price: midPrice };
}

/** Derive average entry from unrealized PnL % and current mark price. */
export function deriveEntryFromPnl(executor: ExecutorInfo, currentPrice: number): number {
  const pnl = executor.pnl;
  const pct = executor.net_pnl_pct;
  if (!currentPrice || !pnl || !pct) return 0;
  const notional = Math.abs(pnl / (pct / 100));
  if (notional <= 0) return 0;
  const base = notional / currentPrice;
  if (base <= 0) return 0;
  const side = String(executor.side || "").toLowerCase();
  const isBuy = side === "buy" || side === "1" || side === "long";
  const entry = isBuy ? currentPrice - pnl / base : currentPrice + pnl / base;
  return entry > 0 ? entry : 0;
}

/**
 * Fill missing entry/close for chart overlays: candle at open/close time, then PnL fallback.
 */
export function enrichExecutorForChart(
  executor: ExecutorInfo,
  candles: Array<{ timestamp?: number; time?: number; close: number }> | undefined,
  interval?: string,
): ExecutorInfo {
  let result = executor;
  const config = resolveExecutorConfig(result);
  const maxDistSec = interval ? candleMaxDistForInterval(interval) : 600;

  const existingEntry =
    result.entry_price ||
    Number(result.custom_info?.current_position_average_price) ||
    0;

  let entry = existingEntry;
  const openTs = result.timestamp;
  if (entry <= 0 && openTs > 0 && candles?.length) {
    entry = candlePriceAt(openTs, candles, maxDistSec);
  }
  const current = result.current_price;
  if (entry <= 0 && current > 0) {
    entry = deriveEntryFromPnl(result, current);
  }

  const existingClose =
    result.current_price ||
    Number(result.custom_info?.close_price) ||
    0;
  let closePrice = existingClose;
  const closeTs = resolveExecutorCloseTimestamp(result);
  if (closePrice <= 0 && closeTs > 0 && candles?.length) {
    closePrice = candlePriceAt(closeTs, candles, maxDistSec);
  }
  if (closePrice <= 0 && !isExecutorActive(result.status) && entry > 0 && result.pnl) {
    closePrice = deriveCloseFromPnl(result, entry);
  }

  const customInfo = { ...result.custom_info };
  if (entry > 0 && !Number(customInfo.current_position_average_price)) {
    customInfo.current_position_average_price = entry;
  }
  if (closePrice > 0 && !Number(customInfo.close_price)) {
    customInfo.close_price = closePrice;
  }

  const patch: Partial<ExecutorInfo> = {};
  if (Object.keys(config).length > Object.keys(result.config || {}).length) {
    patch.config = { ...config, ...(result.config || {}) };
  }
  if (entry > 0 && result.entry_price <= 0) patch.entry_price = entry;
  if (closePrice > 0 && result.current_price <= 0) patch.current_price = closePrice;

  if (
    !patch.entry_price &&
    !patch.current_price &&
    !patch.config &&
    customInfo.current_position_average_price === result.custom_info?.current_position_average_price &&
    customInfo.close_price === result.custom_info?.close_price
  ) {
    return enrichExecutorSideFromPrices(result);
  }
  return enrichExecutorSideFromPrices({ ...result, ...patch, custom_info: customInfo });
}

/** Derive exit/close price from realized PnL and entry (terminated positions). */
export function deriveCloseFromPnl(executor: ExecutorInfo, entryPrice: number): number {
  const pnl = executor.pnl;
  if (!entryPrice || !pnl) return 0;
  const cfg = resolveExecutorConfig(executor);
  const quote = Number(cfg.total_amount_quote || cfg.amount || 0);
  if (quote <= 0) return 0;
  const base = quote / entryPrice;
  if (base <= 0) return 0;
  const side = String(executor.side || "").toLowerCase();
  const isBuy = side === "buy" || side === "1" || side === "long";
  const close = isBuy ? entryPrice + pnl / base : entryPrice - pnl / base;
  return close > 0 ? close : 0;
}

type ExecutorInfiniteCache = {
  pages?: ExecutorPage[];
};

/**
 * Build a lookup of executor rows already in the React Query cache (no network).
 * Sources: live SDS snapshot + paginated REST pages (often WS-enriched while active).
 */
export function getCachedExecutorsById(
  getQueryData: (queryKey: readonly unknown[]) => unknown,
  server: string,
): Map<string, ExecutorInfo> {
  const byId = new Map<string, ExecutorInfo>();

  const live = getQueryData(["executors", server, ""]) as ExecutorInfo[] | undefined;
  if (Array.isArray(live)) {
    for (const ex of live) {
      if (ex?.id) byId.set(ex.id, ex);
    }
  }

  const infinite = getQueryData(["executors-infinite", server]) as ExecutorInfiniteCache | undefined;
  for (const page of infinite?.pages ?? []) {
    for (const raw of page.executors) {
      const ex = raw as ExecutorInfo;
      if (!ex?.id) continue;
      const prev = byId.get(ex.id);
      byId.set(ex.id, prev ? mergeExecutorOverlay(prev, ex) : ex);
    }
  }

  return byId;
}

/** First occurrence wins — earlier pages (WS-patched page 0) take precedence. */
export function dedupeExecutorsById(executors: ExecutorInfo[]): ExecutorInfo[] {
  const seen = new Set<string>();
  const result: ExecutorInfo[] = [];
  for (const ex of executors) {
    if (!ex.id || seen.has(ex.id)) continue;
    seen.add(ex.id);
    result.push(ex);
  }
  return result;
}

export function executorId(ex: unknown): string {
  if (ex && typeof ex === "object" && "id" in ex) {
    const id = (ex as { id?: unknown }).id;
    return typeof id === "string" ? id : "";
  }
  return "";
}

/** Shallow-clone list items so React Query always sees a new reference on WS ticks. */
export function cloneExecutorList<T>(executors: T[]): T[] {
  return executors.map((ex) =>
    ex && typeof ex === "object" ? ({ ...ex } as T) : ex,
  );
}

/**
 * Patch every cached page with live WS fields by executor ID.
 * Replacing only page 0 misses executors that live on later REST pages.
 * Uses mergeExecutorOverlay (not full replace) so a mid-flight WS snapshot
 * cannot wipe persisted open+close fill prices after termination.
 */
export function mergeExecutorPagesWithWs(
  pages: ExecutorPage[],
  wsExecs: unknown[],
): ExecutorPage[] {
  const wsById = new Map<string, ExecutorInfo>();
  for (const ex of wsExecs) {
    const id = executorId(ex);
    if (id && ex && typeof ex === "object") wsById.set(id, ex as ExecutorInfo);
  }

  const seenInPages = new Set<string>();
  const mergedPages = pages.map((page) => ({
    ...page,
    executors: page.executors.map((ex) => {
      const id = executorId(ex);
      if (id) seenInPages.add(id);
      const live = id ? wsById.get(id) : undefined;
      if (!live) return ex;
      if (ex && typeof ex === "object") {
        return mergeExecutorOverlay(ex as ExecutorInfo, live);
      }
      return live;
    }),
  }));

  const newOnes = wsExecs.filter((ex) => {
    const id = executorId(ex);
    return id && !seenInPages.has(id);
  });
  if (newOnes.length > 0 && mergedPages.length > 0) {
    mergedPages[0] = {
      ...mergedPages[0],
      executors: [...newOnes, ...mergedPages[0].executors],
    };
  }

  return mergedPages;
}
