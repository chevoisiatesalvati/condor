import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ChevronDown, ChevronRight, Save } from "lucide-react";
import { useCallback, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { computeMaxTotalExposure } from "@/components/agent/AgentSessionConfigFields";
import { ModeBadge } from "@/components/agent/ModeBadge";
import {
  InstanceLifecycleButtons,
  ResumeSessionButton,
} from "@/components/agent/SessionLifecycleActions";
import { PerformanceStats } from "@/components/strategy/PerformanceStats";
import { SessionsTable, type SessionTableRow } from "@/components/strategy/SessionsTable";
import { api } from "@/lib/api";
import { formatCurrencyPnl } from "@/lib/formatters";

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
  const sessions = allRows.filter((session) => session.kind === "session");

  const closed = sessions.reduce((sum, session) => sum + session.closed_count, 0);
  const wins = sessions.reduce(
    (sum, session) => sum + Math.round(session.win_rate * session.closed_count),
    0,
  );
  const winRate = closed > 0 ? (wins / closed) * 100 : 0;
  const trades = sessions.reduce((sum, session) => sum + session.trade_count, 0);

  const tableRows: SessionTableRow[] = allRows.map((session) => ({
    id: session.agent_id,
    session_num: session.session_num,
    kind: session.kind,
    status: session.status,
    total_pnl: session.total_pnl,
    realized_pnl: session.realized_pnl,
    unrealized_pnl: session.unrealized_pnl,
    volume: session.volume,
    trade_count: session.trade_count,
    open_count: session.open_count,
  }));

  return (
    <div className="space-y-4 lg:col-span-2">
      <PerformanceStats
        totals={{
          total_pnl: Number(totals.total_pnl ?? 0),
          realized_pnl: Number(totals.realized_pnl ?? 0),
          unrealized_pnl: Number(totals.unrealized_pnl ?? 0),
          volume: Number(totals.volume ?? 0),
          open_positions: Number(totals.open_positions ?? 0),
        }}
        extended={{
          fees: Number(totals.fees ?? 0),
          win_rate_pct: winRate,
          trades,
        }}
        chartSessions={sessions}
      />
      <SessionsTable
        rows={tableRows}
        showKind
        showChevron={Boolean(onSessionClick)}
        resetKey={`${slug}:${sslug}`}
        onRowClick={
          onSessionClick
            ? (row) => onSessionClick(row.session_num, row.kind)
            : undefined
        }
        renderActions={(row) =>
          row.kind !== "experiment" && row.status !== "running" ? (
            <ResumeSessionButton slug={slug} sslug={sslug} sessionNum={row.session_num} />
          ) : null
        }
      />
    </div>
  );
}

