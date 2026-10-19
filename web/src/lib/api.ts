/* The JSON half of the API. The two streamed endpoints (POST /api/chat and
   POST /api/bench/run) are read with `sse.ts`; their event types are
   `ChatEvents` and `BenchEvents`. */

import { relock } from "./gate";
import type {
  BenchResults,
  Bootstrap,
  ConfirmChoice,
  Conversation,
  ConversationSummary,
  Health,
  ProjectFileRow,
  Telemetry,
} from "./types";

export const CHAT_URL = "/api/chat";
export const BENCH_RUN_URL = "/api/bench/run";
/** Where a conversation's project is downloaded, as one zip. */
export const projectZipUrl = (id: string): string =>
  `/api/conversations/${encodeURIComponent(id)}/project.zip`;

/** A response that was not 2xx. */
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (res.status === 401) relock();
  if (!res.ok) throw new ApiError(res.status, `${init?.method ?? "GET"} ${url}: ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  bootstrap: () => json<Bootstrap>("/api/bootstrap"),
  health: () => json<Health>("/api/health"),
  telemetry: () => json<Telemetry>("/api/telemetry"),

  conversations: async () =>
    (await json<{ conversations: ConversationSummary[] }>("/api/conversations")).conversations,
  conversation: (id: string) => json<Conversation>(`/api/conversations/${encodeURIComponent(id)}`),
  /** A conversation's project: every file as it stands, with its content. */
  project: async (id: string) =>
    (
      await json<{ files: ProjectFileRow[] }>(
        `/api/conversations/${encodeURIComponent(id)}/project`,
      )
    ).files,
  deleteConversation: (id: string) =>
    json<{ ok: boolean }>(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),

  /** Ask the engine to stop a generation. Only possible once the stream's
   *  `start` event has named the request. */
  cancel: (requestId: string) =>
    json<{ cancelled: boolean }>(`/api/cancel/${encodeURIComponent(requestId)}`, {
      method: "POST",
    }),

  /** Answer a change a run put to the user. `answered` is false when there
   *  was no such question waiting: answered already, or its time was up. */
  confirm: (requestId: string, id: string, choice: ConfirmChoice) =>
    json<{ answered: boolean }>(`/api/confirm/${encodeURIComponent(requestId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, choice }),
    }),

  benchResults: () => json<BenchResults>("/api/bench/results"),
};
