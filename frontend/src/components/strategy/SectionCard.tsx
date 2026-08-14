import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function SectionCard({
  title,
  icon: Icon,
  live = false,
  extra,
  children,
  className = "",
}: {
  title: ReactNode;
  icon?: LucideIcon;
  live?: boolean;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border bg-[var(--color-surface)] p-4 ${
        live ? "border-emerald-500/20" : "border-[var(--color-border)]"
      } ${className}`}
    >
      <h3
        className={`mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest ${
          live ? "text-emerald-400" : "text-[var(--color-text-muted)]"
        }`}
      >
        {Icon ? <Icon className="h-3.5 w-3.5" /> : null}
        {title}
        {extra}
      </h3>
      {children}
    </div>
  );
}
