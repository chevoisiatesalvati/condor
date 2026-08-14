import type { ButtonHTMLAttributes, ReactNode } from "react";

const VARIANT_CLASS = {
  default:
    "border border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]",
  danger:
    "border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20",
  primary:
    "border border-transparent bg-[var(--color-primary)] text-white hover:opacity-90",
  success:
    "border border-transparent bg-emerald-600 text-white hover:bg-emerald-500",
  info: "border border-transparent bg-sky-600 text-white hover:bg-sky-500",
} as const;

export function DetailActionButton({
  variant = "default",
  children,
  className = "",
  type = "button",
  ...props
}: {
  variant?: keyof typeof VARIANT_CLASS;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type={type}
      className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all disabled:cursor-not-allowed disabled:opacity-30 ${VARIANT_CLASS[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
