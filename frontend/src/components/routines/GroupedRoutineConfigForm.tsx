import { useQuery } from "@tanstack/react-query";
import { Calendar, ChevronDown } from "lucide-react";
import { useState } from "react";

import type { RoutineFieldInfo } from "@/lib/api";
import { api } from "@/lib/api";
import { useServer } from "@/hooks/useServer";
import {
  addDaysToDateInput,
  dateInputToIso,
  isoToDateInput,
  todayDateInput,
} from "@/lib/replayDateUtils";
import { RoutineFieldInput } from "@/components/routines/RoutineFieldInput";

interface Props {
  fields: Record<string, RoutineFieldInfo>;
  groups: string[];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

const TIMELINE_RANGE_KEYS = new Set(["range_start_utc", "range_end_utc"]);

function fieldVisible(
  field: RoutineFieldInfo,
  values: Record<string, unknown>,
): boolean {
  if (field.hidden) return false;
  if (field.hidden_when) {
    const hidden = Object.entries(field.hidden_when).every(
      ([key, expected]) => values[key] === expected,
    );
    if (hidden) return false;
  }
  if (!field.visible_when) return true;
  return Object.entries(field.visible_when).every(
    ([key, expected]) => values[key] === expected,
  );
}

function TimelineDateRangePicker({
  values,
  onChange,
}: {
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  const { server } = useServer();
  const snapshotDir = String(values.snapshot_dir ?? "");
  const usesSnapshots = values.data_source === "snapshots";

  const { data: reportRange } = useQuery({
    queryKey: ["timeline-report-range", server],
    queryFn: () => api.getTimelineReportRange(server!),
    enabled: !!server,
    staleTime: 60_000,
  });

  const { data: snapshotRange, isLoading: snapshotRangeLoading } = useQuery({
    queryKey: ["timeline-snapshot-range", server, snapshotDir],
    queryFn: () => api.getTimelineSnapshotRange(server!, snapshotDir || undefined),
    enabled: !!server && usesSnapshots && !!snapshotDir,
    staleTime: 60_000,
  });

  const startDate = isoToDateInput(values.range_start_utc);
  const endDate = isoToDateInput(values.range_end_utc);

  const applyRange = (start: string | null, end: string | null) => {
    if (!start || !end) return;
    onChange("range_start_utc", start);
    onChange("range_end_utc", end);
  };

  const applyAllReports = () => {
    applyRange(reportRange?.start ?? null, reportRange?.end ?? null);
  };

  const applyAllSnapshots = () => {
    applyRange(snapshotRange?.start ?? null, snapshotRange?.end ?? null);
  };

  const applyLastDays = (days: number, endIso?: string | null) => {
    const end = endIso ? isoToDateInput(endIso) : todayDateInput();
    const start = addDaysToDateInput(end, -days);
    onChange("range_start_utc", dateInputToIso(start, false));
    onChange("range_end_utc", endIso ?? dateInputToIso(end, true));
  };

  const applyLastYears = (years: number) => {
    const anchorEnd = usesSnapshots
      ? snapshotRange?.end
      : reportRange?.end;
    applyLastDays(years * 365, anchorEnd);
  };

  return (
    <div className="mb-4 space-y-2 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-text-muted)]">
        <Calendar className="h-3.5 w-3.5" />
        Timeline range
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="date"
          value={startDate}
          onChange={(e) =>
            onChange("range_start_utc", dateInputToIso(e.target.value, false) || null)
          }
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
        />
        <span className="text-xs text-[var(--color-text-muted)]">to</span>
        <input
          type="date"
          value={endDate}
          onChange={(e) =>
            onChange("range_end_utc", dateInputToIso(e.target.value, true) || null)
          }
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
        />
      </div>
      <div className="flex flex-wrap gap-2">
        {usesSnapshots ? (
          <button
            type="button"
            onClick={applyAllSnapshots}
            disabled={snapshotRangeLoading || !snapshotDir || !snapshotRange?.start}
            title={
              !snapshotDir
                ? "Select a snapshot directory first"
                : snapshotRangeLoading
                  ? "Loading snapshot range…"
                  : !snapshotRange?.start
                    ? "No snapshot range available for this directory"
                    : undefined
            }
            className="rounded border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
          >
            All snapshots
          </button>
        ) : (
          <button
            type="button"
            onClick={applyAllReports}
            disabled={!reportRange?.start}
            className="rounded border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
          >
            All reports
          </button>
        )}
        <button
          type="button"
          onClick={() => applyLastYears(1)}
          className="rounded border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          Last 1y
        </button>
        <button
          type="button"
          onClick={() => applyLastYears(2)}
          className="rounded border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          Last 2y
        </button>
        <button
          type="button"
          onClick={() => applyLastYears(3)}
          className="rounded border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          Last 3y
        </button>
        <button
          type="button"
          onClick={() => applyLastDays(7)}
          className="rounded border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          Last 7d
        </button>
        <button
          type="button"
          onClick={() => applyLastDays(30)}
          className="rounded border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          Last 30d
        </button>
      </div>
    </div>
  );
}

export function GroupedRoutineConfigForm({
  fields,
  groups,
  values,
  onChange,
}: Props) {
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(
    () => Object.fromEntries(groups.map((group) => [group, true])),
  );

  const fieldsByGroup = groups.map((group) => ({
    group,
    entries: Object.entries(fields).filter(([, field]) => field.group === group),
  }));

  const toggleGroup = (group: string) => {
    setExpandedGroups((prev) => ({ ...prev, [group]: !prev[group] }));
  };

  return (
    <div className="space-y-4">
      {fieldsByGroup.map(({ group, entries }) => {
        const visibleEntries = entries.filter(([key, field]) => {
          if (TIMELINE_RANGE_KEYS.has(key)) return false;
          return fieldVisible(field, values);
        });
        if (visibleEntries.length === 0 && group !== "Timeline") return null;
        const showTimelinePicker =
          group === "Timeline" && values.replay_mode === "timeline_backtest";

        return (
          <div
            key={group}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]/50"
          >
            <button
              type="button"
              onClick={() => toggleGroup(group)}
              className="flex w-full items-center justify-between px-4 py-3 text-left"
            >
              <span className="text-sm font-semibold text-[var(--color-text)]">
                {group}
              </span>
              <ChevronDown
                className={`h-4 w-4 text-[var(--color-text-muted)] transition-transform ${
                  expandedGroups[group] ? "rotate-180" : ""
                }`}
              />
            </button>
            {expandedGroups[group] && (
              <div className="border-t border-[var(--color-border)] px-4 py-4">
                {showTimelinePicker && (
                  <TimelineDateRangePicker values={values} onChange={onChange} />
                )}
                {visibleEntries.length > 0 && (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {visibleEntries.map(([key, field]) => (
                      <div key={key}>
                        <label className="mb-1 block text-xs font-medium text-[var(--color-text-muted)]">
                          {field.description || key}
                        </label>
                        <RoutineFieldInput
                          fieldKey={key}
                          field={field}
                          value={values[key]}
                          configValues={values}
                          onChange={onChange}
                        />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
