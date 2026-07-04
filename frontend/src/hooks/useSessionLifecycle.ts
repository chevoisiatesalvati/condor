import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

function invalidateStrategyQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  slug: string,
  sslug: string,
) {
  queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug] });
  queryClient.invalidateQueries({ queryKey: ["strategy-performance", slug, sslug] });
  queryClient.invalidateQueries({ queryKey: ["agents"] });
  queryClient.invalidateQueries({ queryKey: ["strategy", slug, sslug, "session"] });
  queryClient.invalidateQueries({ queryKey: ["strategy-session-executors", slug, sslug] });
}

export function useSessionLifecycle(slug: string, sslug: string) {
  const queryClient = useQueryClient();

  const onSuccess = () => invalidateStrategyQueries(queryClient, slug, sslug);

  const resumeSessionMut = useMutation({
    mutationFn: (sessionNum: number) => api.startStrategy(slug, sslug, {}, "", "", sessionNum),
    onSuccess,
  });

  const stopMut = useMutation({
    mutationFn: (agentId: string) => api.stopStrategy(slug, sslug, agentId),
    onSuccess,
  });

  const pauseMut = useMutation({
    mutationFn: (agentId: string) => api.pauseStrategy(slug, sslug, agentId),
    onSuccess,
  });

  const resumeMut = useMutation({
    mutationFn: (agentId: string) => api.resumeStrategy(slug, sslug, agentId),
    onSuccess,
  });

  const loading =
    resumeSessionMut.isPending || stopMut.isPending || pauseMut.isPending || resumeMut.isPending;

  const error = resumeSessionMut.error || stopMut.error || pauseMut.error || resumeMut.error;

  return {
    resumeSessionMut,
    stopMut,
    pauseMut,
    resumeMut,
    loading,
    error: error instanceof Error ? error.message : error ? String(error) : null,
  };
}
