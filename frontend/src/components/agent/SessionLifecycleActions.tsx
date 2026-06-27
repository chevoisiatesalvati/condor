import { Pause, Play, Square } from "lucide-react";
import { useState, type MouseEvent } from "react";

import { useSessionLifecycle } from "@/hooks/useSessionLifecycle";
import { type RunningInstance } from "@/lib/api";

export function ResumeSessionButton({
  slug,
  sessionNum,
  disabled = false,
  size = "sm",
}: {
  slug: string;
  sessionNum: number;
  disabled?: boolean;
  size?: "sm" | "md";
}) {
  const { resumeSessionMut, loading, error } = useSessionLifecycle(slug);

  const sizeCls = size === "md" ? "gap-1.5 px-3 py-1.5 text-xs" : "gap-1 px-2 py-1 text-[10px]";

  return (
    <div className="inline-flex flex-col items-end gap-0.5">
      <button
        type="button"
        disabled={disabled || loading}
        onClick={(e) => {
          e.stopPropagation();
          resumeSessionMut.mutate(sessionNum);
        }}
        className={`inline-flex items-center rounded-lg bg-emerald-600 font-semibold text-white transition-all hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40 ${sizeCls}`}
        title={`Resume session ${sessionNum}`}
      >
        <Play className={size === "md" ? "h-3.5 w-3.5" : "h-3 w-3"} />
        Resume session
      </button>
      {error && (
        <span className="max-w-[12rem] truncate text-[9px] text-red-400" title={error}>
          {error}
        </span>
      )}
    </div>
  );
}

export function InstanceLifecycleButtons({
  slug,
  instance,
  size = "sm",
}: {
  slug: string;
  instance: RunningInstance;
  size?: "sm" | "md";
}) {
  const { stopMut, pauseMut, resumeMut, loading, error } = useSessionLifecycle(slug);
  const [confirmStop, setConfirmStop] = useState(false);

  const sizeCls = size === "md" ? "gap-1.5 px-3 py-1.5 text-xs" : "gap-1 px-2 py-1 text-[10px]";

  const iconSize = size === "md" ? "h-3.5 w-3.5" : "h-3 w-3";

  const handleStop = (e: MouseEvent) => {
    e.stopPropagation();
    if (!confirmStop) {
      setConfirmStop(true);
      return;
    }
    stopMut.mutate(instance.agent_id);
    setConfirmStop(false);
  };

  return (
    <div className="inline-flex flex-col items-end gap-0.5">
      <div className="flex items-center gap-1">
        {instance.status === "running" && (
          <button
            type="button"
            disabled={loading}
            onClick={(e) => {
              e.stopPropagation();
              pauseMut.mutate(instance.agent_id);
            }}
            className={`inline-flex items-center rounded-lg border border-amber-500/30 bg-amber-500/10 font-semibold text-amber-400 transition-all hover:bg-amber-500/20 disabled:opacity-40 ${sizeCls}`}
          >
            <Pause className={iconSize} />
            Pause
          </button>
        )}
        {instance.status === "paused" && (
          <button
            type="button"
            disabled={loading}
            onClick={(e) => {
              e.stopPropagation();
              resumeMut.mutate(instance.agent_id);
            }}
            className={`inline-flex items-center rounded-lg bg-emerald-600 font-semibold text-white transition-all hover:bg-emerald-500 disabled:opacity-40 ${sizeCls}`}
          >
            <Play className={iconSize} />
            Resume
          </button>
        )}
        <button
          type="button"
          disabled={loading}
          onClick={handleStop}
          onBlur={() => setConfirmStop(false)}
          className={`inline-flex items-center rounded-lg border font-semibold transition-all disabled:opacity-40 ${sizeCls} ${
            confirmStop
              ? "border-red-500 bg-red-500/20 text-red-300"
              : "border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20"
          }`}
        >
          <Square className={iconSize} />
          {confirmStop ? "Confirm" : "Stop"}
        </button>
      </div>
      {error && (
        <span className="max-w-[12rem] truncate text-[9px] text-red-400" title={error}>
          {error}
        </span>
      )}
    </div>
  );
}
