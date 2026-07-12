import { useState } from "react";

import {
  EXECUTOR_CHART_DEFAULTS,
  EXECUTOR_CHART_INTERVALS,
  loadExecutorChartPrefs,
  saveExecutorChartPrefs,
  type ExecutorChartInterval,
  type ExecutorChartKind,
} from "@/lib/executorChartPrefs";

function IntervalSelector({
  value,
  onChange,
}: {
  value: ExecutorChartInterval;
  onChange: (interval: ExecutorChartInterval) => void;
}) {
  return (
    <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
      {EXECUTOR_CHART_INTERVALS.map((iv) => (
        <button
          key={iv}
          type="button"
          onClick={() => onChange(iv)}
          className={`px-3 py-1.5 text-sm ${
            value === iv
              ? "bg-[var(--color-primary)] text-white"
              : "bg-[var(--color-bg)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          }`}
        >
          {iv}
        </button>
      ))}
    </div>
  );
}

function PrefRow({
  label,
  description,
  kind,
  value,
  onChange,
}: {
  label: string;
  description: string;
  kind: ExecutorChartKind;
  value: ExecutorChartInterval;
  onChange: (kind: ExecutorChartKind, interval: ExecutorChartInterval) => void;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <div className="mb-3">
        <h3 className="text-sm font-medium text-[var(--color-text)]">{label}</h3>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">{description}</p>
      </div>
      <IntervalSelector value={value} onChange={(iv) => onChange(kind, iv)} />
    </div>
  );
}

export function DisplaySettings() {
  const [prefs, setPrefs] = useState(loadExecutorChartPrefs);

  const handleChange = (kind: ExecutorChartKind, interval: ExecutorChartInterval) => {
    const next = { ...prefs, [kind]: interval };
    setPrefs(next);
    saveExecutorChartPrefs(next);
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold text-[var(--color-text)]">Chart defaults</h2>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          Default candle interval when opening executor detail charts. You can still change the
          interval from the chart header; that updates the default for the matching executor type.
        </p>
      </div>

      <PrefRow
        label="Live executors"
        description={`Default: ${EXECUTOR_CHART_DEFAULTS.live}. Used for running and active executors.`}
        kind="live"
        value={prefs.live}
        onChange={handleChange}
      />

      <PrefRow
        label="Terminated executors"
        description={`Default: ${EXECUTOR_CHART_DEFAULTS.terminated}. Used for stopped and closed executors.`}
        kind="terminated"
        value={prefs.terminated}
        onChange={handleChange}
      />
    </div>
  );
}
