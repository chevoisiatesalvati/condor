export type DeterministicTickSummary = {
  id: string;
  ts?: string;
  session?: number;
  tick?: number;
  tradeable_count?: number;
  signal_count?: number;
  scanner_regime?: string;
  hold_reason?: string;
  creates?: number;
  stops?: number;
  apply_ok?: boolean;
  summary?: string;
  raw?: Record<string, unknown>;
};

export type TickCloseEvent = {
  pair: string;
  closeType: string;
  pnl?: number;
};

export type FormattedDeterministicTick = {
  title: string;
  pairs: string[];
  closes: TickCloseEvent[];
  isError: boolean;
  errorText?: string;
  meta: string[];
};

const HOLD_REASON_LABELS: Record<string, string> = {
  inventory_unavailable: "inventory unavailable",
  at_max_open_executors: "at max open positions",
  no_formal_or_adaptive_trigger: "no entry trigger",
  no_entry_candidate: "no entry candidate",
  sizing_rejected: "sizing rejected",
  hold: "hold",
};

export function humanizeHoldReason(reason: string): string {
  const trimmed = reason.trim();
  if (!trimmed) return "";
  return HOLD_REASON_LABELS[trimmed] || trimmed.replace(/_/g, " ");
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function pairFromUnknown(value: unknown): string {
  if (typeof value === "string") return value;
  const row = asRecord(value);
  return String(row.pair || row.trading_pair || "");
}

export function formatDeterministicTick(
  tick: DeterministicTickSummary,
): FormattedDeterministicTick {
  const raw = asRecord(tick.raw);
  const apply = asRecord(raw.apply);
  const decide = asRecord(raw.decide);
  const creates = Number(tick.creates ?? decide.creates ?? 0);
  const stops = Number(tick.stops ?? decide.stops ?? 0);
  const holdReason = String(tick.hold_reason || decide.hold_reason || "");
  const applyOk = tick.apply_ok ?? (typeof apply.ok === "boolean" ? apply.ok : undefined);
  const applyError = String(apply.error || "");

  const scorePairs = Object.keys(asRecord(decide.scores)).filter(Boolean);
  const signalPairs = (Array.isArray(raw.signals) ? raw.signals : [])
    .map(pairFromUnknown)
    .filter(Boolean);
  const createdIds = Array.isArray(apply.created_ids)
    ? apply.created_ids.map(String)
    : [];
  const pairs = Array.from(new Set([...scorePairs, ...signalPairs.slice(0, creates || 4)]));

  const closes: TickCloseEvent[] = [];
  for (const row of Array.isArray(raw.barrier_closes) ? raw.barrier_closes : []) {
    const rec = asRecord(row);
    const pair = String(rec.pair || "");
    if (!pair) continue;
    closes.push({
      pair,
      closeType: String(rec.close_type || "close").replace(/_/g, " ").toLowerCase(),
      pnl: rec.pnl != null ? Number(rec.pnl) : undefined,
    });
  }

  const isError = applyOk === false;
  let title = "Held";
  if (isError) {
    title = applyError ? `Apply failed — ${applyError}` : "Apply failed";
  } else if (creates > 0 && (stops > 0 || closes.length > 0)) {
    title = `Opened ${creates}, closed ${stops || closes.length}`;
  } else if (creates > 0) {
    title = `Opened ${creates} position${creates === 1 ? "" : "s"}`;
  } else if (stops > 0 || closes.length > 0) {
    const closedCount = stops || closes.length;
    title = `Closed ${closedCount} position${closedCount === 1 ? "" : "s"}`;
  } else if (holdReason) {
    title = `Held — ${humanizeHoldReason(holdReason)}`;
  } else if (tick.summary && tick.summary !== "hold") {
    title = humanizeHoldReason(tick.summary);
  }

  const meta: string[] = [];
  if (tick.scanner_regime) meta.push(`regime ${tick.scanner_regime}`);
  if (tick.tradeable_count != null) meta.push(`${tick.tradeable_count} tradeable`);
  if (tick.signal_count != null) meta.push(`${tick.signal_count} signals`);
  if (createdIds.length > 0) meta.push(`${createdIds.length} created`);

  return {
    title,
    pairs: pairs.slice(0, 8),
    closes,
    isError,
    errorText: isError ? applyError || undefined : undefined,
    meta,
  };
}

export function parseJournalKvChips(
  reasoning: string,
): Array<{ key: string; value: string }> {
  if (!reasoning.trim()) return [];
  const parts = reasoning.trim().split(/\s+(?=[A-Za-z_][A-Za-z0-9_]*=)/);
  const chips: Array<{ key: string; value: string }> = [];
  for (const part of parts) {
    const eq = part.indexOf("=");
    if (eq <= 0) continue;
    const value = part.slice(eq + 1).trim();
    if (!value) continue;
    chips.push({
      key: part.slice(0, eq).replace(/_/g, " "),
      value,
    });
  }
  return chips;
}
