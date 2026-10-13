// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseFrames, sseFetch } from "./sse";

type Seen = [string, unknown][];

const collect = () => {
  const seen: Seen = [];
  return { seen, onEvent: (name: string, payload: unknown) => void seen.push([name, payload]) };
};

/** A response whose body arrives in exactly these pieces. */
function streamed(chunks: (string | Uint8Array)[], init: ResponseInit = {}): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      }
      controller.close();
    },
  });
  return new Response(body, { status: 200, ...init });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseFrames", () => {
  it("reads the event name and parses the data as JSON", () => {
    const { seen, onEvent } = collect();
    const rest = parseFrames('event: token\ndata: {"t":"Hi"}\n\n', onEvent);
    expect(seen).toEqual([["token", { t: "Hi" }]]);
    expect(rest).toBe("");
  });

  it("calls a frame with no event line 'message'", () => {
    const { seen, onEvent } = collect();
    parseFrames("data: 1\n\n", onEvent);
    expect(seen).toEqual([["message", 1]]);
  });

  it("keeps the start of a frame that has not ended", () => {
    const { seen, onEvent } = collect();
    const rest = parseFrames('event: a\ndata: 1\n\nevent: b\ndata: {"half":', onEvent);
    expect(seen).toEqual([["a", 1]]);
    expect(rest).toBe('event: b\ndata: {"half":');
  });

  it("joins several data lines with newlines before parsing", () => {
    const { seen, onEvent } = collect();
    parseFrames('event: multi\ndata: {"a":\ndata: 1}\n\n', onEvent);
    expect(seen).toEqual([["multi", { a: 1 }]]);
  });

  it("hands over text that is not JSON as { raw }", () => {
    const { seen, onEvent } = collect();
    parseFrames("event: note\ndata: line one\ndata: line two\n\n", onEvent);
    expect(seen).toEqual([["note", { raw: "line one\nline two" }]]);
  });

  it("skips a frame with no data, such as a comment or a bare event line", () => {
    const { seen, onEvent } = collect();
    parseFrames(": keep-alive\n\nevent: ping\n\nevent: real\ndata: 2\n\n", onEvent);
    expect(seen).toEqual([["real", 2]]);
  });

  it("trims a carriage return left on a line", () => {
    const { seen, onEvent } = collect();
    parseFrames("event: a\r\ndata: 1\r\n\n", onEvent);
    expect(seen).toEqual([["a", 1]]);
  });

  // The server writes LF and frames are split on a blank LF line. A stream
  // framed entirely in CRLF has no such line, so nothing is recognised: the
  // parser was never asked to read one, and this pins that it does not.
  it("does not recognise frames separated by CRLF CRLF", () => {
    const { seen, onEvent } = collect();
    const text = "event: a\r\ndata: 1\r\n\r\n";
    expect(parseFrames(text, onEvent)).toBe(text);
    expect(seen).toEqual([]);
  });

  it("does nothing without a handler", () => {
    expect(parseFrames("event: a\ndata: 1\n\nrest")).toBe("rest");
  });
});

describe("sseFetch", () => {
  it("posts the body as JSON and passes the signal on", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => streamed([]));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await sseFetch("/api/chat", { body: { message: "hi" }, signal: controller.signal });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/chat");
    expect(init?.method).toBe("POST");
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
    expect(init?.body).toBe('{"message":"hi"}');
    expect(init?.signal).toBe(controller.signal);
  });

  it("sends no body when there is none", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => streamed([]));
    vi.stubGlobal("fetch", fetchMock);
    await sseFetch("/api/bench/run");
    expect(fetchMock.mock.calls[0]![1]?.body).toBeUndefined();
  });

  it("reassembles frames split across chunks, in order", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamed([
          "event: sta",
          'rt\ndata: {"request_id":"r1"}\n',
          '\nevent: token\ndata: {"t":"He"}\n\nevent: token\nda',
          'ta: {"t":"llo"}\n\n',
        ]),
      ),
    );
    const { seen, onEvent } = collect();
    await sseFetch("/api/chat", { onEvent });
    expect(seen).toEqual([
      ["start", { request_id: "r1" }],
      ["token", { t: "He" }],
      ["token", { t: "llo" }],
    ]);
  });

  it("decodes a character whose bytes arrive in two chunks", async () => {
    const bytes = new TextEncoder().encode('event: token\ndata: {"t":"é—"}\n\n');
    const cut = bytes.indexOf(0xc3) + 1; // between the two bytes of "é"
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamed([bytes.slice(0, cut), bytes.slice(cut)])),
    );
    const { seen, onEvent } = collect();
    await sseFetch("/api/chat", { onEvent });
    expect(seen).toEqual([["token", { t: "é—" }]]);
  });

  it("drops a trailing frame the server never finished", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => streamed(['event: a\ndata: 1\n\nevent: b\ndata: {"cut'])),
    );
    const { seen, onEvent } = collect();
    await sseFetch("/api/chat", { onEvent });
    expect(seen).toEqual([["a", 1]]);
  });

  it("narrows the data by the event's name for a typed stream", async () => {
    type Events = { start: { request_id: string }; token: { t: string } };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        streamed(['event: start\ndata: {"request_id":"r9"}\n\nevent: token\ndata: {"t":"x"}\n\n']),
      ),
    );
    let id = "";
    let text = "";
    await sseFetch<Events>("/api/chat", {
      onEvent: (event, data) => {
        if (event === "start") id = data.request_id;
        else if (event === "token") text += data.t;
      },
    });
    expect(id).toBe("r9");
    expect(text).toBe("x");
  });

  it("throws FastAPI's own detail when the request is refused", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail: "No such conversation" }, { status: 404 })),
    );
    await expect(sseFetch("/api/chat")).rejects.toThrow("No such conversation");
  });

  it("joins a list of validation problems", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            detail: [{ msg: "String should have at least 1 character" }, { msg: "Field required" }],
          },
          { status: 422 },
        ),
      ),
    );
    await expect(sseFetch("/api/chat")).rejects.toThrow(
      "String should have at least 1 character; Field required",
    );
  });

  it("has words for the statuses a visitor can meet", async () => {
    for (const [status, words] of [
      [413, "That message is too long for the server to accept."],
      [429, "Too many requests — give the server a moment."],
      [503, "The model is not loaded yet."],
    ] as const) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => new Response("<html>nope</html>", { status })),
      );
      await expect(sseFetch("/api/chat")).rejects.toThrow(words);
    }
  });

  it("falls back to the status line", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("oops", { status: 500, statusText: "Internal Server Error" })),
    );
    await expect(sseFetch("/api/chat")).rejects.toThrow(
      "The server returned 500 Internal Server Error.",
    );
  });
});
