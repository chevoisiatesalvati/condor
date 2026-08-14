import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Loader2, Play, Square } from "lucide-react";
import { Link } from "react-router-dom";

import { StatusBadge } from "@/components/agent/StatusBadge";
import { api, type DeterministicStrategySummary } from "@/lib/api";
import { formatCurrencyPnl } from "@/lib/formatters";
import { useServer } from "@/hooks/useServer";

function StrategyCard({ strategy }: { strategy: DeterministicStrategySummary }) {
  const queryClient = useQueryClient();
  const { server } = useServer();

  const needsPromote =
    strategy.require_promoted && !strategy.promoted_preset;
  const isLive = strategy.status === "running" || strategy.status === "paused";
  const isOrphaned = strategy.status === "orphaned";
  const canQuickStart = strategy.status === "idle" && !needsPromote;

  const { data: performance } = useQuery({
    queryKey: ["deterministic-strategy-performance", strategy.slug],
    queryFn: () => api.getDeterministicStrategyPerformance(strategy.slug),
    refetchInterval: 30_000,
  });

  const startMutation = useMutation({
    mutationFn: () =>
      api.startDeterministicStrategy(strategy.slug, {
        config: {
          ...(strategy.default_config || {}),
          server_name: server || strategy.default_config?.server_name || "local",
        },
        strategy_preset:
          strategy.promoted_preset ||
          String(strategy.default_config?.strategy_preset || ""),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
    },
  });

  const resumeMutation = useMutation({
    mutationFn: () =>
      api.startDeterministicStrategy(strategy.slug, {
        config: {
          ...(strategy.default_config || {}),
          server_name: server || strategy.default_config?.server_name || "local",
        },
        session_num: strategy.session_num ?? undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
    },
  });

  const stopMutation = useMutation({
    mutationFn: () => api.stopDeterministicStrategy(strategy.slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
    },
  });

  const busy =
    startMutation.isPending || stopMutation.isPending || resumeMutation.isPending;
  const err =
    (startMutation.error as Error | null)?.message ||
    (stopMutation.error as Error | null)?.message ||
    (resumeMutation.error as Error | null)?.message;

  const totalPnl = Number(performance?.totals?.total_pnl ?? 0);
  const openPos = Number(performance?.totals?.open_positions ?? 0);

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Link
            to={`/strategies/${strategy.slug}`}
            className="text-base font-semibold text-[var(--color-text)] hover:text-[var(--color-primary)]"
          >
            {strategy.name}
          </Link>
          <p className="mt-1 text-sm text-[var(--color-text-muted)]">
            {strategy.description}
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--color-text-muted)]">
            <span className="rounded-md bg-[var(--color-bg)] px-2 py-1 font-mono">
              {strategy.slug}
            </span>
            <span className="rounded-md bg-[var(--color-bg)] px-2 py-1">
              {strategy.connector}
            </span>
            <StatusBadge status={strategy.status} />
            {strategy.promoted_preset ? (
              <span className="rounded-md bg-[var(--color-bg)] px-2 py-1">
                promoted: {strategy.promoted_preset}
              </span>
            ) : strategy.require_promoted ? (
              <span className="rounded-md bg-amber-500/15 px-2 py-1 text-amber-400">
                promote required
              </span>
            ) : null}
            {performance?.totals ? (
              <span className="rounded-md bg-[var(--color-bg)] px-2 py-1 font-mono">
                PnL {formatCurrencyPnl(totalPnl)} · open {openPos}
              </span>
            ) : null}
          </div>
          {isOrphaned ? (
            <p className="mt-2 text-xs text-amber-400">
              Session looks live on disk but no engine is registered (often after hot-reload).
              Resume session {strategy.session_num} or open the detail page.
            </p>
          ) : (
            <p className="mt-2 text-xs text-[var(--color-text-muted)]">
              Open the detail page to choose presets, edit params, and promote before live Start.
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isLive ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => stopMutation.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm hover:bg-[var(--color-surface-hover)]"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
              Stop
            </button>
          ) : isOrphaned ? (
            <button
              type="button"
              disabled={busy || strategy.session_num == null}
              onClick={() => resumeMutation.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-sm text-white hover:bg-emerald-500 disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              Resume
            </button>
          ) : (
            <button
              type="button"
              disabled={busy || !canQuickStart}
              title={
                needsPromote
                  ? "Promote a preset on the strategy detail page first"
                  : undefined
              }
              onClick={() => startMutation.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-2 text-sm text-white hover:opacity-90 disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              Start
            </button>
          )}
        </div>
      </div>
      {err ? <p className="mt-3 text-sm text-red-400">{err}</p> : null}
      {strategy.agent_id ? (
        <p className="mt-2 font-mono text-xs text-[var(--color-text-muted)]">
          session {strategy.session_num} · {strategy.agent_id}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Strategies — Condor-native deterministic runners (not Agents chat, not HB bots).
 */
export function Strategies() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["deterministic-strategies"],
    queryFn: api.getDeterministicStrategies,
    refetchInterval: 10_000,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-semibold text-[var(--color-text)]">
          <Activity className="h-5 w-5 text-[var(--color-primary)]" />
          Strategies
        </h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          Deterministic runners that share one decision engine with timeline backtests.
          Open a strategy to pick a preset, tune params, promote, then Start — no LLM on the
          tick path.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading strategies…
        </div>
      ) : null}
      {error ? (
        <p className="text-sm text-red-400">{(error as Error).message}</p>
      ) : null}

      <div className="space-y-4">
        {(data || []).map((strategy) => (
          <StrategyCard key={strategy.slug} strategy={strategy} />
        ))}
        {data && data.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No deterministic strategies registered.</p>
        ) : null}
      </div>
    </div>
  );
}
