import { Zap } from "lucide-react";
import { useMemo } from "react";

import { AgentPnlChart, sessionsToDataPoints } from "@/components/agent/AgentPnlChart";
import { SectionCard } from "@/components/strategy/SectionCard";
import { formatCurrency, formatCurrencyPnl, formatCurrencyVolume } from "@/lib/formatters";

export interface PerformanceTotals {
  total_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  volume: number;
  open_positions: number;
}

export interface PerformanceExtendedMetrics {
  fees: number;
  win_rate_pct: number;
  trades: number;
}

export function PerformanceStats({
  totals,
  extended,
  chartSessions,
}: {
  totals: PerformanceTotals;
  extended?: PerformanceExtendedMetrics;
  chartSessions?: { session_num: number; total_pnl: number; status: string }[];
}) {
  const pnlColor =
    totals.total_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  const gridCols = extended ? "lg:grid-cols-8" : "lg:grid-cols-5";
  const pnlData = useMemo(
    () => (chartSessions ? sessionsToDataPoints(chartSessions) : []),
    [chartSessions],
  );

  return (
    <div className="space-y-4">
      <SectionCard title="Performance" icon={Zap}>
        <div className={`grid grid-cols-2 gap-4 sm:grid-cols-4 ${gridCols}`}>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              Total PnL
            </span>
            <span className={`font-mono text-lg font-semibold ${pnlColor}`}>
              {formatCurrencyPnl(totals.total_pnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              Realized
            </span>
            <span className="font-mono text-lg text-[var(--color-text)]">
              {formatCurrencyPnl(totals.realized_pnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              Unrealized
            </span>
            <span className="font-mono text-lg text-[var(--color-text)]">
              {formatCurrencyPnl(totals.unrealized_pnl)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              Volume
            </span>
            <span className="font-mono text-lg text-[var(--color-text)]">
              {formatCurrencyVolume(totals.volume)}
            </span>
          </div>
          {extended ? (
            <>
              <div>
                <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                  Fees
                </span>
                <span className="font-mono text-lg text-[var(--color-text)]">
                  {formatCurrency(extended.fees)}
                </span>
              </div>
              <div>
                <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                  Win Rate
                </span>
                <span className="font-mono text-lg text-[var(--color-text)]">
                  {extended.win_rate_pct.toFixed(0)}%
                </span>
              </div>
              <div>
                <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                  Trades
                </span>
                <span className="font-mono text-lg text-[var(--color-text)]">
                  {extended.trades}
                </span>
              </div>
            </>
          ) : null}
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              Open
            </span>
            <span className="font-mono text-lg text-[var(--color-text)]">
              {totals.open_positions}
            </span>
          </div>
        </div>
      </SectionCard>

      {pnlData.length > 1 ? (
        <AgentPnlChart data={pnlData} height={180} title="PnL Equity Curve" />
      ) : null}
    </div>
  );
}
