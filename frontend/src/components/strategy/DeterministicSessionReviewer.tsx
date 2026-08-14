import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronLeft, ChevronRight, LayoutList, X } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { SessionExecutors } from "@/components/agent/AgentSessionContent";
import { StatusBadge } from "@/components/agent/StatusBadge";
import { DeterministicActivity } from "@/components/strategy/DeterministicActivity";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api } from "@/lib/api";
import { coerceAgentExecutorRow } from "@/lib/executors";
import { formatCurrencyPnl } from "@/lib/formatters";
import { parseJournal } from "@/lib/parse-agent";

const SUB_TABS = [
  { id: "positions", label: "Positions", icon: LayoutList },
  { id: "activity", label: "Activity", icon: Activity },
] as const;
type SubTabId = (typeof SUB_TABS)[number]["id"];

export type DeterministicReviewerSession = {
  session_num: number;
  status: string;
  total_pnl: number;
  agent_id?: string;
};

export function DeterministicSessionReviewer({
  slug,
  strategyName,
  serverName,
  sessionConfig,
  sessions,
  initialSessionNum,
  liveSessionNum,
  liveStatus,
  onClose,
}: {
  slug: string;
  strategyName: string;
  serverName: string;
  sessionConfig?: Record<string, unknown>;
  sessions: DeterministicReviewerSession[];
  initialSessionNum: number;
  liveSessionNum?: number | null;
  liveStatus?: string;
  onClose: () => void;
}) {
  const [selectedNum, setSelectedNum] = useState(initialSessionNum);
  const [activeSubTab, setActiveSubTab] = useState<SubTabId>("positions");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  useEscapeKey(true, onClose);

  const selected = sessions.find((session) => session.session_num === selectedNum);
  const agentId = selected?.agent_id || `${slug}_${selectedNum}`;
  const isLiveSession =
    liveSessionNum === selectedNum &&
    (liveStatus === "running" || liveStatus === "paused" || liveStatus === "orphaned");

  const { data: journalData } = useQuery({
    queryKey: ["deterministic-strategy-journal", slug, selectedNum],
    queryFn: () => api.getDeterministicSessionJournal(slug, selectedNum),
    enabled: selectedNum > 0,
  });

  const parsedJournal = useMemo(
    () => (journalData?.content ? parseJournal(journalData.content) : null),
    [journalData?.content],
  );

  const fetchSessionDetail = useCallback(
    async (_agentSlug: string, _strategySlug: string, sessionNum: number) => {
      const payload = await api.getDeterministicSessionExecutors(slug, sessionNum);
      return {
        executors: payload.executors.map((row) => coerceAgentExecutorRow(row)),
        session_config: sessionConfig ?? {},
      };
    },
    [slug, sessionConfig],
  );

  const selectSession = useCallback((sessionNum: number) => {
    setSelectedNum(sessionNum);
    setActiveSubTab("positions");
  }, []);

  const currentIdx = sessions.findIndex((session) => session.session_num === selectedNum);

  return (
    <div className="fixed inset-0 z-50 flex bg-[var(--color-bg)]">
      <div
        className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
          sidebarCollapsed ? "w-12" : "w-64"
        }`}
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2.5">
          {!sidebarCollapsed ? (
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Sessions
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => setSidebarCollapsed((open) => !open)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            {sidebarCollapsed ? (
              <ChevronRight className="h-3.5 w-3.5" />
            ) : (
              <ChevronLeft className="h-3.5 w-3.5" />
            )}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {sessions.map((session) => {
            const isActive = session.session_num === selectedNum;
            if (sidebarCollapsed) {
              return (
                <button
                  key={session.session_num}
                  type="button"
                  onClick={() => selectSession(session.session_num)}
                  className={`flex w-full items-center justify-center py-3 transition-colors ${
                    isActive
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title={`Session ${session.session_num}`}
                >
                  <span className="text-xs font-bold">{session.session_num}</span>
                </button>
              );
            }
            return (
              <button
                key={session.session_num}
                type="button"
                onClick={() => selectSession(session.session_num)}
                className={`w-full px-3 py-2.5 text-left transition-all ${
                  isActive
                    ? "border-l-2 border-l-[var(--color-primary)] bg-[var(--color-primary)]/5"
                    : "border-l-2 border-l-transparent hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span
                    className={`text-xs font-medium ${
                      isActive ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"
                    }`}
                  >
                    Session {session.session_num}
                  </span>
                  <StatusBadge status={session.status} />
                </div>
                <p
                  className={`mt-1 font-mono text-[10px] ${
                    session.total_pnl >= 0
                      ? "text-[var(--color-green)]"
                      : "text-[var(--color-red)]"
                  }`}
                >
                  {formatCurrencyPnl(session.total_pnl)}
                </p>
              </button>
            );
          })}
        </div>

        {!sidebarCollapsed ? (
          <div className="border-t border-[var(--color-border)] px-3 py-2 text-[9px] text-[var(--color-text-muted)]/40">
            esc close
          </div>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
          <div className="flex min-w-0 items-center gap-3">
            <h2 className="text-sm font-semibold text-[var(--color-text)]">
              Session {selectedNum}
            </h2>
            {selected ? <StatusBadge status={selected.status} /> : null}
            {selected ? (
              <span
                className={`font-mono text-xs ${
                  selected.total_pnl >= 0
                    ? "text-[var(--color-green)]"
                    : "text-[var(--color-red)]"
                }`}
              >
                {formatCurrencyPnl(selected.total_pnl)}
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-[var(--color-text-muted)]">{strategyName}</span>
            {currentIdx >= 0 ? (
              <span className="text-[10px] text-[var(--color-text-muted)]">
                {currentIdx + 1} / {sessions.length}
              </span>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              className="ml-1 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="flex items-center gap-1 border-b border-[var(--color-border)]/50 px-4 py-1.5">
          {SUB_TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setActiveSubTab(id)}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
                activeSubTab === id
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {activeSubTab === "positions" ? (
            <SessionExecutors
              slug={slug}
              sslug={slug}
              sessionNum={selectedNum}
              serverName={serverName}
              controllerIds={[agentId]}
              sessionSummary={parsedJournal?.summary}
              liveSessionStatus={isLiveSession ? liveStatus : selected?.status}
              journalExecutors={parsedJournal?.executors}
              isLiveSession={isLiveSession}
              source="deterministic"
              fetchSessionDetail={fetchSessionDetail}
              sessionConfig={sessionConfig ?? {}}
            />
          ) : (
            <DeterministicActivity slug={slug} sessionNum={selectedNum} />
          )}
        </div>
      </div>
    </div>
  );
}
