import { api } from "@/lib/api";

/** Overlay preset-mapped params onto existing strategy_params (keeps scanner/queue defaults). */
export function mergePresetStrategyParams(
  base: Record<string, unknown>,
  presetParams: Record<string, unknown>,
): Record<string, unknown> {
  if (Object.keys(presetParams).length === 0) {
    return base;
  }
  return { ...base, ...presetParams };
}

export function StrategyPresetSelect({
  slug,
  sslug,
  value,
  presets,
  frequencySec,
  baseParams,
  onChange,
  label = "Strategy preset",
  description = "Apply a tuned parameter profile",
}: {
  slug: string;
  sslug: string;
  value: string;
  presets: Array<{ id: string; label: string }>;
  frequencySec: number;
  /** Current strategy_params; preset fields are merged on top (not replaced). */
  baseParams?: Record<string, unknown>;
  onChange: (
    preset: string,
    strategyParams: Record<string, unknown>,
    riskLimits?: Record<string, unknown>,
  ) => void;
  label?: string;
  description?: string;
}) {
  if (presets.length === 0) {
    return null;
  }

  const handleChange = async (nextPreset: string) => {
    const base = baseParams ?? {};
    if (nextPreset === "custom") {
      onChange(nextPreset, base);
      return;
    }
    try {
      const payload = await api.getStrategyPresetParams(slug, sslug, nextPreset, frequencySec);
      onChange(
        nextPreset,
        mergePresetStrategyParams(base, payload.strategy_params ?? {}),
        payload.risk_limits,
      );
    } catch {
      onChange(nextPreset, base);
    }
  };

  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => void handleChange(e.target.value)}
        className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
      >
        {presets.map((preset) => (
          <option key={preset.id} value={preset.id}>
            {preset.label}
          </option>
        ))}
      </select>
      <p className="mt-1 text-[10px] text-[var(--color-text-muted)]">
        {description}. Preset fields overlay current params; scanner/queue defaults are kept.
      </p>
    </div>
  );
}
