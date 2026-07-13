import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  FlaskConical,
  Save,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AgentPnlChart, sessionsToDataPoints } from "@/components/agent/AgentPnlChart";
import { computeMaxTotalExposure } from "@/components/agent/AgentSessionConfigFields";
import { ModeBadge } from "@/components/agent/ModeBadge";
import {
  InstanceLifecycleButtons,
  ResumeSessionButton,
} from "@/components/agent/SessionLifecycleActions";
import { api } from "@/lib/api";
import { formatCurrency, formatCurrencyPnl, formatCurrencyVolume } from "@/lib/formatters";

// ── Markdown Editor ──

export function MarkdownEditor({
  label,
  sublabel,
  content,
  onSave,
  invalidateKey,
  onDirtyChange,
}: {
  label: string;
  sublabel: string;
  content: string;
  onSave: (value: string) => Promise<unknown>;
  invalidateKey: unknown[];
  /** Notifies the host (e.g. a closable modal) when there are unsaved edits. */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(content);
  const [dirty, setDirty] = useState(false);

  const saveMut = useMutation({
    mutationFn: () => onSave(value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: invalidateKey });
      setDirty(false);
      onDirtyChange?.(false);
    },
  });

  const handleChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setValue(e.target.value);
    setDirty(true);
    onDirtyChange?.(true);
  }, [onDirtyChange]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">{label}</span>
          <span className="ml-2 text-[10px] text-[var(--color-text-muted)]">{sublabel}</span>
        </div>
        <button
          onClick={() => saveMut.mutate()}
          disabled={!dirty || saveMut.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-all disabled:opacity-30"
        >
          <Save className="h-3.5 w-3.5" />
          {saveMut.isPending ? "Saving..." : "Save"}
        </button>
      </div>
      {saveMut.isError && (
        <div className="rounded-md border border-[var(--color-red)]/40 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {saveMut.error instanceof Error ? saveMut.error.message : "Save failed"}
        </div>
      )}
      <textarea
        value={value}
        onChange={handleChange}
        spellCheck={false}
        className="min-h-[500px] w-full resize-y rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 font-mono text-sm leading-relaxed text-[var(--color-text)] outline-none transition-colors focus:border-[var(--color-primary)]/50"
      />
    </div>
  );
}

