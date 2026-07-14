import type { QueryClient } from "@tanstack/react-query";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/lib/auth";
import type {
  BotsPageResponse,
  ControllerInfo,
  ControllerPerformanceHistoryResponse,
  ControllerPerformanceSnapshot,
} from "@/lib/api";
import { candleStore } from "@/lib/candle-store";
import { cloneExecutorList, dedupeExecutorsById, mergeExecutorPagesWithWs } from "@/lib/executors";
import { CondorWebSocket } from "@/lib/websocket";

/**
 * Filter out candle channels — those are managed exclusively by candleStore.
 */
function nonCandleChannels(channels: string[]): string[] {
  return channels.filter((ch) => !ch.startsWith("candles:"));
}

/**
 * Merge incoming performance snapshots into existing ones, deduplicating by
 * `controller_id:timestamp`.
 */
function mergeSnapshots(
  existing: ControllerPerformanceSnapshot[],
  incoming: ControllerPerformanceSnapshot[],
): ControllerPerformanceSnapshot[] {
  const merged = [...existing];
  const seen = new Set(existing.map((s) => `${s.controller_id}:${s.timestamp}`));
  for (const snap of incoming) {
    const key = `${snap.controller_id}:${snap.timestamp}`;
    if (!seen.has(key)) {
      merged.push(snap);
      seen.add(key);
    }
  }
  return merged;
}

const lastExecutorFingerprintByServer = new Map<string, string>();

/** Cheap fingerprint to skip clone+setQueryData when WS payload is unchanged. */
function fingerprintExecutorPayload(raw: unknown): string {
  if (!Array.isArray(raw)) return "";
  let h = raw.length;
  for (const item of raw) {
    const ex = item as {
      id?: string;
      net_pnl?: number;
      status?: string;
      current_price?: number;
    };
    const id = ex.id ?? "";
    h = (h * 31 + id.length) | 0;
    for (let i = 0; i < Math.min(id.length, 8); i++) {
      h = (h * 31 + id.charCodeAt(i)) | 0;
    }
    h = (h * 31 + Math.round((ex.net_pnl ?? 0) * 100)) | 0;
    h = (h * 31 + (ex.status ?? "").length) | 0;
    h = (h * 31 + Math.round((ex.current_price ?? 0) * 100)) | 0;
  }
  return String(h);
}

