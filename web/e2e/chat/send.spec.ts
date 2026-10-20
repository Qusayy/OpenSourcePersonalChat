import { expect, type Page, test } from "@playwright/test";
import type { ConfirmEvent } from "@/lib/types";
import { reseed } from "../support";
import {
  aborted,
  answer,
  answerOf,
  ask,
  CONVERSATION,
  called,
  DONE,
  end,
  fakeChat,
  notice,
  order,
  posts,
  push,
  question,
  reply,
  START,
  STOPPED,
  transitions,
} from "./helpers";

/* Sending a question and following its answer: the phases the status line
   passes through, stopping, what is said when the server makes the visitor
   wait or fails, and what happens to an answer when the page is left. */

test.beforeEach(() => reseed());

test.describe("against the real stream", () => {
  test("a question is posted once and answered, with what it cost", async ({ page }) => {
    const chatPosts: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "POST" && r.url().endsWith("/api/chat"))
        chatPosts.push(r.postData() ?? "");
    });
    await page.goto("/");
    await expect(page.locator("#main")).toHaveAttribute("data-view", "empty");
    await expect(page.locator("#input")).toBeFocused();

    await ask(page, "why is the sky blue?");

    // The sheet becomes a thread, headed by the question.
    await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
    await expect(question(page)).toHaveText("why is the sky blue?");
    await expect(page.locator("#input")).toHaveValue("");
    // Send becomes Stop for as long as the answer is in flight.
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "stop");
    await expect(page.locator("#send")).toHaveAccessibleName("Stop generating");
    await expect(page.locator("#send-label")).toHaveText("Stop");

    // The line comes alive: the model is writing, the rate is on the line and
    // the aurora is up.
    const line = page.locator("#status");
    await expect(line).toHaveAttribute("data-phase", "writing", { timeout: 20_000 });
    await expect(line.locator("#b-state")).toContainText("writing");
    await expect(line.locator(".st-tps")).toBeVisible();
    await expect(line.locator("#ttft-value")).toHaveText(/\d+ms|\d\.\d\ds/);
    await expect(page.locator("#aurora")).toBeVisible();
    // The answer is arriving in the one element React leaves alone.
    const prose = answerOf(page).locator(".prose");
    await expect(prose.locator(".caret")).toHaveCount(1);
    await expect(prose).not.toBeEmpty();

    // It ends: the figures leave the line and are printed under the answer.
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send", { timeout: 40_000 });
    await expect(line).toHaveAttribute("data-phase", "ready");
    await expect(line.locator(".st-tps")).toBeHidden();
    const cost = answerOf(page).locator('[data-part="cost"]');
    await expect(cost).toContainText(/[\d.]+ tok\/s/);
    await expect(cost).toContainText("to first token");
    await expect(cost).toContainText(/\d+ tokens/);
    await expect(cost).toContainText("to answer");
    await expect(cost.getByRole("button", { name: "Copy" })).toBeVisible();
    await expect(cost.getByRole("button", { name: "Ask again" })).toBeVisible();
    await expect(prose.locator(".caret")).toHaveCount(0);
    // The streamed answer keeps the two containers it was written into.
    await expect(prose.locator(":scope > div")).toHaveCount(2);
    await expect(page.locator("#sr-status")).toHaveText(/^Answer complete — \d+ tokens at /);
    await expect(page.locator("#ctx-value")).not.toHaveText("0");
    await expect(page.locator("#input")).toBeFocused();

    expect(chatPosts).toHaveLength(1);
    expect(JSON.parse(chatPosts[0] ?? "{}")).toMatchObject({
      message: "why is the sky blue?",
      conversation_id: null,
    });
  });

  test("Stop ends the answer and keeps what was written", async ({ page }) => {
    await page.goto("/");
    await ask(page, "tell me about the sea");
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "writing", {
      timeout: 20_000,
    });
    await page.locator("#send").click();
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send", { timeout: 15_000 });
    const cost = answerOf(page).locator('[data-part="cost"]');
    await expect(cost).toContainText("stopped");
    await expect(answerOf(page).locator(".prose")).not.toBeEmpty();
    await expect(page.locator("#sr-status")).toHaveText("Generation stopped.");
  });

  test("Enter while an answer is being written stops it", async ({ page }) => {
    await page.goto("/");
    await ask(page, "tell me about the sea");
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "writing", {
      timeout: 20_000,
    });
    await page.locator("#input").press("Enter");
    await expect(answerOf(page).locator('[data-part="cost"]')).toContainText("stopped", {
      timeout: 15_000,
    });
  });

  test("leaving Chat mid-answer stops it and keeps the text", async ({ page }) => {
    const cancels: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/cancel/")) cancels.push(r.url());
    });
    await page.goto("/");
    await ask(page, "tell me about the sea");
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "writing", {
      timeout: 20_000,
    });
    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "About" })
      .click();
    await expect(page).toHaveURL(/\/about$/);
    // The same as pressing Stop: the engine is told, and the line settles.
    await expect.poll(() => cancels.length).toBe(1);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "ready", {
      timeout: 15_000,
    });

    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "Chat" })
      .click();
    await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
    await expect(question(page)).toHaveText("tell me about the sea");
    await expect(answerOf(page).locator(".prose")).not.toBeEmpty();
    await expect(answerOf(page).locator('[data-part="cost"]')).toContainText("stopped");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
  });
});

