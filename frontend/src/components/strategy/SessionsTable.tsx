import { ChevronLeft, ChevronRight, Clock, FlaskConical } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { StatusBadge } from "@/components/agent/StatusBadge";
import { SectionCard } from "@/components/strategy/SectionCard";
import { formatCurrencyPnl, formatCurrencyVolume } from "@/lib/formatters";

export const SESSIONS_PAGE_SIZE = 20;

export interface SessionTableRow {
  id: string;
  session_num: number;
  kind?: "session" | "experiment";
  status: string;
  total_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  volume: number;
  trade_count: number;
  open_count: number;
}

export function SessionsTable({
  rows,
  showKind = false,
  showChevron = false,
  selectedSession = null,
  resetKey,
  onRowClick,
  renderActions,
  emptyMessage = "No sessions yet.",
}: {
  rows: SessionTableRow[];
  showKind?: boolean;
  showChevron?: boolean;
  selectedSession?: number | null;
  resetKey?: string;
  onRowClick?: (row: SessionTableRow) => void;
  renderActions?: (row: SessionTableRow) => ReactNode;
  emptyMessage?: string;
}) {
  const [page, setPage] = useState(0);

  const sessionCount = rows.filter((row) => row.kind !== "experiment").length;
  const experimentCount = rows.filter((row) => row.kind === "experiment").length;

  const sortedRows = useMemo(
    () =>
      rows.slice().sort((a, b) => {
        if (showKind && a.kind !== b.kind) {
          return a.kind === "experiment" ? 1 : -1;
        }
        return b.session_num - a.session_num;
      }),
    [rows, showKind],
  );

  const totalPages = Math.max(1, Math.ceil(sortedRows.length / SESSIONS_PAGE_SIZE));
  const pageStart = page * SESSIONS_PAGE_SIZE;
  const pageRows = sortedRows.slice(pageStart, pageStart + SESSIONS_PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [resetKey]);

  useEffect(() => {
    if (page > totalPages - 1) {
      setPage(Math.max(0, totalPages - 1));
    }
  }, [page, totalPages]);

  return (
    <SectionCard
      title={`Sessions (${sessionCount})`}
      icon={Clock}
      extra={
        experimentCount > 0 ? (
          <span className="ml-1 flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-400">
            <FlaskConical className="h-2.5 w-2.5" />
            {experimentCount} experiments
          </span>
        ) : null
      }
    >
      {sortedRows.length === 0 ? (
        <p className="text-xs text-[var(--color-text-muted)]">{emptyMessage}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                <th className="px-2 py-1">#</th>
                {showKind ? <th className="px-2 py-1">Kind</th> : null}
                <th className="px-2 py-1">Status</th>
                <th className="px-2 py-1 text-right">Total PnL</th>
                <th className="px-2 py-1 text-right">Realized</th>
                <th className="px-2 py-1 text-right">Unrealized</th>
                <th className="px-2 py-1 text-right">Volume</th>
                <th className="px-2 py-1 text-right">Trades</th>
                <th className="px-2 py-1 text-right">Open</th>
                <th className="px-2 py-1 text-right">Actions</th>
                {showChevron ? <th className="w-6 px-2 py-1" /> : null}
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row) => {
                const pnlCol =
                  row.total_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
                const isExperiment = row.kind === "experiment";
                const isSelected = selectedSession === row.session_num;
                return (
                  <tr
                    key={row.id}
                    onClick={() => onRowClick?.(row)}
                    className={`border-t border-[var(--color-border)]/40 font-mono ${
                      onRowClick
                        ? "cursor-pointer transition-colors hover:bg-[var(--color-surface-hover)]"
                        : ""
                    } ${isSelected ? "bg-[var(--color-surface-hover)]" : ""}`}
                  >
                    <td className="px-2 py-1.5 text-[var(--color-text)]">{row.session_num}</td>
                    {showKind ? (
                      <td className="px-2 py-1.5">
                        {isExperiment ? (
                          <span className="inline-flex items-center gap-0.5 rounded bg-amber-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase text-amber-400">
                            <FlaskConical className="h-2.5 w-2.5" />
                            exp
                          </span>
                        ) : (
                          <span className="text-[var(--color-text-muted)]">
                            {row.kind ?? "session"}
                          </span>
                        )}
                      </td>
                    ) : null}
                    <td className="px-2 py-1.5">
                      {row.status ? <StatusBadge status={row.status} /> : "—"}
                    </td>
                    <td className={`px-2 py-1.5 text-right ${pnlCol}`}>
                      {formatCurrencyPnl(row.total_pnl)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">
                      {formatCurrencyPnl(row.realized_pnl)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">
                      {formatCurrencyPnl(row.unrealized_pnl)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">
                      {formatCurrencyVolume(row.volume)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">
                      {row.trade_count}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">
                      {row.open_count}
                    </td>
                    <td className="px-2 py-1.5 text-right">
                      {renderActions?.(row) ?? null}
                    </td>
                    {showChevron ? (
                      <td className="px-2 py-1.5 text-[var(--color-text-muted)]">
                        <ChevronRight className="h-3.5 w-3.5" />
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {sortedRows.length > SESSIONS_PAGE_SIZE ? (
        <div className="mt-3 flex items-center justify-between border-t border-[var(--color-border)]/40 pt-3">
          <span className="text-[10px] text-[var(--color-text-muted)]">
            {pageStart + 1}–{Math.min(pageStart + SESSIONS_PAGE_SIZE, sortedRows.length)} of{" "}
            {sortedRows.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((current) => Math.max(0, current - 1))}
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
              onClick={() => setPage((current) => Math.min(totalPages - 1, current + 1))}
              disabled={page >= totalPages - 1}
              className="rounded p-1 hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-30"
              aria-label="Next page"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      ) : null}
    </SectionCard>
  );
}