export function LearningsArchivePanel({ slug, sslug }: { slug: string; sslug: string }) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["strategy", slug, sslug, "learnings-archive"],
    queryFn: () => api.getStrategyLearningsArchive(slug, sslug),
    enabled: expanded,
  });
  const content = data?.content?.trim() ?? "";

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        type="button"
        onClick={() => setExpanded((open) => !open)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-[var(--color-text-muted)]" />
          ) : (
            <ChevronRight className="h-4 w-4 text-[var(--color-text-muted)]" />
          )}
          <Archive className="h-4 w-4 text-[var(--color-text-muted)]" />
          <span className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Archive (not sent to agent)
          </span>
        </div>
        <span className="text-[10px] text-[var(--color-text-muted)]">
          Historical market observations — for debugging only
        </span>
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)] px-4 py-4">
          {isLoading && (
            <p className="text-sm text-[var(--color-text-muted)]">Loading archive...</p>
          )}
          {isError && (
            <p className="text-sm text-red-400">Failed to load archive.</p>
          )}
          {!isLoading && !isError && !content && (
            <p className="text-sm text-[var(--color-text-muted)]">No archived observations yet.</p>
          )}
          {!isLoading && !isError && content && (
            <div className="prose prose-invert max-w-none text-sm text-[var(--color-text)]">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Instance Card ──

export function InstanceCard({
  instance,
  slug,
  sslug,
}: {
  instance: import("@/lib/api").RunningInstance;
  slug?: string;
  sslug?: string;
}) {
  const riskLimits = (instance.risk_limits || {}) as Record<string, unknown>;
  const maxExecutors = Number(riskLimits.max_open_executors ?? 0);
  const maxTotalExposure = computeMaxTotalExposure(instance.total_amount_quote, maxExecutors);
  const statusColor = instance.status === "running" ? "text-emerald-400" : instance.status === "paused" ? "text-amber-400" : "text-[var(--color-text-muted)]";

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm font-bold text-[var(--color-text)]">{instance.agent_id}</span>
          <span className={`text-xs font-semibold uppercase ${statusColor}`}>{instance.status}</span>
          <ModeBadge mode={instance.execution_mode} />
        </div>
        <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
          {slug && sslug && <InstanceLifecycleButtons slug={slug} sslug={sslug} instance={instance} />}
          <span>Ticks: {instance.tick_count}</span>
          <span className={instance.daily_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}>
            PnL: {formatCurrencyPnl(instance.daily_pnl)}
          </span>
        </div>
      </div>

      {instance.trading_context && (
        <p className="mb-3 whitespace-pre-wrap rounded-md bg-[var(--color-surface)] p-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
          {instance.trading_context}
        </p>
      )}

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs md:grid-cols-4">
        {instance.agent_key && (
          <div className="flex justify-between">
            <span className="text-[var(--color-text-muted)]">model</span>
            <span className="text-[var(--color-primary)]">{instance.agent_key}</span>
          </div>
        )}
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">server</span>
          <span className="text-[var(--color-text)]">{instance.server_name || "auto"}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">budget</span>
          <span className="text-[var(--color-text)]">${instance.total_amount_quote}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-[var(--color-text-muted)]">frequency</span>
          <span className="text-[var(--color-text)]">{instance.frequency_sec}s</span>
        </div>
        {maxTotalExposure > 0 && (
          <div className="flex justify-between">
            <span className="text-[var(--color-text-muted)]">max exposure</span>
            <span className="text-[var(--color-text)]">${maxTotalExposure.toFixed(0)}</span>
          </div>
        )}
        {Object.entries(riskLimits).map(([k, v]) => {
          const label =
            k === "max_position_size_quote"
              ? "max position"
              : k === "max_open_executors"
                ? "max executors"
                : k.replace(/_/g, " ");
          const val = k === "max_position_size_quote" ? `$${v}` : String(v);
          return (
            <div key={k} className="flex justify-between">
              <span className="text-[var(--color-text-muted)]">{label}</span>
              <span className="text-[var(--color-text)]">{val}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const SESSIONS_PAGE_SIZE = 20;

// ── Performance Panel ──

export function PerformancePanel({
  slug,
  sslug,
  onSessionClick,
}: {
  slug: string;
  sslug: string;
  onSessionClick?: (sessionNum: number, kind?: "session" | "experiment") => void;
}) {
  const { data } = useQuery({
    queryKey: ["strategy-performance", slug, sslug],
    queryFn: () => api.getStrategyPerformance(slug, sslug),
    refetchInterval: 10000,
  });

  const totals = data?.totals || {};
  const allRows = data?.sessions || [];
  const sessions = allRows.filter((s) => s.kind === "session");
  const [page, setPage] = useState(0);

  const sortedRows = useMemo(
    () =>
      allRows
        .slice()
        .sort((a, b) =>
          b.kind === a.kind ? b.session_num - a.session_num : a.kind === "experiment" ? 1 : -1,
        ),
    [allRows],
  );

  const totalPages = Math.max(1, Math.ceil(sortedRows.length / SESSIONS_PAGE_SIZE));
  const pageStart = page * SESSIONS_PAGE_SIZE;
  const pageRows = sortedRows.slice(pageStart, pageStart + SESSIONS_PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [slug, sslug]);

  useEffect(() => {
    if (page > totalPages - 1) {
      setPage(Math.max(0, totalPages - 1));
    }
  }, [page, totalPages]);

  const totalPnl = Number(totals.total_pnl ?? 0);
  const realized = Number(totals.realized_pnl ?? 0);
  const unrealized = Number(totals.unrealized_pnl ?? 0);
  const volume = Number(totals.volume ?? 0);
  const fees = Number(totals.fees ?? 0);
  const openPos = Number(totals.open_positions ?? 0);
  const pnlColor = totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";

  const closed = sessions.reduce((s, x) => s + x.closed_count, 0);
  const wins = sessions.reduce((s, x) => s + Math.round(x.win_rate * x.closed_count), 0);
  const winRate = closed > 0 ? (wins / closed) * 100 : 0;
  const trades = sessions.reduce((s, x) => s + x.trade_count, 0);

  // PnL chart data from session-level performance
  const pnlData = useMemo(() => sessionsToDataPoints(sessions), [sessions]);

  return (
    <div className="space-y-4 lg:col-span-2">
      {/* Stat grid */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Zap className="h-3.5 w-3.5" /> Performance
        </h3>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-8">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Total PnL</span>
            <span className={`text-lg font-mono font-semibold ${pnlColor}`}>
              {formatCurrencyPnl(totalPnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Realized</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{formatCurrencyPnl(realized)}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Unrealized</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{formatCurrencyPnl(unrealized)}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Volume</span>
            <span className="text-lg font-mono text-[var(--color-text)]">
              {formatCurrencyVolume(volume)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Fees</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{formatCurrency(fees)}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Win Rate</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{winRate.toFixed(0)}%</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Trades</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{trades}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Open</span>
            <span className="text-lg font-mono text-[var(--color-text)]">{openPos}</span>
          </div>
        </div>
      </div>

      {/* PnL equity curve */}
      {pnlData.length > 1 && (
        <AgentPnlChart data={pnlData} height={180} title="PnL Equity Curve" />
      )}

      {/* Sessions & Experiments table */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Clock className="h-3.5 w-3.5" /> Sessions ({sessions.length})
          {allRows.filter((s) => s.kind === "experiment").length > 0 && (
            <span className="ml-1 flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-400">
              <FlaskConical className="h-2.5 w-2.5" />
              {allRows.filter((s) => s.kind === "experiment").length} experiments
            </span>
          )}
        </h3>
        {allRows.length === 0 ? (
          <p className="text-xs text-[var(--color-text-muted)]">No sessions yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                  <th className="px-2 py-1">#</th>
                  <th className="px-2 py-1">Kind</th>
                  <th className="px-2 py-1">Status</th>
                  <th className="px-2 py-1 text-right">Total PnL</th>
                  <th className="px-2 py-1 text-right">Realized</th>
                  <th className="px-2 py-1 text-right">Unrealized</th>
                  <th className="px-2 py-1 text-right">Volume</th>
                  <th className="px-2 py-1 text-right">Trades</th>
                  <th className="px-2 py-1 text-right">Open</th>
                  <th className="px-2 py-1 text-right">Actions</th>
                  {onSessionClick && <th className="px-2 py-1 w-6" />}
                </tr>
              </thead>
              <tbody>
                {pageRows.map((s) => {
                    const pnlCol = s.total_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
                    const isExperiment = s.kind === "experiment";
                    return (
                      <tr
                        key={s.agent_id}
                        onClick={() => onSessionClick?.(s.session_num, s.kind)}
                        className={`border-t border-[var(--color-border)]/40 font-mono ${onSessionClick ? "cursor-pointer transition-colors hover:bg-[var(--color-surface-hover)]" : ""}`}
                      >
                        <td className="px-2 py-1.5 text-[var(--color-text)]">{s.session_num}</td>
                        <td className="px-2 py-1.5">
                          {isExperiment ? (
                            <span className="inline-flex items-center gap-0.5 rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase text-amber-400">
                              <FlaskConical className="h-2.5 w-2.5" />
                              exp
                            </span>
                          ) : (
                            <span className="text-[var(--color-text-muted)]">{s.kind}</span>
                          )}
                        </td>
                        <td className={`px-2 py-1.5 ${s.status === "running" ? "text-emerald-400" : "text-[var(--color-text-muted)]"}`}>
                          {s.status || "—"}
                        </td>
                        <td className={`px-2 py-1.5 text-right ${pnlCol}`}>
                          {formatCurrencyPnl(s.total_pnl)}
                        </td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">{formatCurrencyPnl(s.realized_pnl)}</td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">{formatCurrencyPnl(s.unrealized_pnl)}</td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">
                          {formatCurrencyVolume(s.volume)}
                        </td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">{s.trade_count}</td>
                        <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">{s.open_count}</td>
                        <td className="px-2 py-1.5 text-right">
                          {!isExperiment && s.status !== "running" && (
                            <ResumeSessionButton slug={slug} sslug={sslug} sessionNum={s.session_num} />
                          )}
                        </td>
                        {onSessionClick && (
                          <td className="px-2 py-1.5 text-[var(--color-text-muted)]">
                            <ChevronRight className="h-3.5 w-3.5" />
                          </td>
                        )}
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        )}
        {sortedRows.length > SESSIONS_PAGE_SIZE && (
          <div className="mt-3 flex items-center justify-between border-t border-[var(--color-border)]/40 pt-3">
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {pageStart + 1}–{Math.min(pageStart + SESSIONS_PAGE_SIZE, sortedRows.length)} of {sortedRows.length}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="rounded p-1 hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-30"
                aria-label="Previous page"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="px-2 text-[10px] text-[var(--color-text-muted)]">
                {page + 1} / {totalPages}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="rounded p-1 hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-30"
                aria-label="Next page"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