function handleWsMessage(
  channel: string,
  data: unknown,
  server: string,
  queryClient: QueryClient,
): void {
  const prefix = channel.split(":")[0];

  if (prefix === "candles") {
    const parts = channel.split(":");
    if (parts.length >= 5) {
      const [, srv, conn, pr, iv] = parts;
      const payload = data as { type: string; message?: string };
      if (payload.type === "error") {
        queryClient.setQueryData(
          ["candles-status", srv, conn, pr, iv],
          { status: "error", message: payload.message ?? "Unknown error" },
        );
      } else if (payload.type === "candle_update" || payload.type === "candles") {
        queryClient.setQueryData(
          ["candles-status", srv, conn, pr, iv],
          { status: "connected" },
        );
      }
    }
    return;
  }

  if (prefix === "portfolio") {
    queryClient.setQueryData(["portfolio", server], data);
  } else if (prefix === "bots") {
    queryClient.setQueryData(["bots", server], (old: BotsPageResponse | undefined) => {
      const incoming = data as BotsPageResponse;
      if (!incoming?.controllers) return old ?? data;
      if (!old?.controllers?.length) return incoming;

      const oldMap = new Map<string, ControllerInfo>();
      for (const c of old.controllers) {
        const key = `${c.bot_name}-${c.controller_id || c.controller_name}`;
        oldMap.set(key, c);
      }
      const oldBotMap = new Map(old.bots.map((b) => [b.bot_name, b]));

      return {
        ...incoming,
        controllers: incoming.controllers.map((c) => {
          const key = `${c.bot_name}-${c.controller_id || c.controller_name}`;
          const prev = oldMap.get(key);
          if (!prev) return c;
          return {
            ...c,
            config: Object.keys(c.config || {}).length ? c.config : prev.config,
            deployed_at: c.deployed_at ?? prev.deployed_at,
            connector: c.connector || prev.connector,
            trading_pair: c.trading_pair || prev.trading_pair,
            controller_name: prev.controller_name || c.controller_name,
            controller_id: prev.controller_id || c.controller_id,
          };
        }),
        bots: incoming.bots.map((b) => {
          const prev = oldBotMap.get(b.bot_name);
          return { ...b, deployed_at: b.deployed_at ?? prev?.deployed_at ?? null };
        }),
      };
    });
  } else if (prefix === "executors") {
    if (Array.isArray(data)) {
      const fp = fingerprintExecutorPayload(data);
      const prev = lastExecutorFingerprintByServer.get(server);
      if (prev === fp) {
        return;
      }
      lastExecutorFingerprintByServer.set(server, fp);
    }

    const liveExecs = Array.isArray(data)
      ? dedupeExecutorsById(cloneExecutorList(data) as import("@/lib/api").ExecutorInfo[])
      : data;
    queryClient.setQueryData(["executors", server, ""], liveExecs);

    const allExecs = liveExecs as { controller_id?: string; trading_pair?: string }[];
    if (Array.isArray(allExecs)) {
      const cache = queryClient.getQueryCache().findAll({ queryKey: ["executors", server] });
      for (const entry of cache) {
        const key = entry.queryKey as string[];
        if (key.length <= 3 && key[2] === "") continue;
        if (key.length === 4) {
          const [, , cid, tp] = key;
          const filtered = allExecs.filter(
            (ex) => (!cid || ex.controller_id === cid) && (!tp || ex.trading_pair === tp),
          );
          queryClient.setQueryData(key, filtered);
        }
      }
    }

    const execs = Array.isArray(liveExecs) ? liveExecs : [];
    const infiniteQuery = queryClient
      .getQueryCache()
      .find({ queryKey: ["executors-infinite", server] });
    if (execs.length > 0 && infiniteQuery && infiniteQuery.getObserversCount() > 0) {
      queryClient.setQueryData(
        ["executors-infinite", server],
        (old: { pages?: { executors: unknown[]; next_cursor: string | null }[]; pageParams?: unknown[] } | undefined) => {
          if (!old?.pages?.length) {
            return {
              pages: [{ executors: execs, next_cursor: null }],
              pageParams: [""],
            };
          }
          return {
            ...old,
            pages: mergeExecutorPagesWithWs(old.pages, execs),
          };
        },
      );
    }
  } else if (prefix === "controller_perf") {
    const incoming = data as { snapshots?: ControllerPerformanceSnapshot[] };
    if (incoming?.snapshots) {
      queryClient.setQueryData(
        ["controller-perf-history-all", server],
        (old: ControllerPerformanceHistoryResponse | undefined) => {
          if (!old) return old;
          const merged = mergeSnapshots(old.snapshots ?? [], incoming.snapshots!);
          return { ...old, snapshots: merged };
        },
      );

      const byController = new Map<string, ControllerPerformanceSnapshot[]>();
      for (const snap of incoming.snapshots) {
        const cid = snap.controller_id || snap.controller_name;
        if (!cid) continue;
        const arr = byController.get(cid) ?? [];
        arr.push(snap);
        byController.set(cid, arr);
      }
      for (const [cid, snaps] of byController) {
        queryClient.setQueryData(
          ["controller-perf-history", server, cid],
          (old: ControllerPerformanceHistoryResponse | undefined) => {
            if (!old) return old;
            const merged = mergeSnapshots(old.snapshots ?? [], snaps);
            return { ...old, snapshots: merged };
          },
        );
      }
    }
  } else if (prefix === "orderbook") {
    const parts = channel.split(":");
    if (parts.length >= 4) {
      const [, srv, connector, pair] = parts;
      queryClient.setQueryData(["order-book", srv, connector, pair], data);
    }
  }
}

// ── Shared WebSocket pool (one connection per token+server) ──

interface SharedWsState {
  ws: CondorWebSocket;
  consumerCount: number;
  channelRefs: Map<string, number>;
  connectListeners: Set<() => void>;
  messageCleanup: (() => void) | null;
  connectCleanup: (() => void) | null;
}

