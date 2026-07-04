import { api } from "@/lib/api";

export function StrategyPresetSelect({
  slug,
  sslug,
  value,
  presets,
  frequencySec,
  onChange,
  label = "Strategy preset",
  description = "Apply a tuned parameter profile",
}: {
  slug: string;
  sslug: string;
  value: string;
  presets: Array<{ id: string; label: string }>;
  frequencySec: number;
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
    if (nextPreset === "custom") {
      onChange(nextPreset, {});
      return;
    }
    try {
      const payload = await api.getStrategyPresetParams(slug, sslug, nextPreset, frequencySec);
      onChange(nextPreset, payload.strategy_params ?? {}, payload.risk_limits);
    } catch {
      onChange(nextPreset, {});
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
      <p className="mt-1 text-[10px] text-[var(--color-text-muted)]">{description}</p>
    </div>
  );
}
