import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Wrench,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AgentPnlChart, metricsToDataPoints } from "@/components/agent/AgentPnlChart";
import { DetailPanel, ExecutorTable, type SortDir, type SortKey } from "@/components/executor/ExecutorTable";
import { StopConfirmDialog } from "@/components/executor/StopConfirmDialog";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { type AgentPerformance, type ExecutorInfo, api } from "@/lib/api";
import {
  controllerIdsForLookup,
  enrichExecutorFromJournal,
  enrichExecutorFromPositions,
  enrichExecutorWithSessionDefaults,
  executorInfoFromAgentRow,
  getCachedExecutorsById,
  mergeExecutorOverlay,
  normalizePositionHeld,
  resolveExecutorConfig,
  type ExecutorEnrichmentContext,
} from "@/lib/executors";
import { type ExecutorEntry, type ParsedJournal, type ParsedSnapshot, parseSnapshot } from "@/lib/parse-agent";
import {
  resolveExecutorSide,
  formatExecutorSide,
  formatCurrencyPnl,
  formatCompactUsd,
} from "@/lib/formatters";
import { useRates } from "@/hooks/useRates";

// ── Helper ──

function detailPanelGridClass(count: number): string {
  const base = "grid gap-3";
  if (count <= 1) return `${base} grid-cols-1`;
  if (count === 2) return `${base} grid-cols-1 sm:grid-cols-2`;
  return `${base} grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`;
}

// ── Session Overview ──

export function SessionOverview(props: {
  journal: ParsedJournal;
  perf?: AgentPerformance | null;
}) {
  const { metrics } = props.journal;

  // PnL chart data from metrics timeline
  const pnlData = useMemo(() => metricsToDataPoints(metrics), [metrics]);

  if (pnlData.length <= 1) {
    return null;
  }

  return (
    <div className="space-y-4">
      <AgentPnlChart data={pnlData} height={400} title="Metrics Timeline" />
    </div>
  );
}

// ── Session Activity ──

