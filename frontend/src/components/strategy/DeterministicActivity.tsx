import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/lib/api";
import {
  formatDeterministicTick,
  parseJournalKvChips,
  type DeterministicTickSummary,
} from "@/lib/formatDeterministicTick";
import { formatCurrencyPnl } from "@/lib/formatters";
import { parseJournal } from "@/lib/parse-agent";

export function DeterministicActivity({
  slug,
  sessionNum,
}: {
  slug: string;
  sessionNum: number;
}) {
  const [expandedTickId, setExpandedTickId] = useState<string | null>(null);

  const { data: ticksInfo } = useQuery({
    queryKey: ["deterministic-strategy-ticks", slug, sessionNum],
    queryFn: () =>
      api.getDeterministicStrategyTicks(slug, { session: sessionNum, limit: 80 }),
    enabled: sessionNum > 0,
    refetchInterval: 10_000,
  });

  const { data: journal } = useQuery({
    queryKey: ["deterministic-strategy-journal", slug, sessionNum],
    queryFn: () => api.getDeterministicSessionJournal(slug, sessionNum),
    enabled: sessionNum > 0,
  });

  const ticks = (ticksInfo?.ticks || []) as DeterministicTickSummary[];
  const parsed = journal?.content ? parseJournal(journal.content) : null;
  const decisions = parsed?.decisions ?? [];

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          Ticks ({ticks.length})
        </h3>
        {ticks.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">
            No tick audit logs yet for this session.
          </p>
        ) : (
          <ul className="space-y-2">
            {ticks.map((tick) => {
              const formatted = formatDeterministicTick(tick);
              const isOpen = expandedTickId === tick.id;
              return (
                <li
                  key={tick.id}
                  className={`rounded-lg border p-3 ${
                    formatted.isError
                      ? "border-red-500/40 bg-red-500/5"
                      : "border-[var(--color-border)] bg-[var(--color-surface)]"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setExpandedTickId(isOpen ? null : tick.id)}
                    className="flex w-full items-start gap-3 text-left"
                  >
                    <span
                      className={`mt-0.5 shrink-0 rounded-md px-2 py-0.5 font-mono text-xs font-bold ${
                        formatted.isError
                          ? "bg-red-500/10 text-red-400"
                          : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                      }`}
                    >
                      #{tick.tick ?? "—"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        {tick.ts ? (
                          <span className="text-xs text-[var(--color-text-muted)]">
                            {new Date(tick.ts).toLocaleString()}
                          </span>
                        ) : null}
                        <span
                          className={`text-sm font-medium ${
                            formatted.isError ? "text-red-400" : "text-[var(--color-text)]"
                          }`}
                        >
                          {formatted.title}
                        </span>
                      </div>
                      {formatted.pairs.length > 0 ? (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {formatted.pairs.map((pair) => (
                            <span
                              key={pair}
                              className="rounded bg-[var(--color-bg)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text)]"
                            >
                              {pair}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      {formatted.closes.length > 0 ? (
                        <div className="mt-1.5 space-y-0.5">
                          {formatted.closes.map((closeEvent) => (
                            <p
                              key={`${closeEvent.pair}-${closeEvent.closeType}`}
                              className="text-xs text-[var(--color-text-muted)]"
                            >
                              Closed {closeEvent.pair} ({closeEvent.closeType})
                              {closeEvent.pnl != null
                                ? ` · ${formatCurrencyPnl(closeEvent.pnl)}`
                                : ""}
                            </p>
                          ))}
                        </div>
                      ) : null}
                      {formatted.meta.length > 0 ? (
                        <p className="mt-1 text-[10px] text-[var(--color-text-muted)]">
                          {formatted.meta.join(" · ")}
                        </p>
                      ) : null}
                    </div>
                  </button>
                  {isOpen && tick.raw ? (
                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-[var(--color-text-muted)]">
                      {JSON.stringify(tick.raw, null, 2)}
                    </pre>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section>
        <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          Journal ({decisions.length})
        </h3>
        {decisions.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No journal decisions yet.</p>
        ) : (
          <div className="space-y-2">
            {decisions
              .slice()
              .reverse()
              .map((decision, index) => {
                const chips = parseJournalKvChips(decision.reasoning);
                return (
                  <div
                    key={`${decision.tick}-${index}`}
                    className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
                  >
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 shrink-0 rounded-md bg-[var(--color-surface-hover)] px-2 py-0.5 font-mono text-xs font-bold text-[var(--color-text-muted)]">
                        #{decision.tick}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs text-[var(--color-text-muted)]">
                            {decision.time}
                          </span>
                          <span className="text-sm font-medium text-[var(--color-text)]">
                            {decision.action === "deterministic_tick"
                              ? "Tick"
                              : decision.action}
                          </span>
                        </div>
                        {chips.length > 0 ? (
                          <div className="mt-1.5 flex flex-wrap gap-1">
                            {chips.map((chip) => (
                              <span
                                key={`${chip.key}-${chip.value}`}
                                className="rounded bg-[var(--color-bg)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--color-text-muted)]"
                              >
                                {chip.key}: {chip.value}
                              </span>
                            ))}
                          </div>
                        ) : decision.reasoning ? (
                          <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-muted)]">
                            {decision.reasoning}
                          </p>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              })}
          </div>
        )}
      </section>
    </div>
  );
}
