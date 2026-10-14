import { expect, test } from "@playwright/test";
import { reseed } from "../support";
import {
  aborted,
  answer,
  answerOf,
  ask,
  CONVERSATION,
  DONE,
  end,
  fakeChat,
  notice,
  posts,
  push,
  question,
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
      skill: null,
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

  test("the route line shows a step while it runs and keeps only the decision", async ({
    page,
  }) => {
    await ask(page, "what is the weather in Paris?");
    await push(page, "conversation", CONVERSATION);
    await push(page, "step", {
      id: "route",
      kind: "route",
      status: "running",
      label: "Choosing a tool",
    });
    const route = answerOf(page).locator('[data-part="route"]');
    await expect(route).toHaveText("Choosing a toolrunning");
    await expect(route.locator('[data-status="running"]')).toHaveCount(1);

    await push(page, "step", {
      id: "route",
      kind: "route",
      status: "ok",
      label: "Weather",
      detail: "model",
      tool: "weather",
      ms: 2140,
    });
    await expect(route).toHaveText("Model's choice2.14s");

    await push(page, "step", {
      id: "tool-0",
      kind: "tool",
      status: "running",
      label: "Weather",
      tool: "weather",
    });
    await expect(route.locator("[data-id]")).toHaveCount(2);
    await expect(route).toContainText("Weatherrunning");
    // A finished tool is named and timed by its own figure, so its step goes.
    await push(page, "step", {
      id: "tool-0",
      kind: "tool",
      status: "ok",
      label: "Weather",
      tool: "weather",
      ms: 412,
    });
    await expect(route.locator("[data-id]")).toHaveCount(1);
    await push(page, "card", {
      step_id: "tool-0",
      card: "weather",
      tool: "weather",
      data: { place: "Paris", temperature: 12 },
    });
    await expect(answerOf(page).locator('[data-part="cards"] > *')).toHaveCount(1);

    // The answer step is never drawn here: the status line carries it.
    await push(page, "step", { id: "answer", kind: "model", status: "running", label: "Writing" });
    await expect(route.locator("[data-id]")).toHaveCount(1);
    await answer(page, "It is mild.");
    await expect(route).toHaveText("Model's choice2.14s");
  });

  test("each choice of a run is a row of its own in the route line", async ({ page }) => {
    const choosing = (id: string, pass: number) =>
      push(page, "step", {
        id,
        kind: "route",
        status: "running",
        label: "Choosing a tool",
        pass,
      });
    const lookedUp = async (index: number, place: string) => {
      const id = `tool-${index}`;
      await push(page, "step", {
        id,
        kind: "tool",
        status: "running",
        label: "Weather",
        tool: "weather",
      });
      await push(page, "step", {
        id,
        kind: "tool",
        status: "ok",
        label: "Weather",
        tool: "weather",
        ms: 400,
      });
      await push(page, "card", {
        step_id: id,
        card: "weather",
        tool: "weather",
        data: { place, temperature: 12 },
      });
    };
    await ask(page, "is Paris or Lisbon warmer right now?");
    await push(page, "conversation", CONVERSATION);
    const route = answerOf(page).locator('[data-part="route"]');

    await choosing("route", 1);
    await expect(route).toHaveText("Choosing a toolrunning");
    await push(page, "step", {
      id: "route",
      kind: "route",
      status: "ok",
      label: "Weather",
      detail: "model",
      tool: "weather",
      ms: 3100,
      pass: 1,
    });
    await lookedUp(0, "Paris");

    // The second choice is being made: the first stays, a second row runs.
    await choosing("route-2", 2);
    await expect(route).toHaveText("Model's choice3.10sChoosing a toolrunning");
    await push(page, "step", {
      id: "route-2",
      kind: "route",
      status: "ok",
      label: "Weather",
      detail: "model",
      tool: "weather",
      ms: 2600,
      pass: 2,
    });
    await lookedUp(1, "Lisbon");

    await choosing("route-3", 3);
    await push(page, "step", {
      id: "route-3",
      kind: "route",
      status: "ok",
      label: "No tool needed",
      detail: "model",
      tool: null,
      ms: 2200,
      pass: 3,
    });
    // Three passes were paid for, and each is there with what it cost.
    await expect(route).toHaveText("Model's choice3.10sModel's choice2.60sNo tool needed2.20s");
    await expect(route.locator("[data-id]")).toHaveCount(3);
    await expect(answerOf(page).locator('[data-part="cards"] > *')).toHaveCount(2);

    await push(page, "start", START);
    await push(page, "token", { t: "Lisbon.", ttft_ms: 86.4 });
    await push(page, "done", { ...DONE, tool_calls: 2 });
    // The last event of every stream. The page has no use for it yet.
    await push(page, "run", {
      id: "req-1",
      outcome: "answered",
      steps: 6,
      passes: 4,
      total_ms: 9800,
      waiting_ms: 0,
      reading_ms: 5200,
      writing_ms: 3800,
      tools_ms: 800,
      wall_ms: 9850,
    });
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await expect(route).toHaveText("Model's choice3.10sModel's choice2.60sNo tool needed2.20s");
    await expect(answerOf(page).locator(".prose")).toHaveText("Lisbon.");
    await expect(notice(page)).toHaveCount(0);
  });

  test("a run that waits before its first choice stops saying so once it chooses", async ({
    page,
  }) => {
    await ask(page, "is Paris or Lisbon warmer right now?");
    await push(page, "conversation", CONVERSATION);
    // It waits for the engine once, for all its passes, before its first step.
    await push(page, "queue", { position: 1, first: true });
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "queued");
    await expect(notice(page)).toHaveCount(1);

    await push(page, "step", {
      id: "route",
      kind: "route",
      status: "running",
      label: "Choosing a tool",
      pass: 1,
    });
    await expect(notice(page)).toHaveCount(0);
    await expect(page.locator("#status")).toHaveAttribute("data-phase", "working");
    await expect(answerOf(page).locator('[data-part="route"]')).toHaveText(
      "Choosing a toolrunning",
    );

    await push(page, "step", {
      id: "route",
      kind: "route",
      status: "ok",
      label: "No tool needed",
      detail: "model",
      tool: null,
      ms: 2200,
      pass: 1,
    });
    await answer(page, "Lisbon, probably.");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await expect(notice(page)).toHaveCount(0);
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

  test("Stop while a tool is being chosen takes the step off the line", async ({ page }) => {
    const cancels: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/cancel/")) cancels.push(new URL(r.url()).pathname);
    });
    await ask(page, "do I need a coat in Paris");
    await push(page, "conversation", CONVERSATION);
    await push(page, "step", {
      id: "route",
      kind: "route",
      status: "running",
      label: "Choosing a tool",
    });
    const route = answerOf(page).locator('[data-part="route"]');
    await expect(route).toContainText("Choosing a tool");

    await page.locator("#send").click();
    await expect.poll(() => cancels).toEqual(["/api/cancel/req-1"]);
    await push(page, "step", {
      id: "route",
      kind: "route",
      status: "stopped",
      label: "Choosing a tool",
      ms: 310,
    });
    await expect(route.locator("[data-id]")).toHaveCount(0);
    await push(page, "done", STOPPED);
    await end(page);
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
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
