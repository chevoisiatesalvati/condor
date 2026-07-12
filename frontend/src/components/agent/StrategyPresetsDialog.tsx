import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Layers, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useAuth } from "@/lib/auth";
import { api, type StrategyPresetSummary } from "@/lib/api";

type EditorMode = "list" | "create" | "edit";

function parseOverridesJson(raw: string): Record<string, unknown> {
  const parsed = JSON.parse(raw) as unknown;
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("Overrides must be a JSON object");
  }
  return parsed as Record<string, unknown>;
}

function PresetEditor({
  slug,
  mode,
  presetId,
  onDone,
  onCancel,
}: {
  slug: string;
  mode: "create" | "edit";
  presetId?: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const [id, setId] = useState(presetId ?? "");
  const [label, setLabel] = useState("");
  const [overridesJson, setOverridesJson] = useState("{\n  \n}");
  const [jsonError, setJsonError] = useState<string | null>(null);

  const { data: existing, isLoading } = useQuery({
    queryKey: ["strategy-preset", slug, presetId],
    queryFn: () => api.getStrategyPreset(slug, presetId!),
    enabled: mode === "edit" && !!presetId,
  });

  useEffect(() => {
    if (!existing) return;
    setLabel(existing.label);
    setOverridesJson(JSON.stringify(existing.overrides, null, 2));
  }, [existing]);

  const saveMut = useMutation({
    mutationFn: async () => {
      const overrides = parseOverridesJson(overridesJson);
      if (mode === "create") {
        return api.createStrategyPreset(slug, {
          id: id.trim(),
          label: label.trim() || id.trim(),
          overrides,
        });
      }
      return api.updateStrategyPreset(slug, presetId!, {
        label: label.trim() || presetId,
        overrides,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy-presets", slug] });
      queryClient.invalidateQueries({ queryKey: ["strategy", slug] });
      queryClient.invalidateQueries({ queryKey: ["strategy-defaults", slug] });
      onDone();
    },
    onError: (err: Error) => setJsonError(err.message),
  });

  const handleSave = () => {
    setJsonError(null);
    try {
      parseOverridesJson(overridesJson);
    } catch (err) {
      setJsonError(err instanceof Error ? err.message : "Invalid JSON");
      return;
    }
    saveMut.mutate();
  };

  if (mode === "edit" && isLoading) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-[var(--color-text-muted)]">
        Loading preset...
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {mode === "create" && (
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-[var(--color-text)]">Preset id</label>
          <input
            value={id}
            onChange={(e) => setId(e.target.value)}
            placeholder="hl_dynamic_timeline_my_preset"
            className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 font-mono text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
          />
          <p className="text-[10px] text-[var(--color-text-muted)]">
            Lowercase letters, digits, and underscores. Cannot be changed after creation.
          </p>
        </div>
      )}

      <div className="space-y-1.5">
        <label className="text-xs font-medium text-[var(--color-text)]">Label</label>
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="Human-readable name"
          className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        />
      </div>

      <div className="space-y-1.5">
        <label className="text-xs font-medium text-[var(--color-text)]">Overrides (JSON)</label>
        <textarea
          value={overridesJson}
          onChange={(e) => {
            setOverridesJson(e.target.value);
            if (jsonError) setJsonError(null);
          }}
          rows={14}
          spellCheck={false}
          className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 font-mono text-xs text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
        />
        <p className="text-[10px] text-[var(--color-text-muted)]">
          Replay/backtest config fields (sl_pct, replay_mode, etc.). Range and preset keys are stripped on save.
        </p>
      </div>

      {(jsonError || saveMut.isError) && (
        <p className="text-xs text-[var(--color-red)]">
          {jsonError || (saveMut.error as Error).message}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={saveMut.isPending || (mode === "create" && !id.trim())}
          onClick={handleSave}
          className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
        >
          {saveMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          Save
        </button>
      </div>
    </div>
  );
}

function PresetRow({
  preset,
  isAdmin,
  onEdit,
  onDelete,
}: {
  preset: StrategyPresetSummary;
  isAdmin: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-[var(--color-text)]">{preset.label}</span>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
              preset.source === "public"
                ? "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                : "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
            }`}
          >
            {preset.source}
          </span>
          {!preset.editable && (
            <span className="text-[10px] text-[var(--color-text-muted)]">read-only</span>
          )}
        </div>
        <p className="mt-0.5 truncate font-mono text-xs text-[var(--color-text-muted)]">{preset.id}</p>
        {preset.override_count > 0 && (
          <p className="mt-1 text-[10px] text-[var(--color-text-muted)]">
            {preset.override_count} override field{preset.override_count !== 1 ? "s" : ""}
          </p>
        )}
      </div>
      {isAdmin && preset.editable && (
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onEdit}
            className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-primary)]"
            title="Edit preset"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-red-500/10 hover:text-red-400"
            title="Delete preset"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

export function StrategyPresetsDialog({
  open,
  onClose,
  slug,
  sslug,
}: {
  open: boolean;
  onClose: () => void;
  slug: string;
  sslug: string;
}) {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  useEscapeKey(open, onClose);

  const [mode, setMode] = useState<EditorMode>("list");
  const [activePresetId, setActivePresetId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<StrategyPresetSummary | null>(null);
  const [filter, setFilter] = useState("");

  const { data: presets = [], isLoading } = useQuery({
    queryKey: ["strategy-presets", slug],
    queryFn: () => api.listStrategyPresets(slug),
    enabled: open,
  });

  const filteredPresets = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return presets;
    return presets.filter(
      (p) => p.id.toLowerCase().includes(q) || p.label.toLowerCase().includes(q),
    );
  }, [presets, filter]);

  const privatePresets = useMemo(
    () => presets.filter((p) => p.editable),
    [presets],
  );

  const deleteMut = useMutation({
    mutationFn: (presetId: string) => api.deleteStrategyPreset(slug, presetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["strategy-presets", slug] });
      queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
      queryClient.invalidateQueries({ queryKey: ["strategy-defaults", slug, sslug] });
      setDeleteTarget(null);
    },
  });

  useEffect(() => {
    if (!open) {
      setMode("list");
      setActivePresetId(null);
      setDeleteTarget(null);
      setFilter("");
    }
  }, [open]);

  if (!open) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      >
        <div
          className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-6 py-4">
            <div>
              <h2 className="flex items-center gap-2 text-lg font-semibold text-[var(--color-text)]">
                <Layers className="h-5 w-5" />
                Strategy Presets
              </h2>
              <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
                Manage private presets in presets.yaml. Built-in presets are read-only.
              </p>
            </div>
            <button
              onClick={onClose}
              className="text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              title="Close"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto px-6 py-4">
            {mode === "list" ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="Filter presets..."
                    className="min-w-[200px] flex-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                  />
                  {isAdmin && (
                    <button
                      type="button"
                      onClick={() => {
                        setMode("create");
                        setActivePresetId(null);
                      }}
                      className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      Add preset
                    </button>
                  )}
                </div>

                {!isAdmin && privatePresets.length > 0 && (
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Admin access is required to add, edit, or delete private presets.
                  </p>
                )}

                {isLoading ? (
                  <div className="flex h-32 items-center justify-center text-sm text-[var(--color-text-muted)]">
                    Loading presets...
                  </div>
                ) : filteredPresets.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-[var(--color-border)] px-4 py-8 text-center text-sm text-[var(--color-text-muted)]">
                    {presets.length === 0 ? "No presets available for this agent." : "No presets match your filter."}
                  </div>
                ) : (
                  <div className="space-y-2">
                    {filteredPresets.map((preset) => (
                      <PresetRow
                        key={preset.id}
                        preset={preset}
                        isAdmin={isAdmin}
                        onEdit={() => {
                          setActivePresetId(preset.id);
                          setMode("edit");
                        }}
                        onDelete={() => setDeleteTarget(preset)}
                      />
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <PresetEditor
                slug={slug}
                mode={mode}
                presetId={activePresetId ?? undefined}
                onDone={() => {
                  setMode("list");
                  setActivePresetId(null);
                }}
                onCancel={() => {
                  setMode("list");
                  setActivePresetId(null);
                }}
              />
            )}
          </div>
        </div>
      </div>

      {deleteTarget && (
        <ConfirmDialog
          open
          title="Delete preset"
          confirmLabel="Delete"
          isPending={deleteMut.isPending}
          isError={deleteMut.isError}
          errorText={(deleteMut.error as Error | undefined)?.message}
          onConfirm={() => deleteMut.mutate(deleteTarget.id)}
          onClose={() => setDeleteTarget(null)}
        >
          Delete <strong className="text-[var(--color-text)]">{deleteTarget.label}</strong>{" "}
          (<span className="font-mono">{deleteTarget.id}</span>)? This removes it from presets.yaml.
        </ConfirmDialog>
      )}
    </>
  );
}
