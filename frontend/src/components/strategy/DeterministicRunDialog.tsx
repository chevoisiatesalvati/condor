import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, Save, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  BudgetFrequencyFields,
  RiskLimitsFields,
  ServerSelect,
  buildRiskLimitsPayload,
} from "@/components/agent/AgentSessionConfigFields";
import { StrategyParamsForm } from "@/components/agent/StrategyParamsForm";
import { StrategyPresetSelect } from "@/components/agent/StrategyPresetSelect";
import { DetailActionButton } from "@/components/strategy/DetailActionButton";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api, type DeterministicStrategySummary } from "@/lib/api";

function readRiskLimit(defaults: Record<string, unknown>, key: string, fallback: number) {
  const risk = (defaults.risk_limits || {}) as Record<string, unknown>;
  const value = risk[key];
  return value !== undefined && value !== null ? String(value) : String(fallback);
}

export function DeterministicRunDialog({
  open,
  onClose,
  slug,
  strategy,
  promoteInfo,
  server,
}: {
  open: boolean;
  onClose: () => void;
  slug: string;
  strategy: DeterministicStrategySummary;
  promoteInfo?: { promoted: boolean; manifest?: Record<string, unknown> };
  server: string | null;
}) {
  const queryClient = useQueryClient();
  useEscapeKey(open, onClose);

  const [notes, setNotes] = useState("");
  const [strategyPreset, setStrategyPreset] = useState("custom");
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>({});
  const [serverName, setServerName] = useState("");
  const [totalAmountQuote, setTotalAmountQuote] = useState("500");
  const [frequencySec, setFrequencySec] = useState("1800");
  const [maxOpenExecutors, setMaxOpenExecutors] = useState("10");
  const [maxDrawdown, setMaxDrawdown] = useState("-2");

  const presets = strategy.strategy_presets || [];
  const cfg = strategy.default_config || {};
  const freqNum = Math.max(10, Number(frequencySec) || 1800);

  const { data: schema } = useQuery({
    queryKey: ["deterministic-strategy-schema", slug, freqNum],
    queryFn: () => api.getDeterministicStrategyConfigSchema(slug, freqNum),
    enabled: open && Boolean(slug),
  });

  useEffect(() => {
    if (!open) return;
    const defaults = strategy.default_config || {};
    const manifestParams =
      (promoteInfo?.manifest?.strategy_params as Record<string, unknown> | undefined) || {};
    const defaultParams = (defaults.strategy_params as Record<string, unknown>) || {};
    const listedPresets = strategy.strategy_presets || [];
    const initialPreset =
      strategy.promoted_preset ||
      String(defaults.strategy_preset || "") ||
      (listedPresets[0]?.id ?? "custom");
    setStrategyPreset(initialPreset);
    setStrategyParams(
      Object.keys(manifestParams).length > 0 ? manifestParams : { ...defaultParams },
    );
    setServerName(String(server || defaults.server_name || "local"));
    setTotalAmountQuote(String(defaults.total_amount_quote ?? 500));
    setFrequencySec(String(defaults.frequency_sec ?? 1800));
    setMaxOpenExecutors(readRiskLimit(defaults, "max_open_executors", 10));
    setMaxDrawdown(readRiskLimit(defaults, "max_drawdown_pct", -2));
    setNotes("");
    // Hydrate only when the dialog opens so a 5s strategy refetch does not wipe edits.
  // eslint-disable-next-line react-hooks/exhaustive-deps -- open-only hydrate
  }, [open]);

  useEffect(() => {
    if (!open || !slug || strategyPreset === "custom") return;
    if (Object.keys(strategyParams).length > 0) return;
    let cancelled = false;
    void (async () => {
      try {
        const payload = await api.getDeterministicStrategyPresetParams(
          slug,
          strategyPreset,
          freqNum,
        );
        if (cancelled) return;
        setStrategyParams(payload.strategy_params || {});
        if (payload.risk_limits?.max_open_executors != null) {
          setMaxOpenExecutors(String(payload.risk_limits.max_open_executors));
        }
      } catch {
        /* leave empty */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, slug, strategyPreset, strategyParams, freqNum]);

  const promotedPreset = String(promoteInfo?.manifest?.preset || strategy.promoted_preset || "");
  const selectionMatchesPromote =
    Boolean(promoteInfo?.promoted) &&
    promotedPreset === strategyPreset &&
    strategyPreset !== "custom";

  const canPromote =
    strategyPreset !== "custom" &&
    strategyPreset.length > 0 &&
    Object.keys(strategyParams).length > 0;

  const isOrphaned = strategy.status === "orphaned";
  const canStart =
    strategy.status === "idle" &&
    (!strategy.require_promoted || selectionMatchesPromote);

  const startBlockedReason = useMemo(() => {
    if (strategy.status === "running" || strategy.status === "paused") {
      return "Stop the live session before starting a new one.";
    }
    if (isOrphaned) {
      return `Session ${strategy.session_num} looks orphaned after hot-reload. Resume it from the header instead of starting a new session.`;
    }
    if (!strategy.require_promoted) return "";
    if (!promoteInfo?.promoted) {
      return "Promote a named preset before live Start (require_promoted).";
    }
    if (promotedPreset !== strategyPreset) {
      return `Promoted preset is "${promotedPreset}". Select it (or re-promote the current selection) before Start.`;
    }
    if (strategyPreset === "custom") {
      return "Custom params cannot be started while require_promoted is on — pick a named preset and promote it.";
    }
    return "";
  }, [strategy, promoteInfo, promotedPreset, strategyPreset, isOrphaned]);

  const buildConfig = () => ({
    ...cfg,
    server_name: serverName || server || "local",
    total_amount_quote: Number(totalAmountQuote) || 500,
    frequency_sec: freqNum,
    risk_limits: buildRiskLimitsPayload(maxOpenExecutors, maxDrawdown),
    strategy_preset: strategyPreset,
    strategy_params: strategyParams,
  });

  const invalidateLifecycle = () => {
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategy", slug] });
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-sessions", slug] });
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-performance", slug] });
    queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-live-executors", slug] });
  };

  const startMutation = useMutation({
    mutationFn: () =>
      api.startDeterministicStrategy(slug, {
        config: buildConfig(),
        strategy_preset: strategyPreset,
        strategy_params: strategyParams,
      }),
    onSuccess: () => {
      invalidateLifecycle();
      onClose();
    },
  });

  const promoteMutation = useMutation({
    mutationFn: () =>
      api.promoteDeterministicStrategy(slug, {
        preset: strategyPreset,
        strategy_params: strategyParams,
        venue: strategy.connector || "hyperliquid_perpetual",
        notes,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategy", slug] });
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-promote", slug] });
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
    },
  });

  const saveDefaultsMutation = useMutation({
    mutationFn: () => api.saveDeterministicStrategyDefaults(slug, buildConfig()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategy", slug] });
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
    },
  });

  if (!open) return null;

  const actionErr =
    (startMutation.error as Error | null)?.message ||
    (promoteMutation.error as Error | null)?.message ||
    (saveDefaultsMutation.error as Error | null)?.message;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[var(--color-text)]">Run config</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            title="Close"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-5 text-sm">
          <ServerSelect value={serverName} onChange={setServerName} enabled={open} />
          <BudgetFrequencyFields
            executionMode="loop"
            totalAmountQuote={totalAmountQuote}
            frequencySec={frequencySec}
            onBudgetChange={setTotalAmountQuote}
            onFrequencyChange={setFrequencySec}
          />
          <RiskLimitsFields
            totalAmountQuote={totalAmountQuote}
            maxOpenExecutors={maxOpenExecutors}
            maxDrawdown={maxDrawdown}
            onMaxOpenExecutorsChange={setMaxOpenExecutors}
            onMaxDrawdownChange={setMaxDrawdown}
          />
          <StrategyPresetSelect
            slug={slug}
            sslug={slug}
            value={strategyPreset}
            presets={presets}
            frequencySec={freqNum}
            baseParams={strategyParams}
            fetchPresetParams={(preset, frequency) =>
              api.getDeterministicStrategyPresetParams(slug, preset, frequency)
            }
            onChange={(preset, params, risk) => {
              setStrategyPreset(preset);
              setStrategyParams(params);
              if (risk?.max_open_executors != null) {
                setMaxOpenExecutors(String(risk.max_open_executors));
              }
            }}
          />
          {promoteInfo?.promoted ? (
            <p className="text-emerald-400">
              Promoted <span className="font-mono">{promotedPreset}</span>
              {promoteInfo.manifest?.preset_hash
                ? ` · hash ${String(promoteInfo.manifest.preset_hash)}`
                : ""}
              {selectionMatchesPromote ? " · matches selection" : " · selection differs"}
            </p>
          ) : (
            <p className="text-amber-400">No promote manifest yet.</p>
          )}
          <textarea
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Optional notes (backtest id, commit, …)"
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm"
            rows={2}
          />
          <DetailActionButton
            disabled={!canPromote || promoteMutation.isPending}
            onClick={() => promoteMutation.mutate()}
          >
            {promoteMutation.isPending ? "Promoting…" : "Promote selected preset"}
          </DetailActionButton>
          {schema && Object.keys(schema.fields || {}).length > 0 ? (
            <StrategyParamsForm
              fields={schema.fields}
              groups={schema.groups || []}
              values={strategyParams}
              frequencySec={freqNum}
              defaultExpanded
              onChange={(key, value) => {
                setStrategyPreset("custom");
                setStrategyParams((prev) => ({ ...prev, [key]: value }));
              }}
            />
          ) : Object.keys(strategyParams).length > 0 ? (
            <pre className="max-h-48 overflow-auto font-mono text-xs text-[var(--color-text)]">
              {JSON.stringify(strategyParams, null, 2)}
            </pre>
          ) : (
            <p className="text-[var(--color-text-muted)]">Select a preset to load strategy params.</p>
          )}
        </div>

        {startBlockedReason ? (
          <p className="mt-4 text-sm text-amber-400">{startBlockedReason}</p>
        ) : null}
        {actionErr ? <p className="mt-3 text-xs text-red-400">{actionErr}</p> : null}
        {saveDefaultsMutation.isSuccess ? (
          <p className="mt-3 text-xs text-emerald-400">Defaults saved.</p>
        ) : null}

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <DetailActionButton
            disabled={saveDefaultsMutation.isPending}
            onClick={() => saveDefaultsMutation.mutate()}
          >
            {saveDefaultsMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            Save as defaults
          </DetailActionButton>
          <button
            type="button"
            disabled={!canStart || startMutation.isPending}
            title={startBlockedReason || undefined}
            onClick={() => startMutation.mutate()}
            className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-all hover:bg-emerald-500 disabled:opacity-40"
          >
            {startMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            {startMutation.isPending ? "Starting..." : "Start Session"}
          </button>
        </div>
      </div>
    </div>
  );
}
