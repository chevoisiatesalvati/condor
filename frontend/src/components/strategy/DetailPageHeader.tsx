import { AlertCircle, ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function MetaChip({
  children,
  mono = false,
}: {
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <span
      className={`rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 ${
        mono ? "font-mono" : ""
      }`}
    >
      {children}
    </span>
  );
}

export function DetailPageHeader({
  backHref,
  backLabel,
  parentLabel,
  title,
  description,
  meta,
  actions,
}: {
  backHref: string;
  backLabel: string;
  parentLabel?: string;
  title: string;
  description?: string;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-4">
      <Link
        to={backHref}
        className="mb-3 inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> {backLabel}
      </Link>

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-[var(--color-text)]">
            {parentLabel ? (
              <>
                <span className="text-[var(--color-text-muted)]">{parentLabel}</span>
                <span className="mx-1 text-[var(--color-text-muted)]">/</span>
              </>
            ) : null}
            {title}
          </h1>
          {description ? (
            <p className="mt-1 text-sm text-[var(--color-text-muted)]">{description}</p>
          ) : null}
        </div>
        {actions ? (
          <div className="flex flex-wrap items-center justify-end gap-2">{actions}</div>
        ) : null}
      </div>

      {meta ? (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
          {meta}
        </div>
      ) : null}
    </div>
  );
}

export function DetailLoading() {
  return (
    <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
    </div>
  );
}

export function DetailError({
  title,
  message,
  backHref,
  backLabel,
}: {
  title: string;
  message: string;
  backHref: string;
  backLabel: string;
}) {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="max-w-sm rounded-lg border border-red-500/30 bg-[var(--color-surface)] p-8 text-center">
        <AlertCircle className="mx-auto mb-3 h-10 w-10 text-[var(--color-red)]" />
        <h2 className="mb-1 text-lg font-semibold">{title}</h2>
        <p className="text-sm text-[var(--color-text-muted)]">{message}</p>
        <Link
          to={backHref}
          className="mt-4 inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> {backLabel}
        </Link>
      </div>
    </div>
  );
}
