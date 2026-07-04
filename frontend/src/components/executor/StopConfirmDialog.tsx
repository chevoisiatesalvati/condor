import { useState, type FormEvent } from "react";

import { useEscapeKey } from "@/hooks/useEscapeKey";

export function StopConfirmDialog({
  ids,
  onConfirm,
  onCancel,
}: {
  ids: string[];
  onConfirm: (ids: string[], keepPosition: boolean) => void;
  onCancel: () => void;
}) {
  useEscapeKey(true, onCancel);
  const [keepPosition, setKeepPosition] = useState(false);
  const count = ids.length;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onConfirm(ids, keepPosition);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onCancel}>
      <div
        className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-xl p-6 w-full max-w-sm space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold">
          Stop {count === 1 ? "Executor" : `${count} Executors`}?
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          {count === 1
            ? "This will stop the executor."
            : `This will stop ${count} active executors.`}
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={keepPosition}
              onChange={(e) => setKeepPosition(e.target.checked)}
              className="h-4 w-4 rounded border-[var(--color-border)] accent-[var(--color-primary)]"
            />
            <span className="text-sm">Keep position open</span>
          </label>
          <p className="text-[10px] text-[var(--color-text-muted)] -mt-2 ml-6">
            {keepPosition
              ? "The executor will stop but the position will remain open on the exchange."
              : "The executor will stop and close any open position."}
          </p>

          <div className="flex items-center gap-2 justify-end">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="rounded-md bg-[var(--color-red)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 transition-colors"
            >
              Confirm Stop
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