const sharedByKey = new Map<string, SharedWsState>();

function sharedKey(token: string, server: string): string {
  return `${token}:${server}`;
}

function subscribeSharedChannel(state: SharedWsState, channel: string): void {
  const count = state.channelRefs.get(channel) ?? 0;
  state.channelRefs.set(channel, count + 1);
  if (count === 0) {
    state.ws.subscribe(channel);
  }
}

function unsubscribeSharedChannel(state: SharedWsState, channel: string): void {
  const count = state.channelRefs.get(channel) ?? 0;
  if (count <= 1) {
    state.channelRefs.delete(channel);
    state.ws.unsubscribe(channel);
  } else {
    state.channelRefs.set(channel, count - 1);
  }
}

function diffSharedChannels(
  state: SharedWsState,
  oldSet: Set<string>,
  newSet: Set<string>,
): void {
  for (const ch of newSet) {
    if (!oldSet.has(ch)) subscribeSharedChannel(state, ch);
  }
  for (const ch of oldSet) {
    if (!newSet.has(ch)) unsubscribeSharedChannel(state, ch);
  }
}

function acquireSharedWs(
  token: string,
  server: string,
  queryClient: QueryClient,
): SharedWsState {
  const key = sharedKey(token, server);
  let state = sharedByKey.get(key);
  if (!state) {
    const ws = new CondorWebSocket(token);
    candleStore.setWs(ws);
    const messageCleanup = ws.onMessage((channel, data) => {
      handleWsMessage(channel, data, server, queryClient);
    });
    const connectListeners = new Set<() => void>();
    const connectCleanup = ws.onConnect(() => {
      for (const listener of connectListeners) listener();
    });
    ws.connect();
    state = {
      ws,
      consumerCount: 0,
      channelRefs: new Map(),
      connectListeners,
      messageCleanup,
      connectCleanup,
    };
    sharedByKey.set(key, state);
  }
  state.consumerCount += 1;
  return state;
}

function releaseSharedWs(token: string, server: string): void {
  const key = sharedKey(token, server);
  const state = sharedByKey.get(key);
  if (!state) return;
  state.consumerCount -= 1;
  if (state.consumerCount > 0) return;

  state.messageCleanup?.();
  state.connectCleanup?.();
  state.ws.disconnect();
  candleStore.setWs(null);
  sharedByKey.delete(key);
}

export function useCondorWebSocket(
  channels: string[],
  server: string | null,
) {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const wsRef = useRef<CondorWebSocket | null>(null);
  const sharedRef = useRef<SharedWsState | null>(null);
  const [wsVersion, setWsVersion] = useState(0);
  const prevChannelsRef = useRef<Set<string>>(new Set());

  // ── Effect 1: Acquire / release shared WS (one socket per token+server) ──
  useEffect(() => {
    if (!token || !server) return;

    const state = acquireSharedWs(token, server, queryClient);
    sharedRef.current = state;
    wsRef.current = state.ws;

    const onConnect = () => setWsVersion((v) => v + 1);
    state.connectListeners.add(onConnect);

    return () => {
      state.connectListeners.delete(onConnect);
      releaseSharedWs(token, server);
      sharedRef.current = null;
      wsRef.current = null;
      prevChannelsRef.current = new Set();
    };
  }, [token, server, queryClient]);

  // ── Effect 2: Diff channels — subscribe/unsubscribe without reconnecting ──
  useEffect(() => {
    const state = sharedRef.current;
    if (!state || !server) return;

    const resolved = nonCandleChannels(channels).map((ch) =>
      ch.includes(":") ? ch : `${ch}:${server}`,
    );
    const newSet = new Set(resolved);
    const oldSet = prevChannelsRef.current;

    diffSharedChannels(state, oldSet, newSet);

    // Re-subscribe on reconnect so channels stay active after socket reopen.
    if (wsVersion > 0) {
      for (const ch of newSet) {
        state.ws.subscribe(ch);
      }
    }

    prevChannelsRef.current = newSet;
  }, [channels.join(","), server, wsVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  return { wsRef, wsVersion };
}
