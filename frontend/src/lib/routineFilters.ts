/** Minimal fields required for source-type filtering. */
export interface RoutineListItem {
  name: string;
  source: string;
}

/**
 * Global routines named `{agentSlug}_*` (e.g. backtests) belong to that agent
 * but live under repo-root `routines/`, not `agents/{slug}/routines/`.
 */
export function routineMatchesAgentFilter<T extends RoutineListItem>(
  routine: T,
  agentSlug: string,
): boolean {
  if (routine.source === `agent:${agentSlug}`) return true;
  if (!routine.source.startsWith("agent:") && routine.name.startsWith(`${agentSlug}_`)) {
    return true;
  }
  return false;
}

export function filterRoutinesBySourceType<T extends RoutineListItem>(
  routines: T[],
  sourceTypeFilter: string,
): T[] {
  if (sourceTypeFilter === "all") return routines;
  if (sourceTypeFilter === "routine") {
    return routines.filter((r) => !r.source.startsWith("agent:"));
  }
  if (sourceTypeFilter === "agent") {
    return routines.filter((r) => r.source.startsWith("agent:"));
  }
  return routines.filter((r) => routineMatchesAgentFilter(r, sourceTypeFilter));
}
