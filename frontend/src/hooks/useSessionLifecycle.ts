import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

function invalidateAgentQueries(queryClient: ReturnType<typeof useQueryClient>, slug: string) {
  queryClient.invalidateQueries({ queryKey: ["agent", slug] });
  queryClient.invalidateQueries({ queryKey: ["agent-performance", slug] });
  queryClient.invalidateQueries({ queryKey: ["agents"] });
  queryClient.invalidateQueries({ queryKey: ["agent", slug, "session"] });
  queryClient.invalidateQueries({ queryKey: ["agent-session-executors", slug] });
}

export function useSessionLifecycle(slug: string) {
  const queryClient = useQueryClient();

  const onSuccess = () => invalidateAgentQueries(queryClient, slug);

  const resumeSessionMut = useMutation({
    mutationFn: (sessionNum: number) => api.startAgent(slug, {}, "", "", sessionNum),
    onSuccess,
  });

  const stopMut = useMutation({
    mutationFn: (agentId: string) => api.stopAgent(slug, agentId),
    onSuccess,
  });

  const pauseMut = useMutation({
    mutationFn: (agentId: string) => api.pauseAgent(slug, agentId),
    onSuccess,
  });

  const resumeMut = useMutation({
    mutationFn: (agentId: string) => api.resumeAgent(slug, agentId),
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
