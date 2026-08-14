import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, FlaskConical, Layers, ScrollText, Settings, Trash2, X, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import {
  InstanceCard,
  LearningsArchivePanel,
  MarkdownEditor,
  PerformancePanel,
} from "@/components/agent/AgentOverviewTab";
import { SessionReviewer } from "@/components/agent/SessionReviewer";
import { StrategyDefaultsDialog } from "@/components/agent/StrategyDefaultsDialog";
import { StrategyPresetsDialog } from "@/components/agent/StrategyPresetsDialog";
import { DiscardChangesDialog } from "@/components/editor/EditorDialogs";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { DetailActionButton } from "@/components/strategy/DetailActionButton";
import {
  DetailError,
  DetailLoading,
  DetailPageHeader,
  MetaChip,
} from "@/components/strategy/DetailPageHeader";
import { SectionCard } from "@/components/strategy/SectionCard";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api } from "@/lib/api";

// ── Strategy Detail Page ──
//
// A strategy is a playbook that loops under an Agent. This page holds the rich
// operational view: sessions/experiments, controls, PnL and the
// strategy.md / learnings editors. The owning Agent's identity lives one level up
// at /agents/:slug.

export function StrategyDetail() {
  const { slug, sslug } = useParams<{ slug: string; sslug: string }>();
  const navigate = useNavigate();
  const location = useLocation();

  const queryClient = useQueryClient();
  const [reviewerSessionNum, setReviewerSessionNum] = useState<number | null>(null);
  const [reviewerKind, setReviewerKind] = useState<"session" | "experiment">("session");
  const [showStrategyModal, setShowStrategyModal] = useState(false);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showDefaults, setShowDefaults] = useState(false);
  const [showPresets, setShowPresets] = useState(false);
  // Unsaved-edit guards for the Playbook/Learnings editors (CORR-093)
  const [playbookDirty, setPlaybookDirty] = useState(false);
  const [learningsDirty, setLearningsDirty] = useState(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);

  const deleteMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug!, sslug!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      navigate(`/agents/${slug}`);
    },
  });

  // Check location.state for session-deep-linking (SessionReviewer nav)
  useEffect(() => {
    const state = location.state as { openReviewer?: boolean; sessionNum?: number } | null;
    if (state?.openReviewer) {
      setReviewerSessionNum(state.sessionNum ?? null);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.state, location.pathname, navigate]);

  // Close the strategy modal, dropping any unsaved-edit guards.
  const closeStrategyModal = useCallback(() => {
    setShowStrategyModal(false);
    setShowDiscardConfirm(false);
    setPlaybookDirty(false);
    setLearningsDirty(false);
  }, []);

  // Backdrop click, Escape and the X button all route through here: with
  // unsaved edits they ask for confirmation instead of silently discarding.
  const requestCloseStrategyModal = useCallback(() => {
    if (playbookDirty || learningsDirty) {
      setShowDiscardConfirm(true);
    } else {
      closeStrategyModal();
    }
  }, [playbookDirty, learningsDirty, closeStrategyModal]);

  // Close strategy modal on Escape (the discard dialog owns Escape while open)
  useEscapeKey(showStrategyModal && !showDiscardConfirm, requestCloseStrategyModal);

  const { data: strategy, isLoading, error } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug!, sslug!),
    enabled: !!slug && !!sslug,
    refetchInterval: 5000,
  });

  // Routine instances for ReportBrowser
  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  // Derive controller IDs from active instances for WS executor streaming
  const instances = strategy?.instances || [];
  const hasRunning = instances.length > 0;
  const hasLiveInstance = instances.some((inst) => inst.status === "running");
  const serverName = (strategy?.config?.server_name as string) || "";

  const controllerIds = useMemo(
    () => instances.map((inst) => inst.agent_id).filter(Boolean),
    [instances],
  );

  // Session/experiment click -> open reviewer
  const handleSessionClick = useCallback((sessionNum: number, kind?: "session" | "experiment") => {
    setReviewerSessionNum(sessionNum);
    setReviewerKind(kind || "session");
  }, []);

  if (error && !strategy) {
    return (
      <DetailError
        title="Failed to Load Strategy"
        message={error instanceof Error ? error.message : "An unexpected error occurred."}
        backHref={`/agents/${slug}`}
        backLabel="Back to Agent"
      />
    );
  }

  if (isLoading || !strategy) {
    return <DetailLoading />;
  }

  const reviewerOpen = reviewerSessionNum !== null;
  const resolvedReviewerSession =
    reviewerSessionNum ?? (strategy.sessions.length > 0 ? strategy.sessions[0].number : 0);

  return (
    <div className="w-full">
      <DetailPageHeader
        backHref={`/agents/${slug}`}
        backLabel="Back to Agent"
        parentLabel={slug}
        title={strategy.name}
        description={strategy.description}
        meta={
          <>
            <MetaChip>
              {strategy.sessions.length} session{strategy.sessions.length !== 1 ? "s" : ""}
            </MetaChip>
            <MetaChip mono>{strategy.slug}</MetaChip>
            {strategy.agent_id ? <MetaChip mono>{strategy.agent_id}</MetaChip> : null}
          </>
        }
        actions={
          <>
            <DetailActionButton
              onClick={() => setShowStrategyModal(true)}
              title="Playbook & Learnings"
            >
              <FileText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Playbook</span>
            </DetailActionButton>
            {strategy.experiments.length > 0 ? (
              <DetailActionButton
                onClick={() =>
                  handleSessionClick(
                    Math.max(...strategy.experiments.map((experiment) => experiment.number)),
                    "experiment",
                  )
                }
                title="Dry-run & run-once snapshots"
              >
                <FlaskConical className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">
                  Dry runs ({strategy.experiments.length})
                </span>
              </DetailActionButton>
            ) : null}
            <DetailActionButton
              onClick={() => setShowRoutinesBrowser(true)}
              title="Routines & Reports"
            >
              <ScrollText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Routines</span>
            </DetailActionButton>
            {(strategy.strategy_presets?.length ?? 0) > 0 ? (
              <DetailActionButton
                onClick={() => setShowPresets(true)}
                title="Manage strategy presets"
              >
                <Layers className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Presets</span>
              </DetailActionButton>
            ) : null}
            <DetailActionButton
              onClick={() => setShowDefaults(true)}
              title="Session Defaults"
            >
              <Settings className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Settings</span>
            </DetailActionButton>
            <DetailActionButton
              variant="danger"
              onClick={() => setShowDeleteConfirm(true)}
              disabled={hasLiveInstance}
              title={
                hasLiveInstance
                  ? "Stop all running sessions before deleting"
                  : "Delete strategy"
              }
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Delete</span>
            </DetailActionButton>
            <AgentControls
              slug={slug!}
              sslug={sslug!}
              defaultContext={
                strategy.default_trading_context ||
                (strategy.config.trading_context as string) ||
                ""
              }
              defaultAgentKey={strategy.defaults?.agent_key ?? ""}
              agentConfig={strategy.defaults?.default_config ?? strategy.config}
              strategyPresets={strategy.strategy_presets ?? []}
            />
          </>
        }
      />

      {hasRunning ? (
        <SectionCard
          title={`Active Sessions (${instances.length})`}
          icon={Zap}
          live
          className="mb-6"
        >
          <div className="space-y-3">
            {instances.map((inst) => (
              <InstanceCard key={inst.agent_id} instance={inst} slug={slug!} sslug={sslug!} />
            ))}
          </div>
        </SectionCard>
      ) : null}

      {/* Performance Panel + Sessions table */}
      <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <PerformancePanel
          slug={slug!}
          sslug={sslug!}
          onSessionClick={handleSessionClick}
        />
      </div>

      {/* Playbook & Learnings Modal (near full-screen) */}
      {showStrategyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60"
            onClick={requestCloseStrategyModal}
          />
          {/* Modal panel */}
          <div className="relative z-10 flex h-[90vh] w-[95vw] max-w-7xl flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl">
            {/* Modal header */}
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-3">
              <h3 className="text-sm font-semibold text-[var(--color-text)]">
                Playbook & Learnings — {strategy.name}
              </h3>
              <button
                onClick={requestCloseStrategyModal}
                className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            {/* Modal content */}
            <div className="flex-1 overflow-y-auto p-6">
              <div className="grid h-full grid-cols-1 gap-6 lg:grid-cols-2">
                <MarkdownEditor
                  label="Playbook"
                  sublabel="strategy.md"
                  content={strategy.strategy_md}
                  onSave={(value) => api.updateStrategyMd(slug!, sslug!, value)}
                  invalidateKey={["strategy", slug, sslug]}
                  onDirtyChange={setPlaybookDirty}
                />
                <div className="flex flex-col gap-4">
                  <MarkdownEditor
                    label="Learnings"
                    sublabel="persists across sessions"
                    content={strategy.learnings}
                    onSave={(value) => api.updateStrategyLearnings(slug!, sslug!, value)}
                    invalidateKey={["strategy", slug, sslug]}
                    onDirtyChange={setLearningsDirty}
                  />
                  <LearningsArchivePanel slug={slug!} sslug={sslug!} />
                </div>
              </div>
            </div>
          </div>
          {/* Unsaved-changes confirmation before discarding edits */}
          {showDiscardConfirm && (
            <DiscardChangesDialog
              fileName={
                playbookDirty && learningsDirty
                  ? "strategy.md & learnings"
                  : playbookDirty
                    ? "strategy.md"
                    : "learnings"
              }
              onDiscard={closeStrategyModal}
              onClose={() => setShowDiscardConfirm(false)}
            />
          )}
        </div>
      )}

      {/* Routines ReportBrowser (full-screen overlay; routines live at the agent
          level and are shared across strategies, so filter by the agent slug) */}
      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      {/* Delete Confirmation Dialog */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowDeleteConfirm(false)}>
          <div
            className="w-full max-w-sm rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-2 text-lg font-semibold text-[var(--color-text)]">Delete Strategy</h2>
            <p className="mb-6 text-sm text-[var(--color-text-muted)]">
              Delete <strong className="text-[var(--color-text)]">{strategy.name}</strong>? This cannot be undone.
            </p>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setShowDeleteConfirm(false)}
                className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                Cancel
              </button>
              <button
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white transition-opacity hover:bg-red-600 disabled:opacity-40"
              >
                {deleteMutation.isPending ? "Deleting..." : "Delete"}
              </button>
            </div>
            {deleteMutation.isError && (
              <p className="mt-3 text-xs text-red-400">Failed to delete strategy. It may be running.</p>
            )}
          </div>
        </div>
      )}

      <StrategyDefaultsDialog
        open={showDefaults}
        onClose={() => setShowDefaults(false)}
        slug={slug!}
        sslug={sslug!}
      />

      <StrategyPresetsDialog
        open={showPresets}
        onClose={() => setShowPresets(false)}
        slug={slug!}
        sslug={sslug!}
      />

      {/* Session Reviewer Overlay */}
      {reviewerOpen && (strategy.sessions.length > 0 || strategy.experiments.length > 0) && (
        <SessionReviewer
          slug={slug!}
          sslug={sslug!}
          agentName={`${slug} / ${strategy.name}`}
          sessions={strategy.sessions}
          experiments={strategy.experiments}
          initialSessionNum={resolvedReviewerSession}
          initialKind={reviewerKind}
          serverName={serverName}
          controllerIds={controllerIds}
          runningInstances={instances}
          onClose={() => setReviewerSessionNum(null)}
        />
      )}
    </div>
  );
}
