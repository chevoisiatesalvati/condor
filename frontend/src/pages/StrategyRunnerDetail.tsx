import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pause, Play, Settings, Square, Zap } from "lucide-react";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { StatusBadge } from "@/components/agent/StatusBadge";
import { DetailActionButton } from "@/components/strategy/DetailActionButton";
import {
  DetailError,
  DetailLoading,
  DetailPageHeader,
  MetaChip,
} from "@/components/strategy/DetailPageHeader";
import { DeterministicRunDialog } from "@/components/strategy/DeterministicRunDialog";
import { DeterministicSessionReviewer } from "@/components/strategy/DeterministicSessionReviewer";
import { PerformanceStats } from "@/components/strategy/PerformanceStats";
import { SectionCard } from "@/components/strategy/SectionCard";
import { SessionsTable, type SessionTableRow } from "@/components/strategy/SessionsTable";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

function formatTs(ts?: number | null) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

type SessionPerfRow = {
  agent_id?: string;
  session_num: number;
  status?: string;
  total_pnl?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  volume?: number;
  trade_count?: number;
  open_count?: number;
};

export function StrategyRunnerDetail() {
  const { slug = "" } = useParams();
  const { server } = useServer();
  const queryClient = useQueryClient();
  const [reviewerSessionNum, setReviewerSessionNum] = useState<number | null>(null);
  const [showRunDialog, setShowRunDialog] = useState(false);

  const { data: strategy, isLoading, error } = useQuery({
    queryKey: ["deterministic-strategy", slug],
    queryFn: () => api.getDeterministicStrategy(slug),
    enabled: Boolean(slug),
    refetchInterval: 5_000,
  });

  const { data: promoteInfo } = useQuery({
    queryKey: ["deterministic-strategy-promote", slug],
    queryFn: () => api.getStrategyPromote(slug),
    enabled: Boolean(slug),
  });

  const { data: sessionsInfo } = useQuery({
    queryKey: ["deterministic-strategy-sessions", slug],
    queryFn: () => api.getDeterministicStrategySessions(slug),
    enabled: Boolean(slug),
    refetchInterval: 10_000,
  });

  const { data: performance } = useQuery({
    queryKey: ["deterministic-strategy-performance", slug],
    queryFn: () => api.getDeterministicStrategyPerformance(slug),
    enabled: Boolean(slug),
    refetchInterval: 15_000,
  });

  const isLive = strategy?.status === "running" || strategy?.status === "paused";
  const isOrphaned = strategy?.status === "orphaned";
  const showActiveSession =
    isLive || isOrphaned || Number(performance?.totals?.open_positions || 0) > 0;

  const invalidateLifecycle = () => {
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategy", slug] });
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-sessions", slug] });
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-performance", slug] });
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-live-executors", slug] });
  };

  const resumeConfig = () => ({
    ...(strategy?.default_config || {}),
    server_name: server || strategy?.default_config?.server_name || "local",
  });

  const resumeSessionMutation = useMutation({
    mutationFn: (sessionNum: number) =>
      api.startDeterministicStrategy(slug, {
        config: resumeConfig(),
        strategy_preset:
          strategy?.promoted_preset ||
          String(strategy?.default_config?.strategy_preset || ""),
        session_num: sessionNum,
      }),
    onSuccess: invalidateLifecycle,
  });

  const stopMutation = useMutation({
    mutationFn: () => api.stopDeterministicStrategy(slug),
    onSuccess: invalidateLifecycle,
  });

  const pauseMutation = useMutation({
    mutationFn: () => api.pauseDeterministicStrategy(slug),
    onSuccess: invalidateLifecycle,
  });

  const unpauseMutation = useMutation({
    mutationFn: () => api.resumeDeterministicStrategyLoop(slug),
    onSuccess: invalidateLifecycle,
  });

  const sessionRows = useMemo(() => {
    const perfRows = (performance?.sessions || []) as SessionPerfRow[];
    const sessionStatusByNum = new Map(
      (sessionsInfo?.sessions || []).map((session) => [
        session.session_num,
        session.status || "closed",
      ]),
    );
    const fallbackRows: SessionPerfRow[] = (sessionsInfo?.sessions || []).map((session) => ({
      session_num: session.session_num,
      agent_id: session.agent_id,
      status: session.status,
      total_pnl: 0,
      realized_pnl: 0,
      unrealized_pnl: 0,
      volume: 0,
      trade_count: 0,
      open_count: 0,
    }));
    const displayRows = perfRows.length ? perfRows : fallbackRows;
    return displayRows.map((row) => ({
      id: String(row.agent_id || row.session_num),
      session_num: row.session_num,
      agent_id: row.agent_id || `${slug}_${row.session_num}`,
      status: row.status || sessionStatusByNum.get(row.session_num) || "closed",
      total_pnl: Number(row.total_pnl || 0),
      realized_pnl: Number(row.realized_pnl || 0),
      unrealized_pnl: Number(row.unrealized_pnl || 0),
      volume: Number(row.volume || 0),
      trade_count: Number(row.trade_count || 0),
      open_count: Number(row.open_count || 0),
    }));
  }, [performance?.sessions, sessionsInfo?.sessions, slug]);

  if (isLoading) {
    return <DetailLoading />;
  }
  if (error || !strategy) {
    return (
      <DetailError
        title="Failed to Load Strategy"
        message={(error as Error)?.message || "Not found"}
        backHref="/strategies"
        backLabel="Strategies"
      />
    );
  }

  const busy =
    resumeSessionMutation.isPending ||
    stopMutation.isPending ||
    pauseMutation.isPending ||
    unpauseMutation.isPending;
  const actionErr =
    (resumeSessionMutation.error as Error | null)?.message ||
    (stopMutation.error as Error | null)?.message ||
    (pauseMutation.error as Error | null)?.message ||
    (unpauseMutation.error as Error | null)?.message;

  const totals = performance?.totals || {};
  const tableRows: SessionTableRow[] = sessionRows.map((row) => ({
    id: row.id,
    session_num: row.session_num,
    status: row.status,
    total_pnl: row.total_pnl,
    realized_pnl: row.realized_pnl,
    unrealized_pnl: row.unrealized_pnl,
    volume: row.volume,
    trade_count: row.trade_count,
    open_count: row.open_count,
  }));
  const chartSessions = tableRows.map((row) => ({
    session_num: row.session_num,
    total_pnl: row.total_pnl,
    status: row.status,
  }));
  const serverName = String(server || strategy.default_config?.server_name || "local");

  return (
    <div className="w-full space-y-6">
      <DetailPageHeader
        backHref="/strategies"
        backLabel="Strategies"
        title={strategy.name}
        description={strategy.description}
        meta={
          <>
            <StatusBadge status={strategy.status} />
            <MetaChip mono>{strategy.slug}</MetaChip>
            {strategy.agent_id ? (
              <MetaChip mono>
                session {strategy.session_num} · {strategy.agent_id}
              </MetaChip>
            ) : null}
          </>
        }
        actions={
          <>
            <DetailActionButton onClick={() => setShowRunDialog(true)} title="Run config">
              <Settings className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Settings</span>
            </DetailActionButton>
            {strategy.status === "running" ? (
              <>
                <DetailActionButton disabled={busy} onClick={() => pauseMutation.mutate()}>
                  <Pause className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Pause</span>
                </DetailActionButton>
                <DetailActionButton disabled={busy} onClick={() => stopMutation.mutate()}>
                  {busy ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Square className="h-3.5 w-3.5" />
                  )}
                  <span className="hidden sm:inline">Stop</span>
                </DetailActionButton>
              </>
            ) : strategy.status === "paused" ? (
              <>
                <DetailActionButton
                  variant="info"
                  disabled={busy}
                  onClick={() => unpauseMutation.mutate()}
                >
                  <Play className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Unpause</span>
                </DetailActionButton>
                <DetailActionButton disabled={busy} onClick={() => stopMutation.mutate()}>
                  <Square className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Stop</span>
                </DetailActionButton>
              </>
            ) : isOrphaned ? (
              <DetailActionButton
                variant="success"
                disabled={busy || strategy.session_num == null}
                onClick={() => resumeSessionMutation.mutate(strategy.session_num!)}
              >
                {busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )}
                <span className="hidden sm:inline">Resume session {strategy.session_num}</span>
              </DetailActionButton>
            ) : (
              <DetailActionButton
                variant="success"
                disabled={busy}
                onClick={() => setShowRunDialog(true)}
              >
                <Play className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Start</span>
              </DetailActionButton>
            )}
          </>
        }
      />

      {actionErr ? <p className="text-sm text-red-400">{actionErr}</p> : null}

      {showActiveSession ? (
        <SectionCard
          title={
            <>
              Active session
              {isOrphaned ? (
                <span className="ml-2 text-[10px] font-normal normal-case tracking-normal text-amber-400">
                  (orphaned — positions may still be live)
                </span>
              ) : null}
            </>
          }
          icon={Zap}
          live={isLive}
        >
          <dl className="grid gap-2 text-sm text-[var(--color-text-muted)] sm:grid-cols-2 lg:grid-cols-3">
            <div className="flex justify-between gap-4 sm:block">
              <dt>State</dt>
              <dd className="font-medium text-[var(--color-text)]">{strategy.status}</dd>
            </div>
            <div className="flex justify-between gap-4 sm:block">
              <dt>Session</dt>
              <dd className="font-mono text-[var(--color-text)]">{strategy.agent_id || "—"}</dd>
            </div>
            <div className="flex justify-between gap-4 sm:block">
              <dt>Ticks</dt>
              <dd className="text-[var(--color-text)]">{strategy.tick_count ?? "—"}</dd>
            </div>
            <div className="flex justify-between gap-4 sm:block">
              <dt>Last tick</dt>
              <dd className="text-[var(--color-text)]">{formatTs(strategy.last_tick_at)}</dd>
            </div>
            <div className="flex justify-between gap-4 sm:col-span-2 sm:block">
              <dt>Last summary</dt>
              <dd className="font-mono text-xs text-[var(--color-text)]">
                {strategy.last_tick_summary || "—"}
              </dd>
            </div>
          </dl>
          {strategy.last_error ? (
            <p className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">
              {strategy.last_error}
            </p>
          ) : null}
        </SectionCard>
      ) : null}

      <PerformanceStats
        totals={{
          total_pnl: Number(totals.total_pnl || 0),
          realized_pnl: Number(totals.realized_pnl || 0),
          unrealized_pnl: Number(totals.unrealized_pnl || 0),
          volume: Number(totals.volume || 0),
          open_positions: Number(totals.open_positions || 0),
        }}
        chartSessions={chartSessions}
      />

      <SessionsTable
        rows={tableRows}
        showChevron
        selectedSession={reviewerSessionNum}
        resetKey={slug}
        onRowClick={(row) => setReviewerSessionNum(row.session_num)}
        renderActions={(row) => {
          const canResumeRow =
            row.status !== "running" && row.status !== "paused" && !isLive;
          if (!canResumeRow) return null;
          return (
            <DetailActionButton
              variant="success"
              disabled={busy}
              onClick={(event) => {
                event.stopPropagation();
                resumeSessionMutation.mutate(row.session_num);
              }}
            >
              Resume
            </DetailActionButton>
          );
        }}
      />

      <DeterministicRunDialog
        open={showRunDialog}
        onClose={() => setShowRunDialog(false)}
        slug={slug}
        strategy={strategy}
        promoteInfo={promoteInfo}
        server={server}
      />

      {reviewerSessionNum != null && sessionRows.length > 0 ? (
        <DeterministicSessionReviewer
          slug={slug}
          strategyName={strategy.name}
          serverName={serverName}
          sessionConfig={strategy.default_config}
          sessions={sessionRows}
          initialSessionNum={reviewerSessionNum}
          liveSessionNum={strategy.session_num}
          liveStatus={strategy.status}
          onClose={() => setReviewerSessionNum(null)}
        />
      ) : null}
    </div>
  );
}
