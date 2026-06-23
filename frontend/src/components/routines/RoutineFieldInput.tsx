import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { RoutineFieldInfo } from "@/lib/api";
import { api } from "@/lib/api";
import { useServer } from "@/hooks/useServer";
import {
  dateInputToIso,
  isoToDateInput,
} from "@/lib/replayDateUtils";
import { getDisabledSelectOptions } from "@/lib/routineUtils";

function SelectField({
  fieldKey,
  field,
  value,
  configValues,
  onChange,
}: {
  fieldKey: string;
  field: RoutineFieldInfo;
  value: unknown;
  configValues: Record<string, unknown>;
  onChange: (v: unknown) => void;
}) {
  const { server } = useServer();
  const staticOptions = field.options ?? [];
  const usesDynamicOptions = !!field.options_from;
  const { data, isLoading } = useQuery({
    queryKey: ["routine-field-options", field.options_from, server],
    queryFn: () => api.getRoutineFieldOptions(field.options_from!, server!),
    enabled: usesDynamicOptions && !!server,
    staleTime: 30_000,
  });

  const options = usesDynamicOptions ? (data?.options ?? []) : staticOptions;
  const allowsEmpty = field.nullable === true;
  const disabledOptions = useMemo(
    () => getDisabledSelectOptions(fieldKey, configValues),
    [fieldKey, configValues.replay_mode, configValues.data_source],
  );

  useEffect(() => {
    if (allowsEmpty) return;
    if (options.length === 0) return;
    const current = String(value ?? "");
    if (current && options.includes(current) && !disabledOptions.has(current)) {
      return;
    }
    const firstEnabled = options.find((opt) => !disabledOptions.has(opt));
    if (firstEnabled) onChange(firstEnabled);
  }, [allowsEmpty, options, value, onChange, disabledOptions]);

  return (
    <div className="relative">
      <select
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        className="w-full appearance-none rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 pr-8 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
      >
        {allowsEmpty && (
          <option value="">—</option>
        )}
        {usesDynamicOptions && isLoading && <option value="">Loading...</option>}
        {!isLoading && options.length === 0 && !allowsEmpty && (
          <option value="">No options available</option>
        )}
        {options.map((opt) => (
          <option
            key={opt}
            value={opt}
            disabled={disabledOptions.has(opt)}
            className={disabledOptions.has(opt) ? "text-[var(--color-text-muted)]" : undefined}
          >
            {field.option_labels?.[opt] ?? opt}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
    </div>
  );
}

function NumberField({
  fieldType,
  value,
  onChange,
}: {
  fieldType: string;
  value: unknown;
  onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value ?? ""));

  useEffect(() => {
    setDraft(String(value ?? ""));
  }, [value]);

  return (
    <input
      type="number"
      step={fieldType === "float" ? "any" : "1"}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const v = fieldType === "int" ? parseInt(draft, 10) : parseFloat(draft);
        if (!isNaN(v)) onChange(v);
        else setDraft(String(value ?? ""));
      }}
      className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
    />
  );
}

function DateField({
  value,
  endOfDay,
  onChange,
}: {
  value: unknown;
  endOfDay: boolean;
  onChange: (iso: string) => void;
}) {
  return (
    <input
      type="date"
      value={isoToDateInput(value)}
      onChange={(e) => onChange(dateInputToIso(e.target.value, endOfDay))}
      className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
    />
  );
}

export function RoutineFieldInput({
  fieldKey,
  field,
  value,
  configValues,
  onChange,
}: {
  fieldKey: string;
  field: RoutineFieldInfo;
  value: unknown;
  configValues?: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  const allValues = configValues ?? {};

  if (field.widget === "select" && (field.options_from || (field.options?.length ?? 0) > 0)) {
    return (
      <SelectField
        fieldKey={fieldKey}
        field={field}
        value={value}
        configValues={allValues}
        onChange={(v) => onChange(fieldKey, v)}
      />
    );
  }

  if (field.widget === "date") {
    const endOfDay = fieldKey === "range_end_utc";
    return (
      <DateField
        value={value}
        endOfDay={endOfDay}
        onChange={(iso) => onChange(fieldKey, iso || null)}
      />
    );
  }

  if (field.type === "bool") {
    return (
      <button
        type="button"
        onClick={() => onChange(fieldKey, !value)}
        className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
          value
            ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)]"
            : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
        }`}
      >
        {value ? "ON" : "OFF"}
      </button>
    );
  }

  if (field.type === "int" || field.type === "float") {
    return (
      <NumberField
        fieldType={field.type}
        value={value ?? field.default ?? ""}
        onChange={(v) => onChange(fieldKey, v)}
      />
    );
  }

  return (
    <input
      type="text"
      value={String(value ?? field.default ?? "")}
      onChange={(e) => onChange(fieldKey, e.target.value)}
      className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
    />
  );
}
