import { expect, test } from "@playwright/test";
import { reseed } from "../support";
import {
  answer,
  answerOf,
  ask,
  CONVERSATION,
  fakeChat,
  push,
  question,
  START,
  transitions,
} from "./helpers";

/* Motion, and its absence. Reduced motion is not "no motion": what is removed
   is travel and looping, and removing the motion never removes the content. */

test.beforeEach(async ({ page }) => {
  reseed();
  await fakeChat(page);
});

/**
 * How many animations are running on or inside an element: keyframes and
 * scripted ones, the things that travel. A transition is not counted. Those
 * are the colour changes that acknowledge a pointer or a focus, and reduced
 * motion keeps them.
 */
const moving = (page: import("@playwright/test").Page, selector: string) =>
  page.evaluate(
    (s) =>
      [...document.querySelectorAll(s)].reduce(
        (n, el) =>
          n +
          el.getAnimations({ subtree: true }).filter((a) => !(a instanceof CSSTransition)).length,
        0,
      ),
    selector,
  );

test.describe("with motion", () => {
  test("the sheet assembles once on arrival, and not again on New chat", async ({ page }) => {
    await page.goto("/");
    // Arriving from outside: the page introduces itself.
    await expect(page.locator("html")).toHaveClass(/intro/);
    expect(await moving(page, "#hero, #composer")).toBeGreaterThan(0);
    await expect(page.locator("html")).not.toHaveClass(/intro/, { timeout: 4000 });

    await ask(page, "hello");
    await answer(page, "Hi.");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await page.locator("#new-chat").click();
    await expect(page.locator("#hero")).toBeVisible();
    expect(await moving(page, "#hero")).toBe(0);
  });

  test("the curtains hang over the empty sheet, go with the first question and come back for a new chat", async ({
    page,
  }) => {
    await page.goto("/");
    const band = page.locator("#curtains");
    // Shown once the page has been idle long enough to decide how to draw them.
    await expect(band).toBeVisible({ timeout: 8000 });
    await ask(page, "hello");
    await expect(band).toBeHidden({ timeout: 4000 });
    await answer(page, "Hi.");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
    await page.locator("#new-chat").click();
    await expect(band).toBeVisible({ timeout: 4000 });
  });

  test("a question and its answer arrive from just below", async ({ page }) => {
    await page.goto("/");
    await ask(page, "hello");
    await expect(question(page)).toBeVisible();
    const name = await question(page).evaluate((el) => getComputedStyle(el).animationName);
    expect(name).toBe("msg-in");
  });
});

test.describe("with reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("the sheet is simply there on arrival", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#hero h1")).toBeVisible();
    expect(await moving(page, "#hero, #composer")).toBe(0);
    // The curtains are a still tint, there at once.
    await expect(page.locator("#curtains")).toHaveAttribute("data-mode", "still");
    await expect(page.locator("#curtains")).toBeVisible();
    const opacity = await page.locator("#hero h1").evaluate((el) => getComputedStyle(el).opacity);
    expect(opacity).toBe("1");
  });

  test("the first send changes the view without a transition, and nothing travels", async ({
    page,
  }) => {
    await page.goto("/");
    const before = await transitions(page);
    await ask(page, "hello");
    await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
    expect(await transitions(page)).toBe(before);
    await expect(question(page)).toBeVisible();
    expect(await question(page).evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
    // Send becomes Stop without the word sliding.
    expect(await moving(page, "#send")).toBe(0);

    await push(page, "conversation", CONVERSATION);
    await push(page, "start", START);
    await push(page, "token", { t: "Written ", ttft_ms: 80 });
    await push(page, "token", { t: "plainly." });
    const prose = answerOf(page).locator(".prose");
    await expect(prose).toContainText("Written plainly.");
    // The caret stays lit and stops blinking; new words do not fade.
    await expect(prose.locator(".caret")).toHaveCount(1);
    expect(await prose.locator(".caret").evaluate((el) => getComputedStyle(el).animationName)).toBe(
      "none",
    );
    expect(await moving(page, '[data-part="answer"] .prose')).toBe(0);
    // The aurora is a still tint, not a moving light.
    await expect(page.locator("#aurora")).toHaveAttribute("data-mode", "still");
    await expect(page.locator("#aurora")).toBeVisible();
    // The state word changed without sliding.
    await expect(page.locator("#b-state")).toHaveText("writing");
  });

  test("the history appears in place", async ({ page }) => {
    await page.goto("/");
    await page.locator("#menu-btn").click();
    await expect(page.locator("#rail")).toBeVisible();
    await expect(page.locator("#convo-list [data-id]")).toHaveCount(4);
    await expect(page.locator("#convo-list")).not.toHaveClass(/stagger/);
    expect(await moving(page, "#rail")).toBe(0);
    const translate = await page.locator("#rail").evaluate((el) => getComputedStyle(el).translate);
    expect(["none", "0px", "0px 0px"]).toContain(translate);
  });

  test("a queued request's mark is still, and the line goes when the wait ends", async ({
    page,
  }) => {
    await page.goto("/");
    await ask(page, "hello");
    await push(page, "conversation", CONVERSATION);
    await push(page, "queue", { position: 1, first: true });
    const mark = page.locator('[data-part="notice"] > span').first();
    await expect(mark).toBeVisible();
    expect(await mark.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
    await push(page, "start", START);
    await expect(page.locator('[data-part="notice"]')).toHaveCount(0);
  });
});
