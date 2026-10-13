/* What the server knows, kept current.
 *
 * Two things are polled, by exactly one component each (shell/Layout): the
 * engine's health and the machine's telemetry. Every other reader subscribes
 * to the same cache entry without asking the server itself, so three places
 * showing memory use cost one request, not three.
 *
 * This box has two cores and other work to do. Nothing here retries on its
 * own, and nothing is asked more often than the figures can change.
 */

import { QueryClient, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { bootstrap } from "@/lib/bootstrap";
import type {
  BenchResults,
  Bootstrap,
  ConversationSummary,
  EngineInfo,
  Health,
  SystemSnapshot,
  Telemetry,
} from "@/lib/types";
import { onRequestBoundary, ownRequestUntil, setOtherBusy, useShell } from "./shell";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      // The server is this page's own origin; `navigator.onLine` says nothing
      // useful about whether it answers.
      networkMode: "always",
    },
  },
});

export const keys = {
  health: ["health"] as const,
  telemetry: ["telemetry"] as const,
  conversations: ["conversations"] as const,
  conversation: (id: string) => ["conversation", id] as const,
  bench: ["bench"] as const,
};

/** A telemetry sample, and whether the next should come early (see below). */
interface Sample extends Telemetry {
  retry: boolean;
}

/**
 * Put the bootstrap's values in the cache, so the first render has them. They
 * are marked as old as possible: the figures in the page are from when it was
 * rendered, and both polls ask again straight away.
 */
export function seedQueries(boot: Bootstrap): void {
  const { info, system } = boot;
  queryClient.setQueryData<Health>(
    keys.health,
    { engine: info, system, chat: { n: 0, avg_tps: null, avg_ttft: null, tokens: null } },
    { updatedAt: 0 },
  );
  queryClient.setQueryData<Sample>(
    keys.telemetry,
    {
      system,
      engine: { status: info.status, busy: info.busy, queued: info.queued },
      retry: false,
    },
    { updatedAt: 0 },
  );
  queryClient.setQueryData<ConversationSummary[]>(keys.conversations, boot.conversations);
}

/* ---------------------------------------------------------------- health -- */

/**
 * Poll the engine's health. Mount once. Briskly while the model is loading,
 * then every fifteen seconds: this box has no cycles to spare for a chatty
 * front-end.
 */
export function usePollHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: api.health,
    refetchInterval: (query) => (query.state.data?.engine.status === "ready" ? 15000 : 1500),
    refetchIntervalInBackground: true,
  });
}

/** The engine as last reported. Does not poll. */
export function useInfo(): EngineInfo {
  const { data } = useQuery({ queryKey: keys.health, queryFn: api.health, enabled: false });
  return data?.engine ?? bootstrap().info;
}

/** The engine as last reported, outside a component. */
export function currentInfo(): EngineInfo {
  return queryClient.getQueryData<Health>(keys.health)?.engine ?? bootstrap().info;
}

/** Ask for the engine's health now, and resolve to it (null if it did not answer). */
export async function refreshHealth(): Promise<EngineInfo | null> {
  try {
    const data = await queryClient.fetchQuery({ queryKey: keys.health, queryFn: api.health });
    return data.engine;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------- telemetry -- */

// A load figure is the difference between two readings. A server nobody has
// asked for a while has only one, and answers with no figure; the next
// reading it can use is half a second later, so that is when to ask again,
// rather than leaving the slot empty for seconds.
const TELE_RETRY_MS = 600;
let teleRetries = 0;

async function sampleTelemetry(): Promise<Sample> {
  const asked = performance.now();
  const mine = Boolean(useShell.getState().phase);
  const data = await api.telemetry();
  // Only a sample taken wholly outside our own request can be about someone else's.
  const stillClear = !useShell.getState().phase;
  setOtherBusy(Boolean(data.engine?.busy) && !mine && stillClear && asked >= ownRequestUntil());
  let retry = false;
  if (data.system?.cpu_pct === null && teleRetries < 2) {
    teleRetries += 1;
    retry = true;
  } else {
    teleRetries = 0;
  }
  return { ...data, retry };
}

/**
 * Poll the machine's telemetry. Mount once. Once a second while a request is
 * running, when the figures are moving; every three seconds otherwise; not at
 * all while the tab is hidden, and at once when it is seen again.
 */
export function usePollTelemetry() {
  const inFlight = useShell((s) => s.phase !== null && s.phase.key !== "held");
  return useQuery({
    queryKey: keys.telemetry,
    queryFn: sampleTelemetry,
    refetchInterval: (query) => (query.state.data?.retry ? TELE_RETRY_MS : inFlight ? 1000 : 3000),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}

/** The machine as last measured. Does not poll. */
export function useSystem(): SystemSnapshot {
  const { data } = useQuery({ queryKey: keys.telemetry, queryFn: sampleTelemetry, enabled: false });
  return data?.system ?? bootstrap().system;
}

// A request starting or ending changes how often the machine is sampled; do
// not wait out the slower interval to notice.
let nudge: ReturnType<typeof setTimeout> | undefined;
onRequestBoundary(() => {
  clearTimeout(nudge);
  nudge = setTimeout(() => {
    if (document.hidden) return; // picked up again when the tab is seen
    void queryClient.refetchQueries({ queryKey: keys.telemetry });
  }, 250);
});

/* --------------------------------------------------------- conversations -- */

/** The stored conversations, newest first. Refreshed only when asked: after a
 *  send, a delete, or when the history is opened. */
export function useConversations(): ConversationSummary[] {
  const { data } = useQuery({
    queryKey: keys.conversations,
    queryFn: api.conversations,
    staleTime: Number.POSITIVE_INFINITY,
  });
  return data ?? bootstrap().conversations;
}

/** Fetch the list again and resolve to it. */
export function refreshConversations(): Promise<ConversationSummary[]> {
  return queryClient.fetchQuery({ queryKey: keys.conversations, queryFn: api.conversations });
}

/* ------------------------------------------------------------- benchmark -- */

/** Stored benchmark results: the newest suite and the history behind the chart. */
export function useBenchResults() {
  return useQuery<BenchResults>({ queryKey: keys.bench, queryFn: api.benchResults });
}
