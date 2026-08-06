import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Play, Save, Square } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  BudgetFrequencyFields,
  RiskLimitsFields,
  ServerSelect,
  buildRiskLimitsPayload,
} from "@/components/agent/AgentSessionConfigFields";
import { StrategyParamsForm } from "@/components/agent/StrategyParamsForm";
import { StrategyPresetSelect } from "@/components/agent/StrategyPresetSelect";
import { api } from "@/lib/api";
import { useServer } from "@/hooks/useServer";

function formatTs(ts?: number | null) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

export function StrategyRunnerDetail() {
  const { slug = "" } = useParams();
  const { server } = useServer();
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState("");
  const [initialized, setInitialized] = useState(false);
  const [selectedSession, setSelectedSession] = useState<number | null>(null);
  const [expandedTickId, setExpandedTickId] = useState<string | null>(null);

  const [strategyPreset, setStrategyPreset] = useState("custom");
  const [strategyParams, setStrategyParams] = useState<Record<string, unknown>>({});
  const [serverName, setServerName] = useState("");
  const [totalAmountQuote, setTotalAmountQuote] = useState("500");
  const [frequencySec, setFrequencySec] = useState("1800");
  const [maxOpenExecutors, setMaxOpenExecutors] = useState("10");
  const [maxDrawdown, setMaxDrawdown] = useState("-2");

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

  const { data: liveExecutors } = useQuery({
    queryKey: ["deterministic-strategy-live-executors", slug],
    queryFn: () => api.getDeterministicLiveExecutors(slug),
    enabled: Boolean(slug) && strategy?.status === "running",
    refetchInterval: 5_000,
  });

  const { data: journal } = useQuery({
    queryKey: ["deterministic-strategy-journal", slug, selectedSession],
    queryFn: () => api.getDeterministicSessionJournal(slug, selectedSession!),
    enabled: Boolean(slug) && selectedSession != null,
  });

  const { data: ticksInfo } = useQuery({
    queryKey: ["deterministic-strategy-ticks", slug, selectedSession],
    queryFn: () =>
      api.getDeterministicStrategyTicks(slug, {
        session: selectedSession ?? undefined,
        limit: 40,
      }),
    enabled: Boolean(slug),
    refetchInterval: 10_000,
  });

  const freqNum = Math.max(10, Number(frequencySec) || 1800);

  const { data: schema } = useQuery({
    queryKey: ["deterministic-strategy-schema", slug, freqNum],
    queryFn: () => api.getDeterministicStrategyConfigSchema(slug, freqNum),
    enabled: Boolean(slug),
  });

  const presets = strategy?.strategy_presets || [];

  useEffect(() => {
    if (!strategy || initialized) return;
    const cfg = strategy.default_config || {};
    const manifestParams =
      (promoteInfo?.manifest?.strategy_params as Record<string, unknown> | undefined) ||
      {};
    const defaultParams = (cfg.strategy_params as Record<string, unknown>) || {};
    const initialPreset =
      strategy.promoted_preset ||
      String(cfg.strategy_preset || "") ||
      (presets[0]?.id ?? "custom");
    setStrategyPreset(initialPreset);
    setStrategyParams(
      Object.keys(manifestParams).length > 0 ? manifestParams : { ...defaultParams },
    );
    setServerName(String(server || cfg.server_name || "local"));
    setTotalAmountQuote(String(cfg.total_amount_quote ?? 500));
    setFrequencySec(String(cfg.frequency_sec ?? 1800));
    const risk = (cfg.risk_limits as Record<string, unknown>) || {};
    setMaxOpenExecutors(String(risk.max_open_executors ?? 10));
    setMaxDrawdown(String(risk.max_drawdown_pct ?? -2));
    setInitialized(true);
  }, [strategy, promoteInfo, presets, server, initialized]);

  useEffect(() => {
    if (!initialized || !slug || strategyPreset === "custom") return;
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
  }, [initialized, slug, strategyPreset, strategyParams, freqNum]);

  const promotedPreset = String(promoteInfo?.manifest?.preset || strategy?.promoted_preset || "");
  const selectionMatchesPromote =
    Boolean(promoteInfo?.promoted) &&
    promotedPreset === strategyPreset &&
    strategyPreset !== "custom";

  const canPromote =
    strategyPreset !== "custom" &&
    strategyPreset.length > 0 &&
    Object.keys(strategyParams).length > 0;

  const canStart =
    strategy?.status !== "running" &&
    (!strategy?.require_promoted || selectionMatchesPromote);

  const startBlockedReason = useMemo(() => {
    if (!strategy?.require_promoted) return "";
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
  }, [strategy, promoteInfo, promotedPreset, strategyPreset]);

  const buildConfig = () => ({
    ...(strategy?.default_config || {}),
    server_name: serverName || server || "local",
    total_amount_quote: Number(totalAmountQuote) || 500,
    frequency_sec: freqNum,
    risk_limits: buildRiskLimitsPayload(maxOpenExecutors, maxDrawdown),
    strategy_preset: strategyPreset,
    strategy_params: strategyParams,
  });

  const startMutation = useMutation({
    mutationFn: () =>
      api.startDeterministicStrategy(slug, {
        config: buildConfig(),
        strategy_preset: strategyPreset,
        strategy_params: strategyParams,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategy", slug] });
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-sessions", slug] });
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-live-executors", slug] });
    },
  });

  const stopMutation = useMutation({
    mutationFn: () => api.stopDeterministicStrategy(slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategy", slug] });
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategies"] });
      queryClient.invalidateQueries({ queryKey: ["deterministic-strategy-live-executors", slug] });
    },
  });

  const promoteMutation = useMutation({
    mutationFn: () =>
      api.promoteDeterministicStrategy(slug, {
        preset: strategyPreset,
        strategy_params: strategyParams,
        venue: strategy?.connector || "hyperliquid_perpetual",
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

  if (isLoading || !initialized) {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading…
      </div>
    );
  }
  if (error || !strategy) {
    return <p className="text-sm text-red-400">{(error as Error)?.message || "Not found"}</p>;
  }

  const busy = startMutation.isPending || stopMutation.isPending;
  const actionErr =
    (startMutation.error as Error | null)?.message ||
    (stopMutation.error as Error | null)?.message ||
    (promoteMutation.error as Error | null)?.message ||
    (saveDefaultsMutation.error as Error | null)?.message;

  const openExecutors = (liveExecutors?.executors || []).filter((ex) => {
    const status = String(ex.status || "").toLowerCase();
    return status === "running" || status === "active" || status === "";
  });

  return (
    <div className="space-y-6">
      <Link
        to="/strategies"
        className="inline-flex items-center gap-1.5 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Strategies
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[var(--color-text)]">{strategy.name}</h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--color-text-muted)]">
            {strategy.description}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={saveDefaultsMutation.isPending}
            onClick={() => saveDefaultsMutation.mutate()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm"
          >
            {saveDefaultsMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            Save as defaults
          </button>
          {strategy.status === "running" ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => stopMutation.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
              Stop
            </button>
          ) : (
            <button
              type="button"
              disabled={busy || !canStart}
              title={startBlockedReason || undefined}
              onClick={() => startMutation.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-2 text-sm text-white disabled:opacity-50"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              Start
            </button>
          )}
        </div>
      </div>

      {actionErr ? <p className="text-sm text-red-400">{actionErr}</p> : null}
      {saveDefaultsMutation.isSuccess ? (
        <p className="text-sm text-emerald-400">Defaults saved.</p>
      ) : null}
      {startBlockedReason && strategy.status !== "running" ? (
        <p className="text-sm text-amber-400">{startBlockedReason}</p>
      ) : null}

      {/* Live status */}
      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm">
        <h2 className="mb-3 font-medium text-[var(--color-text)]">Live status</h2>
        <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 text-[var(--color-text-muted)]">
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
          <div className="flex justify-between gap-4 sm:block sm:col-span-2">
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
        {performance?.totals ? (
          <div className="mt-3 flex flex-wrap gap-3 text-xs text-[var(--color-text-muted)]">
            <span>
              PnL{" "}
              <span className="text-[var(--color-text)]">
                ${Number(performance.totals.total_pnl || 0).toFixed(2)}
              </span>
            </span>
            <span>
              Volume{" "}
              <span className="text-[var(--color-text)]">
                ${Number(performance.totals.volume || 0).toFixed(2)}
              </span>
            </span>
            <span>
              Open{" "}
              <span className="text-[var(--color-text)]">
                {Number(performance.totals.open_positions || 0)}
              </span>
            </span>
          </div>
        ) : null}
      </div>

      {strategy.status === "running" ? (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm">
          <h2 className="mb-3 font-medium text-[var(--color-text)]">
            Open executors ({openExecutors.length})
          </h2>
          {openExecutors.length === 0 ? (
            <p className="text-[var(--color-text-muted)]">No open executors yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-[var(--color-text-muted)]">
                  <tr>
                    <th className="py-1 pr-3">Pair</th>
                    <th className="py-1 pr-3">Side</th>
                    <th className="py-1 pr-3">PnL</th>
                    <th className="py-1 pr-3">Status</th>
                    <th className="py-1">Id</th>
                  </tr>
                </thead>
                <tbody>
                  {openExecutors.map((ex, idx) => (
                    <tr key={String(ex.id || idx)} className="border-t border-[var(--color-border)]">
                      <td className="py-1.5 pr-3 font-mono text-[var(--color-text)]">
                        {String(ex.pair || ex.trading_pair || "—")}
                      </td>
                      <td className="py-1.5 pr-3 text-[var(--color-text)]">
                        {String(ex.side || "—")}
                      </td>
                      <td className="py-1.5 pr-3 text-[var(--color-text)]">
                        ${Number(ex.pnl ?? ex.net_pnl_quote ?? 0).toFixed(2)}
                      </td>
                      <td className="py-1.5 pr-3 text-[var(--color-text)]">
                        {String(ex.status || "—")}
                      </td>
                      <td className="py-1.5 font-mono text-[var(--color-text-muted)]">
                        {String(ex.id || "—")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm">
          <h2 className="font-medium text-[var(--color-text)]">Run config</h2>
          <ServerSelect value={serverName} onChange={setServerName} />
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
        </div>

        <div className="space-y-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm">
          <h2 className="font-medium text-[var(--color-text)]">Preset & promote</h2>
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
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Optional notes (backtest id, commit, …)"
            className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm"
            rows={2}
          />
          <button
            type="button"
            disabled={!canPromote || promoteMutation.isPending}
            onClick={() => promoteMutation.mutate()}
            className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-sm hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
          >
            {promoteMutation.isPending ? "Promoting…" : "Promote selected preset"}
          </button>
        </div>
      </div>

      {schema && Object.keys(schema.fields || {}).length > 0 ? (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h2 className="mb-3 text-sm font-medium text-[var(--color-text)]">
            Strategy params
          </h2>
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
        </div>
      ) : (
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm text-[var(--color-text-muted)]">
          {Object.keys(strategyParams).length > 0 ? (
            <pre className="max-h-64 overflow-auto font-mono text-xs text-[var(--color-text)]">
              {JSON.stringify(strategyParams, null, 2)}
            </pre>
          ) : (
            "Select a preset to load strategy params."
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm">
          <h2 className="mb-3 font-medium text-[var(--color-text)]">Sessions</h2>
          {(sessionsInfo?.sessions || []).length === 0 ? (
            <p className="text-[var(--color-text-muted)]">No sessions yet.</p>
          ) : (
            <ul className="space-y-2">
              {(sessionsInfo?.sessions || []).slice(0, 20).map((session) => (
                <li key={session.session_num}>
                  <button
                    type="button"
                    onClick={() => setSelectedSession(session.session_num)}
                    className={`flex w-full justify-between gap-4 rounded-lg px-2 py-1.5 text-left font-mono text-xs hover:bg-[var(--color-bg)] ${
                      selectedSession === session.session_num
                        ? "bg-[var(--color-bg)] text-[var(--color-primary)]"
                        : "text-[var(--color-text-muted)]"
                    }`}
                  >
                    <span>
                      session_{session.session_num}
                      {session.status === "running" ? " · running" : ""}
                    </span>
                    <span>{new Date(session.mtime * 1000).toLocaleString()}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm">
          <h2 className="mb-3 font-medium text-[var(--color-text)]">
            Journal{selectedSession != null ? ` · session_${selectedSession}` : ""}
          </h2>
          {selectedSession == null ? (
            <p className="text-[var(--color-text-muted)]">Select a session to read its journal.</p>
          ) : journal?.content ? (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap font-mono text-xs text-[var(--color-text)]">
              {journal.content}
            </pre>
          ) : (
            <p className="text-[var(--color-text-muted)]">Empty journal.</p>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4 text-sm">
        <h2 className="mb-3 font-medium text-[var(--color-text)]">
          Recent ticks
          {selectedSession != null ? ` · session_${selectedSession}` : ""}
        </h2>
        {(ticksInfo?.ticks || []).length === 0 ? (
          <p className="text-[var(--color-text-muted)]">
            No tick audit logs yet (TTL’d JSON under data/strategy_runs).
          </p>
        ) : (
          <ul className="space-y-2">
            {(ticksInfo?.ticks || []).map((tick) => {
              const isOpen = expandedTickId === tick.id;
              return (
                <li
                  key={tick.id}
                  className="rounded-lg border border-[var(--color-border)] px-2 py-1.5"
                >
                  <button
                    type="button"
                    onClick={() =>
                      setExpandedTickId(isOpen ? null : tick.id)
                    }
                    className="flex w-full flex-wrap items-center justify-between gap-2 text-left font-mono text-xs text-[var(--color-text-muted)]"
                  >
                    <span>
                      s{tick.session}#t{tick.tick} · sig={tick.signal_count ?? "—"} ·
                      tradeable={tick.tradeable_count ?? "—"} ·{" "}
                      {tick.creates || tick.stops
                        ? `c${tick.creates ?? 0}/x${tick.stops ?? 0}`
                        : "hold"}{" "}
                      · apply={tick.apply_ok == null ? "—" : tick.apply_ok ? "ok" : "fail"}
                    </span>
                    <span>{tick.ts ? new Date(tick.ts).toLocaleString() : ""}</span>
                  </button>
                  {isOpen && tick.raw ? (
                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-[var(--color-text)]">
                      {JSON.stringify(tick.raw, null, 2)}
                    </pre>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
