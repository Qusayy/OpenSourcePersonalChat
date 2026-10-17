import type { Locator, Page } from "@playwright/test";

/* Helpers for the chat specs.

   Most of them drive the page against a stream the test controls rather than
   the server's: the page is handed a response whose events arrive when the
   test says, so a state that lasts a moment on the server (queued, reading,
   an error) can be held and looked at. Everything else the page asks for is
   the real app's. Specs that are about the stream itself use the real one. */

type Chat = {
  posts: unknown[];
  streams: Array<{
    push(event: string, data: unknown): void;
    end(): void;
    aborted: boolean;
  }>;
  transitions: number;
};

/**
 * Take over POST /api/chat in the page. Call before `page.goto`. Each send
 * then opens a stream that stays open until the test ends it; `push` adds an
 * event to the newest one.
 */
export async function fakeChat(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const chat: Chat = { posts: [], streams: [], transitions: 0 };
    (window as unknown as { __chat: Chat }).__chat = chat;
    // Count view transitions, so a spec can say whether a change of view was
    // the animated kind.
    const start = document.startViewTransition?.bind(document);
    if (start) {
      document.startViewTransition = ((update?: () => void) => {
        chat.transitions += 1;
        return start(update);
      }) as typeof document.startViewTransition;
    }
    const real = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (!url.includes("/api/chat")) return real(input, init);
      chat.posts.push(JSON.parse(String(init?.body ?? "{}")));
      const encoder = new TextEncoder();
      let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          controller = c;
        },
      });
      const stream = {
        aborted: false,
        push(event: string, data: unknown) {
          controller?.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
        },
        end() {
          try {
            controller?.close();
          } catch {
            /* already closed */
          }
        },
      };
      init?.signal?.addEventListener("abort", () => {
        stream.aborted = true;
        try {
          controller?.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* already closed */
        }
      });
      chat.streams.push(stream);
      return Promise.resolve(
        new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
      );
    };
  });
}

const chat = "(window.__chat)";

/** Send one event down the newest stream. */
export function push(page: Page, event: string, data: unknown): Promise<void> {
  return page.evaluate(
    `${chat}.streams.at(-1).push(${JSON.stringify(event)}, ${JSON.stringify(data)})`,
  );
}

/** End the newest stream, as the server does after `done` or `error`. */
export function end(page: Page): Promise<void> {
  return page.evaluate(`${chat}.streams.at(-1).end()`);
}

/** The bodies of every POST to /api/chat so far. */
export function posts(page: Page): Promise<Array<Record<string, unknown>>> {
  return page.evaluate(`${chat}.posts`);
}

/** How many view transitions the page has started. */
export function transitions(page: Page): Promise<number> {
  return page.evaluate(`${chat}.transitions`);
}

/** Whether the page closed the newest stream's connection itself. */
export function aborted(page: Page): Promise<boolean> {
  return page.evaluate(`${chat}.streams.at(-1).aborted`);
}

// The first event names the run, and the pass that writes the answer goes by
// the same name.
export const CONVERSATION = {
  id: "c0ffee",
  title: "A test",
  created: false,
  request_id: "req-1",
};
export const START = { request_id: "req-1", queue_wait_ms: 0, prompt_tokens: 56, n_ctx: 4096 };
/** How a run ends when it was stopped before its answer was begun. */
export const STOPPED = {
  cancelled: true,
  ttft_ms: null,
  queue_wait_ms: null,
  prompt_tokens: null,
  completion_tokens: 0,
  prefill_tps: null,
  gen_tps: null,
  total_ms: null,
  rss_mb: null,
  tool_calls: 0,
};
export const DONE = {
  cancelled: false,
  ttft_ms: 86.4,
  queue_wait_ms: 0,
  prompt_tokens: 56,
  completion_tokens: 12,
  prefill_tps: 650,
  gen_tps: 11.72,
  total_ms: 1100,
  rss_mb: 60,
  tool_calls: 0,
};

/** A whole answer down the newest stream: start, the text in pieces, done. */
export async function answer(page: Page, text: string, done: object = DONE): Promise<void> {
  await push(page, "conversation", CONVERSATION);
  await push(page, "start", START);
  const pieces = text.match(/\S+\s*/g) ?? [text];
  for (const [i, t] of pieces.entries()) {
    await push(page, "token", i === 0 ? { t, ttft_ms: 86.4 } : { t });
  }
  await push(page, "done", done);
  await end(page);
}

/** Type a question and press Enter. */
export async function ask(page: Page, text: string): Promise<void> {
  await page.locator("#input").fill(text);
  await page.locator("#input").press("Enter");
}

/** Open the history drawer and the nth stored conversation (0 is the newest). */
export async function openStored(page: Page, n: number): Promise<void> {
  await page.locator("#menu-btn").click();
  await page.locator("#convo-list [data-id]").nth(n).click();
  await page.locator('#main[data-view="thread"]').waitFor();
}

export const question = (page: Page): Locator => page.locator('[data-part="question"]');
export const answerOf = (page: Page): Locator => page.locator('[data-part="answer"]');
export const notice = (page: Page): Locator => page.locator('[data-part="notice"]');
