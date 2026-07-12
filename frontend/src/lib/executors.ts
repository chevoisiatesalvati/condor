import type { ExecutorInfo } from "@/lib/api";

export type ExecutorPage = { executors: unknown[]; next_cursor: string | null };

/** First occurrence wins — earlier pages (WS-patched page 0) take precedence. */
export function dedupeExecutorsById(executors: ExecutorInfo[]): ExecutorInfo[] {
  const seen = new Set<string>();
  const result: ExecutorInfo[] = [];
  for (const ex of executors) {
    if (!ex.id || seen.has(ex.id)) continue;
    seen.add(ex.id);
    result.push(ex);
  }
  return result;
}

export function executorId(ex: unknown): string {
  if (ex && typeof ex === "object" && "id" in ex) {
    const id = (ex as { id?: unknown }).id;
    return typeof id === "string" ? id : "";
  }
  return "";
}

/** Shallow-clone list items so React Query always sees a new reference on WS ticks. */
export function cloneExecutorList<T>(executors: T[]): T[] {
  return executors.map((ex) =>
    ex && typeof ex === "object" ? ({ ...ex } as T) : ex,
  );
}

/**
 * Patch every cached page with live WS fields by executor ID.
 * Replacing only page 0 misses executors that live on later REST pages.
 */
export function mergeExecutorPagesWithWs(
  pages: ExecutorPage[],
  wsExecs: unknown[],
): ExecutorPage[] {
  const wsById = new Map<string, unknown>();
  for (const ex of wsExecs) {
    const id = executorId(ex);
    if (id) wsById.set(id, ex);
  }

  const seenInPages = new Set<string>();
  const mergedPages = pages.map((page) => ({
    ...page,
    executors: page.executors.map((ex) => {
      const id = executorId(ex);
      if (id) seenInPages.add(id);
      const live = id ? wsById.get(id) : undefined;
      return live ?? ex;
    }),
  }));

  const newOnes = wsExecs.filter((ex) => {
    const id = executorId(ex);
    return id && !seenInPages.has(id);
  });
  if (newOnes.length > 0 && mergedPages.length > 0) {
    mergedPages[0] = {
      ...mergedPages[0],
      executors: [...newOnes, ...mergedPages[0].executors],
    };
  }

  return mergedPages;
}