test.describe("against a stream the test controls", () => {
  test.beforeEach(async ({ page }) => {
    await fakeChat(page);
    await page.goto("/");
  });

  test("the first send moves the composer in a view transition; later ones do not", async ({
    page,
  }) => {
    const before = await transitions(page);
    await ask(page, "first");
    await answer(page, "One.");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    expect((await transitions(page)) - before).toBe(1);
    await ask(page, "second");
    await answer(page, "Two.");
    await expect(question(page)).toHaveCount(2);
    expect((await transitions(page)) - before).toBe(1);
  });

  test("a wait in the queue is counted on the line and said in the thread", async ({ page }) => {
    await ask(page, "hello");
    await push(page, "conversation", CONVERSATION);
    await push(page, "queue", { position: 2, first: true });
    await expect(notice(page)).toHaveText(
      "2 requests ahead of yours — this server runs one generation at a time.",
    );
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "queued");
    await expect(page.locator("#b-state")).toContainText("queued");
    await expect(page.locator("#st-wait-lead")).toHaveText("2 ahead");
    await expect(page.locator("#sr-status")).toHaveText(/^2 requests ahead/);

    await push(page, "queue", { position: 1, first: false });
    await expect(notice(page)).toHaveText(
      "One request ahead of yours — this server runs one generation at a time.",
    );
    await expect(page.locator("#st-wait-lead")).toHaveText("1 ahead");
    await push(page, "queue", { position: 0, first: false });
    await expect(notice(page)).toHaveText("Next in line — starting shortly.");
    await expect(page.locator("#st-wait-lead")).toHaveText("next in line");
    // Only one line, however many updates.
    await expect(notice(page)).toHaveCount(1);

    // The engine takes the request: the line resolves and the model reads.
    await push(page, "start", START);
    await expect(notice(page)).toHaveCount(0);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "reading");
    await expect(page.locator("#ctx-value")).toHaveText("56");
  });

  test("a wait is timed in seconds once it has lasted two", async ({ page }) => {
    await ask(page, "hello");
    await push(page, "conversation", CONVERSATION);
    await push(page, "start", START);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "reading");
    await expect(page.locator("#st-wait")).toBeHidden();
    await expect(page.locator("#st-wait-s")).toHaveText(/^[23] s$/, { timeout: 5000 });
    await expect(page.locator("#st-wait")).toBeVisible();
  });

  test("an error says what broke and offers the way back", async ({ page }) => {
    await ask(page, "hello there");
    await push(page, "conversation", CONVERSATION);
    await push(page, "error", { message: "RuntimeError: the engine fell over" });
    await end(page);

    const error = notice(page);
    await expect(error).toHaveAttribute("data-kind", "err");
    await expect(error).toContainText("RuntimeError: the engine fell over");
    await expect(page.locator("#sr-status")).toHaveText("RuntimeError: the engine fell over");
    // An answer that never began leaves nothing behind.
    await expect(answerOf(page)).toHaveCount(0);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");

    await error.getByRole("button", { name: "Try again" }).click();
    await expect(notice(page)).toHaveCount(0);
    await expect
      .poll(async () => (await posts(page)).map((p) => p.message))
      .toEqual(["hello there", "hello there"]);
    await answer(page, "Better.");
    await expect(answerOf(page).locator(".prose")).toHaveText("Better.");
  });

  test("a dropped connection keeps what had arrived", async ({ page }) => {
    await ask(page, "hello there");
    await push(page, "conversation", CONVERSATION);
    await push(page, "start", START);
    await push(page, "token", { t: "Half an ", ttft_ms: 90 });
    await push(page, "token", { t: "answer" });
    await expect(answerOf(page).locator(".prose")).toContainText("Half an answer");
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await expect(answerOf(page).locator(".prose")).toHaveText("Half an answer");
    await expect(answerOf(page).locator(".caret")).toHaveCount(0);
    // No `done`, so nothing is claimed about what it cost.
    await expect(answerOf(page).locator('[data-part="cost"]')).toHaveCount(0);
  });

  test("a figure nothing returned is said under the answer", async ({ page }) => {
    await ask(page, "/weather Paris");
    await answer(page, "It is 25°C.", {
      ...DONE,
      unsupported: ["25°C"],
      correction: "Not in what was looked up: 25°C. Weather returned: Paris, France: 17.9°C.",
    });
    await expect(answerOf(page).locator('[data-part="correction"]')).toHaveText(
      "Not in what was looked up: 25°C. Weather returned: Paris, France: 17.9°C.",
    );
  });

  test("dropped history is said in the thread", async ({ page }) => {
    await ask(page, "hello");
    await push(page, "conversation", CONVERSATION);
    await push(page, "trimmed", { dropped: 3 });
    await expect(notice(page)).toHaveText(
      "The 3 oldest messages were dropped to fit the context window.",
    );
    await answer(page, "Fine.");
    // Unlike the queue line, this one stays: it is a fact about the answer.
    await expect(notice(page)).toHaveCount(1);
  });

  test("a message cut to fit is said in the thread", async ({ page }) => {
    await ask(page, "hello");
    await push(page, "conversation", CONVERSATION);
    await push(page, "trimmed", { dropped: 0, turns: 0, summarised: 0, cut: true });
    await expect(notice(page)).toHaveText(
      "Your message was too long to read whole; the model read its first part.",
    );
    await answer(page, "Fine.");
  });

  test("a call is drawn while it runs, and its card comes where the reply made it", async ({
    page,
  }) => {
    await ask(page, "what is the weather in Paris?");
    await push(page, "conversation", CONVERSATION);
    const calls = answerOf(page).locator('[data-part="calls"]');
    // A reply of the model is never drawn as a row: the status line carries it.
    await reply(page, 1, { say: "Let me look that up.", called: "Weather" });
    await expect(answerOf(page).locator(".prose")).toHaveText("Let me look that up.");
    await expect(calls).toHaveCount(0);

    await push(page, "step", {
      id: "tool-0",
      kind: "tool",
      status: "running",
      label: "Weather",
      tool: "weather",
      args: { location: "Paris" },
    });
    await expect(calls).toHaveText("Weatherrunning");
    await expect(calls.locator('[data-status="running"]')).toHaveCount(1);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "working");
    // Under what was said before it, where its result will come.
    expect(await order(page)).toEqual(["text", "calls"]);

    // A finished call is named and timed by its own figure, so its row goes.
    await push(page, "step", {
      id: "tool-0",
      kind: "tool",
      status: "ok",
      label: "Weather",
      tool: "weather",
      ms: 412,
    });
    await expect(calls).toHaveCount(0);
    await push(page, "card", {
      step_id: "tool-0",
      card: "weather",
      tool: "weather",
      data: { place: "Paris", temperature: 12 },
    });
    const card = answerOf(page).locator('figure[data-card="weather"]');
    await expect(card.locator("figcaption")).toContainText("412ms");

    // What the next reply says comes under the card, in a part of its own.
    await reply(page, 2, { say: "It is mild." });
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "writing");
    expect(await order(page)).toEqual(["text", "cards", "text"]);
    await push(page, "done", { ...DONE, tool_calls: 1 });
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    expect(await order(page)).toEqual(["text", "cards", "text"]);
    const prose = answerOf(page).locator(".prose");
    await expect(prose.first()).toHaveText("Let me look that up.");
    await expect(prose.nth(1)).toHaveText("It is mild.");
    await expect(prose.locator(".caret")).toHaveCount(0);
    // Nothing anywhere says a tool was being chosen.
    await expect(answerOf(page)).not.toContainText(/Choosing a tool|Model's choice|No tool needed/);
  });

  test("a run that calls twice reads in order, and How it ran lists each reply", async ({
    page,
  }) => {
    const weather = (index: number, place: string) =>
      called(page, index, {
        label: "Weather",
        tool: "weather",
        card: "weather",
        data: { place, temperature: 12 },
      });
    await ask(page, "is Paris or Lisbon warmer right now?");
    await push(page, "conversation", CONVERSATION);
    await reply(page, 1, {
      think: "Two places, so two lookups.",
      say: "I'll check both.",
      called: "Weather",
    });
    await weather(0, "Paris");
    // The second reply calls at once, saying nothing: its card joins the first.
    await reply(page, 2, { called: "Weather" });
    await weather(1, "Lisbon");
    await reply(page, 3, { say: "Lisbon." });
    await push(page, "done", { ...DONE, tool_calls: 2 });
    // The last event of every stream: the whole run, step by step.
    const pass = (tokens_read: number, tokens_out: number) => ({
      via: "model",
      ok: true,
      error: null,
      tokens_in: 667,
      tokens_read,
      tokens_out,
    });
    const tool = {
      kind: "tool",
      name: "weather",
      via: "model",
      ok: true,
      error: null,
      tokens_in: null,
      tokens_read: null,
      tokens_out: null,
      ms: 400,
    };
    await push(page, "run", {
      id: "req-1",
      outcome: "answered",
      steps: 5,
      passes: 3,
      total_ms: 9800,
      waiting_ms: 0,
      reading_ms: 5200,
      writing_ms: 3800,
      tools_ms: 800,
      wall_ms: 9850,
      ledger: [
        { kind: "call", name: "weather", ms: 3100, ...pass(18, 12) },
        tool,
        { kind: "call", name: "weather", ms: 2600, ...pass(36, 5) },
        tool,
        { kind: "answer", name: null, ms: 3300, ...pass(61, 4) },
      ],
    });
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");

    expect(await order(page)).toEqual(["thought", "text", "cards", "text"]);
    await expect(answerOf(page).locator('[data-part="cards"] > figure')).toHaveCount(2);
    await expect(answerOf(page).locator(".prose").last()).toHaveText("Lisbon.");
    // What it cost step by step is there when asked for, and not before.
    const ledger = answerOf(page).locator('[data-part="ledger"]');
    await expect(ledger).toHaveCount(0);
    const toggle = answerOf(page).locator('[data-act="ledger"]');
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    const rows = ledger.locator("li");
    await expect(rows).toHaveCount(5);
    await expect(rows.first()).toContainText("chose");
    await expect(rows.first()).toContainText("Weather");
    await expect(rows.first()).toContainText("read 18 of 667 · wrote 12");
    await expect(rows.nth(1)).toContainText("tool");
    await expect(rows.nth(4)).toContainText("answer");
    await expect(ledger).toContainText("run 9.80s · waiting 0ms · reading 5.20s");
    await toggle.click();
    await expect(ledger).toHaveCount(0);
    await expect(notice(page)).toHaveCount(0);
  });

  test("the line says when the model reads, thinks and writes, and the thought folds away", async ({
    page,
  }) => {
    await ask(page, "is Paris or Lisbon warmer right now?");
    await push(page, "conversation", CONVERSATION);
    // It waits for the engine once, for all its replies, before its first step.
    await push(page, "queue", { position: 1, first: true });
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "queued");
    await expect(notice(page)).toHaveCount(1);

    await push(page, "step", {
      id: "reply-1",
      kind: "model",
      status: "running",
      label: "Thinking",
    });
    await expect(notice(page)).toHaveCount(0);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "working");
    await push(page, "start", START);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "reading");

    await push(page, "think", { t: "Lisbon is further ", ttft_ms: 412 });
    await push(page, "think", { t: "south." });
    const line = page.locator("#status");
    await expect(line).toHaveAttribute("data-phase", "thinking");
    await expect(line.locator("#b-state")).toContainText("thinking");
    // The figures come up with the first token, thought or said.
    await expect(line.locator(".st-tps")).toBeVisible();
    await expect(line.locator("#ttft-value")).toHaveText("412ms");
    // Folded away, saying so quietly; what it thinks is there to be opened.
    const thought = answerOf(page).locator('[data-part="thought"]');
    await expect(thought).toHaveCount(1);
    await expect(thought.locator("summary")).toHaveText("Thinking…");
    await expect(thought).not.toHaveAttribute("open", "");
    await expect(answerOf(page).locator(".prose")).toHaveCount(0);

    await push(page, "token", { t: "Lisbon, " });
    await push(page, "token", { t: "probably." });
    await expect(line).toHaveAttribute("data-phase", "writing");
    await expect(thought.locator("summary")).toHaveText(/^Thought for \d+ s$/);
    await expect(answerOf(page).locator(".prose")).toContainText("Lisbon, probably.");
    expect(await order(page)).toEqual(["thought", "text"]);

    await thought.locator("summary").click();
    await expect(thought).toHaveAttribute("open", "");
    await expect(thought.locator("summary + div")).toHaveText("Lisbon is further south.");
    await push(page, "done", DONE);
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await expect(thought.locator("summary")).toHaveText(/^Thought for \d+ s$/);
    await expect(thought.locator("summary + div")).toHaveText("Lisbon is further south.");
    // What it said is set apart from what it thought.
    await expect(answerOf(page).locator(".prose")).toHaveText("Lisbon, probably.");
  });

  test("a reply that only thinks before it is stopped keeps its thought", async ({ page }) => {
    await ask(page, "think about it");
    await push(page, "conversation", CONVERSATION);
    await push(page, "step", {
      id: "reply-1",
      kind: "model",
      status: "running",
      label: "Thinking",
    });
    await push(page, "start", START);
    await push(page, "think", { t: "Where to begin.", ttft_ms: 80 });
    await expect(answerOf(page).locator('[data-part="thought"]')).toHaveCount(1);
    await push(page, "step", {
      id: "reply-1",
      kind: "model",
      status: "stopped",
      label: "Thinking",
    });
    await push(page, "done", { ...DONE, cancelled: true });
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    const thought = answerOf(page).locator('[data-part="thought"]');
    await expect(thought.locator("summary")).toHaveText(/^Thought for \d+ s$/);
    await expect(answerOf(page).locator('[data-part="cost"]')).toContainText("stopped");
  });

  test.describe("a change put to you after reading the web", () => {
    /** Answers to POST /api/confirm, kept as they were sent. */
    async function answers(page: Page, status = 200) {
      const sent: Array<{ url: string; body: unknown }> = [];
      await page.route("**/api/confirm/**", async (route) => {
        const request = route.request();
        sent.push({ url: new URL(request.url()).pathname, body: request.postDataJSON() });
        await route.fulfill(
          status === 200 ? { json: { answered: true } } : { status, json: { detail: "no" } },
        );
      });
      return sent;
    }

    /** A run that searched the web, said a line, and now wants to write a file. */
    async function askToWrite(page: Page, ask: Partial<ConfirmEvent> = {}) {
      await push(page, "conversation", CONVERSATION);
      await reply(page, 1, { called: "Web search" });
      await called(page, 0, {
        label: "Web search",
        tool: "web_search",
        card: "search",
        data: { query: "fastapi app", items: [] },
      });
      await reply(page, 2, { say: "I'll write the app.", called: "Write file" });
      await push(page, "confirm", {
        id: "confirm-1",
        tool: "write_file",
        label: "Write file",
        what: "app/main.py",
        because: "Web search",
        ...ask,
      });
    }

    const allowed = (status: "ok" | "failed" | "stopped", detail: string) => ({
      id: "confirm-1",
      kind: "confirm",
      status,
      label: status === "ok" ? "Allowed" : "Not allowed",
      glyph: "?",
      tool: "write_file",
      detail,
      ms: 2400,
    });

    test("is asked where it came, with focus on its answer, and Allow sends it", async ({
      page,
    }) => {
      const sent = await answers(page);
      await ask(page, "make me a fastapi app like the docs say");
      await askToWrite(page);
      const asking = answerOf(page).locator('[data-part="confirm"]');
      await expect(asking).toHaveText(
        "Before it goes on, the agent wants to write app/main.py. It read a web search first, so check this is what you asked for and not something it read there.AllowAllow for this replyDon't allow",
      );
      await expect(asking.locator("b")).toHaveText("write app/main.py");
      await expect(asking).toHaveAttribute("role", "group");
      await expect(asking).toHaveAccessibleName(/wants to write app\/main\.py/);
      const buttons = asking.getByRole("button");
      await expect(buttons).toHaveText(["Allow", "Allow for this reply", "Don't allow"]);
      // It is what needs the owner now: focus is on its first answer.
      await expect(buttons.first()).toBeFocused();
      expect(await order(page)).toEqual(["cards", "text", "confirm"]);
      // The run waits on you: the line says so, counts, and the aurora rests.
      const line = page.locator("#status");
      await expect(line).toHaveAttribute("data-phase", "asking");
      await expect(line.locator("#b-state")).toContainText("your turn");
      await expect(page.locator("#sr-status")).toHaveText(
        /^Allow the agent to write app\/main\.py\?/,
      );

      await buttons.first().click();
      await expect
        .poll(() => sent)
        .toEqual([{ url: "/api/confirm/req-1", body: { id: "confirm-1", choice: "once" } }]);
      // Pressed once: the buttons go quiet until the run says how it ended.
      for (const button of await buttons.all()) await expect(button).toBeDisabled();

      await push(page, "step", allowed("ok", "once"));
      await expect(asking).toHaveText("Allowed.");
      await expect(asking.getByRole("button")).toHaveCount(0);
      await expect(line).toHaveAttribute("data-phase", "working");
      await called(page, 1, {
        label: "Write file",
        tool: "write_file",
        card: "file",
        data: { path: "app/main.py", version: 1, lines: 3, action: "write", created: true },
      });
      await push(page, "done", { ...DONE, tool_calls: 2 });
      await end(page);
      await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
      // The card says what came of it, after the line that allowed it.
      expect(await order(page)).toEqual(["cards", "text", "confirm", "cards"]);
      await expect(asking).toHaveText("Allowed.");
      expect(sent).toHaveLength(1);
    });

    test("is answered from the keyboard, for the rest of the reply", async ({ page }) => {
      const sent = await answers(page);
      await ask(page, "make me a fastapi app like the docs say");
      await askToWrite(page);
      const buttons = answerOf(page).locator('[data-part="confirm"]').getByRole("button");
      await expect(buttons.first()).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(buttons.nth(1)).toBeFocused();
      await page.keyboard.press("Enter");
      await expect
        .poll(() => sent.map((s) => s.body))
        .toEqual([{ id: "confirm-1", choice: "reply" }]);
      await push(page, "step", allowed("ok", "reply"));
      await expect(answerOf(page).locator('[data-part="confirm"]')).toHaveText(
        "Allowed for this reply.",
      );
    });

    test("Don't allow is sent, and the call that was not made says so", async ({ page }) => {
      const sent = await answers(page);
      await ask(page, "make me a fastapi app like the docs say");
      await askToWrite(page);
      const asking = answerOf(page).locator('[data-part="confirm"]');
      await asking.getByRole("button", { name: "Don't allow" }).click();
      await expect.poll(() => sent.map((s) => s.body)).toEqual([{ id: "confirm-1", choice: "no" }]);
      await push(page, "step", allowed("failed", "no"));
      await expect(asking).toHaveText("Not allowed.");
      // Said as what you chose, not as a failure.
      await expect(page.locator("#sr-status")).toHaveText("Not allowed.");
      await push(page, "step", {
        id: "tool-1",
        kind: "tool",
        status: "running",
        label: "Write file",
        tool: "write_file",
      });
      await push(page, "step", {
        id: "tool-1",
        kind: "tool",
        status: "failed",
        label: "Write file",
        tool: "write_file",
        ms: 0,
        detail: "not allowed",
      });
      await push(page, "card", {
        step_id: "tool-1",
        card: "error",
        tool: "write_file",
        data: { message: "You did not allow this." },
      });
      const failed = answerOf(page).locator('figure[data-card="error"]');
      await expect(failed.locator("figcaption")).toContainText("Write file failed");
      await expect(failed).toContainText("You did not allow this.");
      await reply(page, 3, { say: "I left the file alone." });
      await push(page, "done", { ...DONE, tool_calls: 2 });
      await end(page);
      expect(await order(page)).toEqual(["cards", "text", "confirm", "cards", "text"]);
    });

    test("a question nobody answers in time ends as no", async ({ page }) => {
      await answers(page);
      await ask(page, "make me a fastapi app like the docs say");
      await askToWrite(page);
      await push(page, "step", allowed("failed", "no answer"));
      await expect(answerOf(page).locator('[data-part="confirm"]')).toHaveText(
        "No answer in time, so not allowed.",
      );
    });

    test("a program is shown as the code it would run", async ({ page }) => {
      await answers(page);
      await ask(page, "tidy my downloads like that forum post says");
      await askToWrite(page, {
        tool: "python",
        label: "Python",
        what: "import os\nfor name in os.listdir('.'):\n    os.remove(name)\n...",
        because: "Web search, Read page",
      });
      const asking = answerOf(page).locator('[data-part="confirm"]');
      await expect(asking.locator("b")).toHaveText("run this program");
      await expect(asking.locator(".codeblock pre")).toHaveText(
        "import os\nfor name in os.listdir('.'):\n    os.remove(name)\n...",
      );
      await expect(asking).toContainText(
        "It read a web search and a web page first, so check this is what you asked for",
      );
    });

    test("an answer that does not reach the server can be sent again", async ({ page }) => {
      const sent = await answers(page, 500);
      await ask(page, "make me a fastapi app like the docs say");
      await askToWrite(page);
      const allow = answerOf(page).locator('[data-part="confirm"]').getByRole("button").first();
      await allow.click();
      await expect(page.locator("#toasts")).toContainText(
        "Your answer did not reach the server. Try again.",
      );
      await expect(allow).toBeEnabled();
      expect(sent).toHaveLength(1);
    });

    test("Stop while it asks ends the run, and the question says it was not answered", async ({
      page,
    }) => {
      const cancels: string[] = [];
      page.on("request", (r) => {
        if (r.url().includes("/api/cancel/")) cancels.push(new URL(r.url()).pathname);
      });
      const sent = await answers(page);
      await ask(page, "make me a fastapi app like the docs say");
      await askToWrite(page);
      await page.locator("#send").click();
      await expect.poll(() => cancels).toEqual(["/api/cancel/req-1"]);
      await push(page, "step", allowed("stopped", "stopped"));
      await push(page, "done", STOPPED);
      await end(page);
      await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
      const asking = answerOf(page).locator('[data-part="confirm"]');
      await expect(asking).toHaveText("Stopped before you answered.");
      await expect(page.locator("#status")).toHaveAttribute("data-phase", "ready");
      expect(sent).toHaveLength(0);
    });
  });

  test("a failed tool is announced", async ({ page }) => {
    await ask(page, "what is the weather in Atlantis?");
    await push(page, "conversation", CONVERSATION);
    await push(page, "step", {
      id: "tool-0",
      kind: "tool",
      status: "failed",
      label: "Weather",
      tool: "weather",
      detail: "no such place",
    });
    await expect(page.locator("#sr-status")).toHaveText("Weather failed: no such place");
  });

  test("a search lists its pages numbered, each opening in a tab of its own", async ({ page }) => {
    await ask(page, "who is the current president of France");
    await push(page, "conversation", CONVERSATION);
    await push(page, "step", {
      id: "tool-0",
      kind: "tool",
      status: "ok",
      label: "Web search",
      tool: "web_search",
      ms: 640,
    });
    await push(page, "card", {
      step_id: "tool-0",
      card: "search",
      tool: "web_search",
      data: {
        query: "current president of France",
        answer: "",
        items: [
          {
            title: "Élysée",
            url: "https://www.elysee.fr/",
            source: "elysee.fr",
            snippet: "The presidency.",
          },
          {
            title: "President of France",
            url: "https://en.wikipedia.org/wiki/President_of_France",
            source: "en.wikipedia.org",
          },
        ],
      },
    });
    const card = answerOf(page).locator('figure[data-card="search"]');
    await expect(card).toBeVisible();
    await expect(card.locator("figcaption")).toContainText("Web search");
    await expect(card.locator("li")).toHaveCount(2);
    await expect(card.locator("li").first()).toContainText("1.");
    const link = card.locator("li a").first();
    await expect(link).toHaveAttribute("href", "https://www.elysee.fr/");
    await expect(link).toHaveAttribute("target", "_blank");
    await answer(page, "He is named on the Élysée site.");
  });

  test("an answer that ends while the tab is hidden is held until it is seen", async ({ page }) => {
    await ask(page, "hello");
    await push(page, "conversation", CONVERSATION);
    await push(page, "start", START);
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await push(page, "token", { t: "Written while away.", ttft_ms: 80 });
    await push(page, "done", DONE);
    await end(page);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "held");
    await expect(page.locator("#b-state")).toContainText("answer ready");
    // Nothing was laid out while nobody could see it, and all of it is there now.
    await expect(answerOf(page).locator(".prose")).toHaveText("Written while away.");

    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "held");
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "ready", {
      timeout: 8000,
    });
  });

  test("Stop while waiting in the queue asks the server to stop the run", async ({ page }) => {
    const cancels: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/cancel/")) cancels.push(new URL(r.url()).pathname);
    });
    await ask(page, "hello");
    await push(page, "conversation", CONVERSATION);
    await push(page, "queue", { position: 1, first: true });
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "queued");

    // The first event named the run, so there is something to stop.
    await page.locator("#send").click();
    await expect.poll(() => cancels).toEqual(["/api/cancel/req-1"]);
    // The connection is left open: the server ends the stream itself.
    expect(await aborted(page)).toBe(false);

    await push(page, "done", STOPPED);
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "ready");
    await expect(page.locator("#sr-status")).toHaveText("Generation stopped.");
    // Nothing was written, so nothing is left but the question.
    await expect(question(page)).toHaveText("hello");
    await expect(answerOf(page)).toHaveCount(0);
    await expect(notice(page)).toHaveCount(0);
  });

  test("Stop while a call is being made takes it off the line", async ({ page }) => {
    const cancels: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/cancel/")) cancels.push(new URL(r.url()).pathname);
    });
    await ask(page, "do I need a coat in Paris");
    await push(page, "conversation", CONVERSATION);
    await reply(page, 1, { called: "Weather" });
    await push(page, "step", {
      id: "tool-0",
      kind: "tool",
      status: "running",
      label: "Weather",
      tool: "weather",
    });
    const calls = answerOf(page).locator('[data-part="calls"]');
    await expect(calls).toContainText("Weather");

    await page.locator("#send").click();
    await expect.poll(() => cancels).toEqual(["/api/cancel/req-1"]);
    await push(page, "step", {
      id: "tool-0",
      kind: "tool",
      status: "stopped",
      label: "Weather",
      tool: "weather",
    });
    await expect(calls).toHaveCount(0);
    await push(page, "done", STOPPED);
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    // It said nothing and found nothing: nothing is left but the question.
    await expect(answerOf(page)).toHaveCount(0);
  });

  test("leaving before the stream has said anything closes the connection", async ({ page }) => {
    await ask(page, "hello");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "stop");
    // No event has arrived, so the run has no name yet: Stop does nothing.
    await page.locator("#send").click();
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "stop");
    expect(await aborted(page)).toBe(false);

    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "About" })
      .click();
    await expect.poll(() => aborted(page)).toBe(true);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "ready");

    // The question is still on the sheet, with no answer and no error.
    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "Chat" })
      .click();
    await expect(question(page)).toHaveText("hello");
    await expect(answerOf(page)).toHaveCount(0);
    await expect(notice(page)).toHaveCount(0);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
  });

  test("the conversation is still on the sheet after a visit to another page", async ({ page }) => {
    await ask(page, "what is a token?");
    await answer(page, "A piece of a word.");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    const nav = page.getByRole("navigation", { name: "Primary" });
    await nav.getByRole("link", { name: "Benchmark" }).click();
    await expect(page).toHaveURL(/\/bench$/);
    await nav.getByRole("link", { name: "Chat" }).click();
    await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
    await expect(question(page)).toHaveText("what is a token?");
    await expect(answerOf(page).locator(".prose")).toHaveText("A piece of a word.");
    await expect(answerOf(page).locator('[data-part="cost"]')).toContainText("11.7 tok/s");
    await expect(page.locator("#ctx-value")).toHaveText("68");
    await expect(page.locator("#input")).toBeFocused();
    // The next question carries on the same conversation.
    await ask(page, "and a context?");
    await expect
      .poll(async () => (await posts(page)).at(-1))
      .toMatchObject({
        message: "and a context?",
        conversation_id: CONVERSATION.id,
      });
  });

  test("Chat, clicked while on Chat, starts a new conversation", async ({ page }) => {
    await ask(page, "what is a token?");
    await answer(page, "A piece of a word.");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "Chat" })
      .click();
    await expect(page.locator("#main")).toHaveAttribute("data-view", "empty");
    await expect(question(page)).toHaveCount(0);
    await expect(page.locator("#ctx-value")).toHaveText("0");
    await expect(page.locator("#input")).toBeFocused();
    await ask(page, "fresh");
    await expect
      .poll(async () => (await posts(page)).at(-1))
      .toMatchObject({
        message: "fresh",
        conversation_id: null,
      });
  });
});
