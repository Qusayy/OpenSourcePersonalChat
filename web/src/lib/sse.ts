/* SSE over fetch.
 *
 * EventSource cannot POST, and the chat request carries a body, so the stream
 * is read straight off the response with a reader and parsed here. Frames are
 * "event: <name>\ndata: <json>\n\n".
 */

import { relock } from "./gate";

/** What a stream can send: each event's name, and the shape of its data. */
export type SseEventMap = Record<string, unknown>;

/** One event as its handler receives it: the name, then the data that name
 *  carries. Written as a union of pairs so that testing the name narrows the
 *  data in the handler's body. */
export type SseEvent<M extends SseEventMap> = {
  [K in keyof M & string]: [name: K, payload: M[K]];
}[keyof M & string];

export type SseHandler<M extends SseEventMap = SseEventMap> = (...event: SseEvent<M>) => void;

export interface SseOptions<M extends SseEventMap = SseEventMap> {
  body?: unknown;
  signal?: AbortSignal;
  onEvent?: SseHandler<M>;
}

const KNOWN: Record<number, string> = {
  413: "That message is too long for the server to accept.",
  429: "Too many requests — give the server a moment.",
  503: "The model is not loaded yet.",
};

/**
 * Take every complete frame off the front of `buffer`, hand each to `onEvent`,
 * and return what is left: the start of a frame whose end has not arrived yet.
 */
export function parseFrames<M extends SseEventMap = SseEventMap>(
  buffer: string,
  onEvent?: SseHandler<M>,
): string {
  // The handler is typed for its caller; here an event is a name and whatever
  // the server sent with it.
  const emit = onEvent as ((name: string, payload: unknown) => void) | undefined;
  let split = buffer.indexOf("\n\n");
  while (split !== -1) {
    const frame = buffer.slice(0, split);
    buffer = buffer.slice(split + 2);
    split = buffer.indexOf("\n\n");
    let name = "message";
    const data: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) name = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (!data.length) continue;
    let payload: unknown;
    try {
      payload = JSON.parse(data.join("\n"));
    } catch {
      payload = { raw: data.join("\n") };
    }
    emit?.(name, payload);
  }
  return buffer;
}

export async function sseFetch<M extends SseEventMap = SseEventMap>(
  url: string,
  { body, signal, onEvent }: SseOptions<M> = {},
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });

  if (res.status === 401) relock();
  if (!res.ok || !res.body) {
    // FastAPI puts validation problems in the body; a bare status code tells
    // the user nothing they can act on.
    let detail = "";
    try {
      const payload = (await res.json()) as { detail?: unknown } | null;
      detail =
        typeof payload?.detail === "string"
          ? payload.detail
          : Array.isArray(payload?.detail)
            ? payload.detail.map((d: { msg?: unknown }) => d.msg).join("; ")
            : "";
    } catch {
      /* not JSON — fall back to the status line */
    }
    const known = KNOWN[res.status];
    throw new Error(detail || known || `The server returned ${res.status} ${res.statusText}.`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = parseFrames(buffer, onEvent);
  }
}