export function SessionActivity({ journal }: { journal: ParsedJournal }) {
  const { decisions } = journal;

  if (decisions.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No decisions yet.</p>;
  }

  return (
    <div className="space-y-2">
      {decisions.map((d, i) => (
        <div key={i} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <div className="flex items-start gap-3">
            {d.tick > 0 ? (
              <span className="mt-0.5 shrink-0 rounded-md bg-[var(--color-surface-hover)] px-2 py-0.5 font-mono text-xs font-bold text-[var(--color-text-muted)]">
                #{d.tick}
              </span>
            ) : (
              <span className="mt-0.5 shrink-0 rounded-md bg-red-500/10 px-2 py-0.5 font-mono text-xs font-bold text-red-400">
                ERR
              </span>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-[var(--color-text-muted)]">{d.time}</span>
                <span className="text-sm font-medium text-[var(--color-text)]">{d.action}</span>
                {d.riskNote && (
                  <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-400">
                    {d.riskNote}
                  </span>
                )}
              </div>
              {d.reasoning && (
                <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-muted)]">{d.reasoning}</p>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Session Executors ──

export function SessionExecutors({
  slug,
  sslug,
  sessionNum,
  serverName,
  controllerIds,
  sessionSummary,
  liveSessionStatus,
  journalExecutors,
}: {
  slug: string;
  sslug: string;
  sessionNum: number;
  serverName: string;
  controllerIds?: string[];
  sessionSummary?: { status: string; lastTick: number; lastAction: string };
  liveSessionStatus?: string;
  journalExecutors?: ExecutorEntry[];
}) {
  const queryClient = useQueryClient();
  const [executorCacheTick, setExecutorCacheTick] = useState(0);

  useEffect(() => {
    const unsub = queryClient.getQueryCache().subscribe((event) => {
      const key = event.query?.queryKey;
      if (!key || key[1] !== serverName) return;
      if (key[0] === "executors-infinite" || (key[0] === "executors" && key[2] === "")) {
        setExecutorCacheTick((n) => n + 1);
      }
    });
    return unsub;
  }, [queryClient, serverName]);

  const cachedExecutorsById = useMemo(
    () => getCachedExecutorsById((key) => queryClient.getQueryData(key), serverName),
    [queryClient, serverName, executorCacheTick],
  );

  const { data: sessionDetail } = useQuery({
    queryKey: ["strategy-session-executors", slug, sslug, sessionNum],
    queryFn: () => api.getStrategySessionExecutors(slug, sslug, sessionNum),
    refetchInterval: 10000,
    placeholderData: keepPreviousData,
  });

  const restExecutors = sessionDetail?.executors ?? [];
  const sessionConfig = sessionDetail?.session_config;

  const journalById = useMemo(() => {
    const map = new Map<string, { id: string; side?: string; amount?: number }>();
    for (const row of journalExecutors ?? []) {
      if (row.id) map.set(row.id, { id: row.id, side: row.side, amount: row.amount });
    }
    return map;
  }, [journalExecutors]);

  const sessionControllerIds = useMemo(() => {
    const ids = new Set<string>();
    for (const id of controllerIdsForLookup(`${slug}.${sslug}_${sessionNum}`)) {
      ids.add(id);
    }
    for (const id of controllerIds ?? []) {
      for (const lookupId of controllerIdsForLookup(id)) ids.add(lookupId);
    }
    return Array.from(ids);
  }, [slug, sslug, sessionNum, controllerIds]);

  const { executors: wsExecutors } = useAgentExecutors(
    serverName || null,
    sessionControllerIds,
  );

  const { data: liveExecutorsCache } = useQuery({
    queryKey: ["executors", serverName, ""],
    queryFn: () => api.getExecutors(serverName),
    enabled: !!serverName,
    refetchInterval: 60000,
  });

  const liveById = useMemo(
    () => new Map((liveExecutorsCache ?? []).map((ex) => [ex.id, ex])),
    [liveExecutorsCache],
  );

  const { data: positionsData } = useQuery({
    queryKey: ["positions-held", serverName],
    queryFn: () => api.getPositionsHeld(serverName),
    enabled: !!serverName && sessionControllerIds.length > 0,
    refetchInterval: 10000,
  });

  const { data: consolidatedPositions } = useQuery({
    queryKey: ["consolidated-positions", serverName],
    queryFn: () => api.getConsolidatedPositions(serverName),
    enabled: !!serverName,
    refetchInterval: 10000,
  });

  const normalizedPositions = useMemo(() => {
    const raws = [
      ...(positionsData?.positions ?? []),
      ...(consolidatedPositions?.executor_positions ?? []),
      ...(consolidatedPositions?.bot_positions ?? []),
    ];
    return raws.map((p) => normalizePositionHeld(p as unknown as Record<string, unknown>));
  }, [positionsData, consolidatedPositions]);

  const positions = useMemo(() => {
    if (normalizedPositions.length === 0 || sessionControllerIds.length === 0) return [];
    const cidSet = new Set(sessionControllerIds);
    return normalizedPositions.filter((p) => p.controller_id && cidSet.has(p.controller_id));
  }, [normalizedPositions, sessionControllerIds]);

  const executorInfos = useMemo(() => {
    const restInfos = restExecutors.map(executorInfoFromAgentRow);
    let merged: ExecutorInfo[];
    if (wsExecutors.length === 0) {
      merged = restInfos;
    } else {
      const wsMap = new Map(wsExecutors.map((ex) => [ex.id, ex]));
      merged = restInfos.map((ex) => {
        const wsEx = wsMap.get(ex.id);
        return wsEx ? mergeExecutorOverlay(ex, wsEx) : ex;
      });
      const restIds = new Set(restInfos.map((ex) => ex.id));
      for (const ex of wsExecutors) {
        if (!restIds.has(ex.id)) merged.push(ex);
      }
    }
    const enrich = (ex: ExecutorInfo): ExecutorInfo => {
      let row = ex;
      const cached = cachedExecutorsById.get(ex.id);
      if (cached) row = mergeExecutorOverlay(row, cached);
      const live = liveById.get(ex.id);
      if (live) row = mergeExecutorOverlay(row, live);
      row = enrichExecutorFromJournal(row, journalById);
      row = enrichExecutorWithSessionDefaults(row, sessionConfig);
      if (normalizedPositions.length > 0) {
        row = enrichExecutorFromPositions(row, normalizedPositions);
      }
      const side = resolveExecutorSide(row);
      if (side) row = { ...row, side };
      return row;
    };
    return merged.map(enrich);
  }, [restExecutors, wsExecutors, normalizedPositions, liveById, cachedExecutorsById, journalById, sessionConfig]);

  // #region agent log
  useEffect(() => {
    const sample = executorInfos.filter((ex) => {
      const s = ex.status?.toLowerCase() ?? "";
      const active = s === "running" || s === "active" || s === "active_position";
      const terminated = s === "terminated" || s === "closed" || s === "completed";
      return active || terminated;
    }).slice(0, 4);
    if (sample.length === 0) return;
    fetch("http://127.0.0.1:7313/ingest/66e6cf39-e791-4256-8122-105d89ec429b", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "644d7b" },
      body: JSON.stringify({
        sessionId: "644d7b",
        runId: "post-fix-v6",
        hypothesisId: "H11",
        location: "AgentSessionContent.tsx:executorInfos",
        message: "Session executor merge result",
        data: {
          sessionControllerIds,
          cachedCount: cachedExecutorsById.size,
          wsCount: wsExecutors.length,
          restCount: restExecutors.length,
          sample: sample.map((ex) => ({
            id: ex.id,
            status: ex.status,
            entry_price: ex.entry_price,
            current_price: ex.current_price,
            close_timestamp: ex.close_timestamp,
            configKeys: Object.keys(resolveExecutorConfig(ex)),
            customInfoKeys: Object.keys(ex.custom_info || {}),
            cachedHit: cachedExecutorsById.has(ex.id),
          })),
        },
        timestamp: Date.now(),
      }),
    }).catch(() => {});
  }, [executorInfos, wsExecutors, restExecutors, sessionControllerIds, cachedExecutorsById]);
  // #endregion

  const sessionEnrichment = useMemo<ExecutorEnrichmentContext>(
    () => ({
      sessionConfig,
      journalById,
    }),
    [sessionConfig, journalById],
  );

  const displaySessionStatus = liveSessionStatus ?? "closed";

  const quoteCurrencies = useMemo(
    () => executorInfos.map((ex) => ex.trading_pair?.split("-")[1] || "USDT"),
    [executorInfos],
  );
  const { formatPnlValue, formatValue, formatValueDetailed } = useRates(quoteCurrencies);

  const stats = useMemo(() => {
    let totalPnl = 0;
    let totalVolume = 0;
    let totalFees = 0;
    let activeCount = 0;
    for (const ex of executorInfos) {
      totalPnl += ex.pnl ?? 0;
      totalVolume += ex.volume ?? 0;
      totalFees += ex.cum_fees_quote ?? 0;
      const s = ex.status?.toLowerCase() ?? "";
      if (s === "running" || s === "active_position" || s === "active") activeCount++;
    }
    return { totalPnl, totalVolume, totalFees, activeCount, total: executorInfos.length };
  }, [executorInfos]);

  const [sortKey, setSortKey] = useState<SortKey>("status");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [pendingStopIds, setPendingStopIds] = useState<string[] | null>(null);

  const stopMutation = useMutation({
    mutationFn: async ({ ids, keepPosition }: { ids: string[]; keepPosition: boolean }) => {
      setStoppingIds((prev) => new Set([...prev, ...ids]));
      return Promise.allSettled(
        ids.map((id) => api.stopExecutor(serverName, id, keepPosition)),
      );
    },
    onSettled: (_data, _error, vars) => {
      setStoppingIds((prev) => {
        const next = new Set(prev);
        vars?.ids.forEach((id) => next.delete(id));
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ["strategy-session-executors", slug, sslug, sessionNum] });
      queryClient.invalidateQueries({ queryKey: ["executors", serverName] });
    },
  });

  const handleStopOne = useCallback((id: string) => {
    setPendingStopIds([id]);
  }, []);

  const handleConfirmStop = useCallback(
    (ids: string[], keepPosition: boolean) => {
      setPendingStopIds(null);
      stopMutation.mutate({ ids, keepPosition });
    },
    [stopMutation],
  );

  const selectedExecutors = useMemo(
    () => executorInfos.filter((ex) => selectedIds.has(ex.id)),
    [executorInfos, selectedIds],
  );

  const handleSort = useCallback((key: SortKey) => {
    setSortDir((prev) => (sortKey === key ? (prev === "asc" ? "desc" : "asc") : "desc"));
    setSortKey(key);
  }, [sortKey]);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  if (!sessionDetail) {
    return (
      <div className="flex h-32 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (executorInfos.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No executors for this session.</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
        {sessionSummary && (
          <div>
            <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Status</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${
              displaySessionStatus === "ACTIVE" || displaySessionStatus === "running" || displaySessionStatus === "Running"
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                : displaySessionStatus === "paused"
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-400"
                  : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
            }`}>
              {displaySessionStatus}
            </span>
          </div>
        )}
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Net PnL</span>
          <span className={`font-mono text-sm font-semibold ${stats.totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
            {formatCurrencyPnl(stats.totalPnl)}
          </span>
        </div>
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Volume</span>
          <span className="font-mono text-sm text-[var(--color-text)]">{formatCompactUsd(stats.totalVolume)}</span>
        </div>
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Fees</span>
          <span className="font-mono text-sm text-[var(--color-text-muted)]">{formatCompactUsd(stats.totalFees)}</span>
        </div>
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Executors</span>
          <span className="text-sm text-[var(--color-text)]">
            {stats.total}
            {stats.activeCount > 0 && (
              <span className="ml-1 text-emerald-400">({stats.activeCount} active)</span>
            )}
          </span>
        </div>
        {sessionSummary && sessionSummary.lastTick > 0 && (
          <div>
            <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Tick</span>
            <span className="font-mono text-sm text-[var(--color-text)]">#{sessionSummary.lastTick}</span>
          </div>
        )}
        {sessionSummary?.lastAction && (
          <div>
            <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Action</span>
            <span className="text-sm text-[var(--color-text)]">{sessionSummary.lastAction}</span>
          </div>
        )}
      </div>

      {positions.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Positions Held ({positions.length})
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                  <th className="pb-2 pr-3">Pair</th>
                  <th className="pb-2 pr-3">Side</th>
                  <th className="pb-2 pr-3 text-right">Amount</th>
                  <th className="pb-2 pr-3 text-right">Entry</th>
                  <th className="pb-2 pr-3 text-right">Current</th>
                  <th className="pb-2 pr-3 text-right">Unreal. PnL</th>
                  <th className="pb-2 text-right">Leverage</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const upnl = p.unrealized_pnl_quote ?? p.unrealized_pnl ?? 0;
                  const sideLabel = formatExecutorSide(p.position_side || p.side || "");
                  const amount = p.net_amount_base ?? p.amount ?? 0;
                  const entry = p.buy_breakeven_price ?? p.entry_price ?? 0;
                  const current = p.current_price ?? 0;
                  return (
                    <tr key={`${p.trading_pair}-${i}`} className="border-b border-[var(--color-border)]/30">
                      <td className="py-2 pr-3 font-mono text-[var(--color-text)]">{p.trading_pair}</td>
                      <td className="py-2 pr-3">
                        <span className={sideLabel === "BUY" ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}>
                          {sideLabel}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-[var(--color-text)]">{Math.abs(amount).toFixed(4)}</td>
                      <td className="py-2 pr-3 text-right font-mono text-[var(--color-text-muted)]">${entry.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-right font-mono text-[var(--color-text)]">${current.toFixed(2)}</td>
                      <td className={`py-2 pr-3 text-right font-mono ${upnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                        {formatCurrencyPnl(upnl)}
                      </td>
                      <td className="py-2 text-right font-mono text-[var(--color-text-muted)]">{p.leverage ? `${p.leverage}x` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <ExecutorTable
        executors={executorInfos}
        sortKey={sortKey}
        sortDir={sortDir}
        onSort={handleSort}
        showCheckboxes={false}
        highlightSelectedIds
        selectedIds={selectedIds}
        onRowClick={(ex) => toggleSelect(ex.id)}
        selectedExecutorId={null}
        onStop={handleStopOne}
        stoppingIds={stoppingIds}
        rateFormatPnl={formatPnlValue}
        rateFormatValue={formatValue}
        rateFormatDetailed={formatValueDetailed}
      />

      {pendingStopIds && (
        <StopConfirmDialog
          ids={pendingStopIds}
          onConfirm={handleConfirmStop}
          onCancel={() => setPendingStopIds(null)}
        />
      )}

      {selectedExecutors.length > 0 && (
        <div className={detailPanelGridClass(selectedExecutors.length)}>
          {selectedExecutors.map((ex) => (
            <DetailPanel
              key={ex.id}
              variant="inline"
              executor={ex}
              server={serverName}
              enrichmentContext={sessionEnrichment}
              onClose={() => toggleSelect(ex.id)}
              onStop={handleStopOne}
              stopping={stoppingIds.has(ex.id)}
              rateFormatPnl={formatPnlValue}
              rateFormatValue={formatValue}
              rateFormatDetailed={formatValueDetailed}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Session Snapshots ──

export function SessionSnapshots({ slug, sslug, sessionNum, initialTick }: { slug: string; sslug: string; sessionNum: number; initialTick?: number | null }) {
  const [selectedTick, setSelectedTick] = useState<number>(initialTick ?? 0);

  const { data: snapshotsData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "snapshots"],
    queryFn: () => api.getSessionSnapshots(slug, sslug, sessionNum),
  });

  const snapshots = snapshotsData?.snapshots || [];

  if (snapshots.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No snapshots yet.</p>;
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* Snapshot list */}
      <div className="w-full shrink-0 lg:w-72">
        <div className="max-h-[600px] space-y-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2">
          {snapshots.map((snap) => (
            <button
              key={snap.tick}
              onClick={() => setSelectedTick(snap.tick)}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left transition-colors ${
                selectedTick === snap.tick
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs font-bold">#{snap.tick}</span>
                <span className="text-[10px]">{snap.timestamp}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Snapshot detail */}
      <div className="min-w-0 flex-1">
        {selectedTick > 0 ? (
          <SnapshotDetail slug={slug} sslug={sslug} sessionNum={sessionNum} tick={selectedTick} />
        ) : (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select a snapshot to view details.</p>
        )}
      </div>
    </div>
  );
}

// ── Snapshot Detail ──

function SnapshotDetail({ slug, sslug, sessionNum, tick }: { slug: string; sslug: string; sessionNum: number; tick: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "snapshot", tick],
    queryFn: () => api.getSnapshot(slug, sslug, sessionNum, tick),
    enabled: tick > 0,
  });

  const parsed = useMemo<ParsedSnapshot | null>(() => {
    if (!data?.content) return null;
    return parseSnapshot(data.content);
  }, [data?.content]);

  if (isLoading) {
    return (
      <div className="flex h-48 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (!parsed) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select a snapshot to view details.</p>;
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-lg font-bold text-[var(--color-text)]">#{parsed.tick}</span>
        <span className="text-sm text-[var(--color-text-muted)]">{parsed.timestamp}</span>
      </div>

      {/* System Prompt */}
      {parsed.systemPrompt && (
        <SystemPromptCard prompt={parsed.systemPrompt} charCount={parsed.systemPromptLength} />
      )}

      {/* Agent Response */}
      {parsed.agentResponse && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h4 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Agent Response</h4>
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-text)]">
            {parsed.agentResponse}
          </div>
        </div>
      )}

      {/* Tool Calls */}
      {parsed.toolCalls.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h4 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            <Wrench className="h-3 w-3" /> Tool Calls ({parsed.toolCalls.length})
          </h4>
          <div className="flex flex-wrap gap-2">
            {parsed.toolCalls.map((tc) => (
              <ToolCallChip key={tc.number} tc={tc} />
            ))}
          </div>
        </div>
      )}

      {/* Risk + Executor side by side */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {parsed.riskState && (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Risk State</h4>
            <div className="space-y-1 font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {parsed.riskState.split("\n").map((line, i) => {
                const isBlocked = line.includes("BLOCKED");
                const isActiveLine = line.includes("ACTIVE");
                return (
                  <div key={i} className={isBlocked ? "text-red-400" : isActiveLine ? "text-emerald-400" : ""}>
                    {line.replace(/^- /, "")}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {parsed.executorState && (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Executor State</h4>
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {parsed.executorState}
            </pre>
          </div>
        )}
      </div>

      {/* Stats Footer */}
      {parsed.stats.duration > 0 && (
        <div className="flex flex-wrap gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 font-mono text-xs text-[var(--color-text-muted)]">
          <span>Duration: <strong className="text-[var(--color-text)]">{parsed.stats.duration.toFixed(1)}s</strong></span>
        </div>
      )}
    </div>
  );
}

// ── Tool Call Chip ──

export function ToolCallChip({ tc }: { tc: import("@/lib/parse-agent").ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = tc.input || tc.output;
  const isOk = tc.status === "success" || tc.status === "completed";
  const isErr = tc.status === "error";
  const dotColor = isOk ? "bg-emerald-400" : isErr ? "bg-red-400" : "bg-[var(--color-text-muted)]";

  const shortName = tc.name.replace(/^mcp__\w+__/, "");

  if (!hasDetails) {
    return (
      <div className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)] px-2.5 py-1.5">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
        <span className="font-mono text-[11px] text-[var(--color-text)]">{shortName}</span>
      </div>
    );
  }

  return (
    <div className="w-full rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
          <span className="font-mono text-[11px] text-[var(--color-text)]">{shortName}</span>
        </div>
        {expanded ? <ChevronDown className="h-3 w-3 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3 w-3 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-[var(--color-border)]/30 p-3">
          {tc.input && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Input</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                {tc.input}
              </pre>
            </div>
          )}
          {tc.output && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Output</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                {tc.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── System Prompt Card ──

export function SystemPromptCard({ prompt, charCount }: { prompt: string; charCount: number }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between p-4 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">System Prompt</h4>
          <span className="text-[10px] text-[var(--color-text-muted)]">({charCount.toLocaleString()} chars)</span>
        </div>
        {expanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && (
        <pre className="max-h-96 overflow-auto border-t border-[var(--color-border)] p-4 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          {prompt}
        </pre>
      )}
    </div>
  );
}
