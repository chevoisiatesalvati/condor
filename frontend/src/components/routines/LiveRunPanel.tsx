import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { type RoutineInstance, type RoutineProgress, api } from "@/lib/api";

interface LiveRunPanelProps {
  instanceId: string | null;
  logPath?: string | null;
  /** Fallback progress from instance detail while logs catch up. */
  progressHint?: RoutineProgress | null;
  statusHint?: string | null;
}

function phaseLabel(phase: string | undefined): string {
  if (!phase) return "Waiting…";
  return phase.replace(/_/g, " ");
}

export function LiveRunPanel({
  instanceId,
  logPath,
  progressHint,
  statusHint,
}: LiveRunPanelProps) {
  const [expanded, setExpanded] = useState(true);
  const [lines, setLines] = useState<string[]>([]);
  const [progress, setProgress] = useState<RoutineProgress | null>(null);
  const [complete, setComplete] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    if (!instanceId) return;

    let cancelled = false;
    let offset = 0;
    let finished = statusHint != null
      && statusHint !== "running"
      && statusHint !== "queued";
    setLines([]);
    setProgress(null);
    setComplete(finished);
    setExpanded(true);
    stickToBottomRef.current = true;

    const poll = async () => {
      try {
        const data = await api.getRoutineInstanceLogs(instanceId, {
          offset,
          tail: offset === 0 ? 200 : 500,
        });
        if (cancelled) return;
        if (data.progress) setProgress(data.progress);
        if (data.complete) {
          finished = true;
          setComplete(true);
        }
        if (data.lines.length > 0) {
          setLines((prev) => {
            if (offset === 0) return data.lines;
            const merged = prev.concat(data.lines);
            return merged.length > 800 ? merged.slice(-800) : merged;
          });
        }
        if (data.next_offset >= offset) {
          offset = data.next_offset;
        }
      } catch {
        // Keep last good snapshot; next poll retries.
      }
    };

    void poll();
    const timer = window.setInterval(() => {
      if (!finished) void poll();
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [instanceId, statusHint]);

  useEffect(() => {
    if (stickToBottomRef.current) {
      logEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [lines]);

  const displayProgress = progress ?? progressHint ?? null;
  const percent =
    displayProgress?.percent != null && Number.isFinite(displayProgress.percent)
      ? Math.max(0, Math.min(100, displayProgress.percent))
      : null;

  if (!instanceId) return null;

  return (
    <div className="mx-4 mt-2 shrink-0 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-hover)]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {!complete && (
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--color-primary)]" />
            )}
            <span className="text-xs font-semibold text-[var(--color-text)]">
              Live run
            </span>
            <span className="truncate text-[10px] capitalize text-[var(--color-text-muted)]">
              {phaseLabel(displayProgress?.phase)}
              {displayProgress?.message ? ` · ${displayProgress.message}` : ""}
            </span>
            {percent != null && (
              <span className="shrink-0 text-[10px] tabular-nums text-[var(--color-text-muted)]">
                {percent.toFixed(0)}%
              </span>
            )}
          </div>
          {percent != null && (
            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-surface)]">
              <div
                className="h-full rounded-full bg-[var(--color-primary)] transition-[width] duration-500"
                style={{ width: `${percent}%` }}
              />
            </div>
          )}
        </div>
        {expanded ? (
          <ChevronUp className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
        )}
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)]/60 px-3 pb-3 pt-2">
          <div
            className="max-h-48 overflow-y-auto rounded bg-[var(--color-surface)] px-2.5 py-2 font-mono text-[10px] leading-relaxed text-[var(--color-text-muted)]"
            onScroll={(e) => {
              const el = e.currentTarget;
              stickToBottomRef.current =
                el.scrollHeight - el.scrollTop - el.clientHeight < 40;
            }}
          >
            {lines.length === 0 ? (
              <span className="italic opacity-60">Waiting for worker output…</span>
            ) : (
              lines.map((line, idx) => (
                <div key={`${idx}-${line.slice(0, 24)}`} className="whitespace-pre-wrap break-all">
                  {line}
                </div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
          {logPath && (
            <p className="mt-1.5 truncate font-mono text-[9px] text-[var(--color-text-muted)]/80">
              {logPath}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Pick a live instance from a list, preferring ``preferredId`` when present. */
export function liveInstanceFromList(
  instances: RoutineInstance[],
  preferredId?: string | null,
): RoutineInstance | null {
  if (preferredId) {
    const preferred = instances.find((i) => i.instance_id === preferredId);
    if (preferred) return preferred;
  }
  return (
    instances.find((i) => i.status === "running" || i.status === "queued") ?? null
  );
}
