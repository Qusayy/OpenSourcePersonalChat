import { expect, test } from "@playwright/test";
import { reseed } from "../support";
import { answer, ask, fakeChat, question } from "./helpers";

/* The history drawer: a native dialog listing the stored conversations, from
   which one is opened or deleted. */

test.beforeEach(async ({ page }) => {
  reseed();
  await fakeChat(page);
  await page.goto("/");
});

const rail = (page: import("@playwright/test").Page) => page.locator("#rail");
const rows = (page: import("@playwright/test").Page) => page.locator("#convo-list [data-id]");

test("opens as a modal dialog, lists the conversations, and says so on its button", async ({
  page,
}) => {
  const button = page.locator("#menu-btn");
  await expect(button).toHaveAttribute("aria-haspopup", "dialog");
  await expect(button).toHaveAttribute("aria-expanded", "false");
  await expect(rail(page)).toBeHidden();

  await button.click();
  await expect(rail(page)).toBeVisible();
  await expect(rail(page)).toHaveAccessibleName("History");
  await expect(button).toHaveAttribute("aria-expanded", "true");
  expect(await rail(page).evaluate((el: HTMLDialogElement) => el.matches(":modal"))).toBe(true);

  await expect(rows(page)).toHaveCount(3);
  await expect(rows(page).first()).toContainText("Show me every figure");
  await expect(rows(page).first()).toContainText(/2 messages · (just now|\d+ min ago)/);
  await expect(rows(page).nth(1)).toContainText(/2 messages · \d+ (min|h) ago/);
  await expect(rows(page).nth(2)).toContainText(/2 messages · \d+ (h|d) ago/);
  // Each row is two controls side by side, never one inside the other.
  await expect(
    page.getByRole("button", { name: /^Delete conversation: Show me every figure/ }),
  ).toBeVisible();
});

test("keeps focus inside, and Escape gives it back to the button", async ({ page }) => {
  await page.locator("#menu-btn").click();
  await expect(rail(page)).toBeVisible();
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    const inside = await page.evaluate(() => {
      const active = document.activeElement;
      return active === document.body || Boolean(active?.closest("#rail"));
    });
    expect(inside).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(rail(page)).toBeHidden();
  await expect(page.locator("#menu-btn")).toBeFocused();
  await expect(page.locator("#menu-btn")).toHaveAttribute("aria-expanded", "false");
});

test("Close and a click on the backdrop both close it", async ({ page }) => {
  await page.locator("#menu-btn").click();
  await page.locator("#rail-close").click();
  await expect(rail(page)).toBeHidden();

  await page.locator("#menu-btn").click();
  await expect(rail(page)).toBeVisible();
  // The drawer is on the right; the far left of the window is backdrop.
  await page.mouse.click(20, 300);
  await expect(rail(page)).toBeHidden();
  await expect(page.locator("#menu-btn")).toHaveAttribute("aria-expanded", "false");
});

test("its rows arrive as a list, once per opening", async ({ page }) => {
  // Hold the fresh list back a moment, as a slow server would.
  await page.route("**/api/conversations", async (route) => {
    await new Promise((done) => setTimeout(done, 150));
    await route.continue();
  });
  const list = page.locator("#convo-list");
  await page.locator("#menu-btn").click();
  // Held unseen while the fresh list is fetched, then staggered in.
  await expect(list).toHaveClass(/pending/);
  await expect(list).toHaveClass(/stagger/);
  await expect(list).not.toHaveClass(/pending/);
  // And then left alone: the class that staggers them is taken off again.
  await expect(list).not.toHaveClass(/stagger/, { timeout: 3000 });
  expect(
    await rows(page)
      .first()
      .evaluate((el) => getComputedStyle(el.parentElement as Element).opacity),
  ).toBe("1");
});

test("opening a conversation draws it and closes the drawer", async ({ page }) => {
  await page.locator("#menu-btn").click();
  await rows(page).nth(2).click();
  await expect(rail(page)).toBeHidden();
  await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
  await expect(question(page)).toHaveText("ما هو النموذج اللغوي؟");

  // Open again: the one on the sheet is marked as the current one.
  await page.locator("#menu-btn").click();
  await expect(rows(page).nth(2)).toHaveAttribute("aria-current", "true");
  await expect(rows(page).first()).not.toHaveAttribute("aria-current", "true");

  // Another one replaces it; nothing of the first is left.
  await rows(page).first().click();
  await expect(question(page)).toHaveText("Show me every figure a tool can draw.");
  await expect(question(page)).toHaveCount(1);
});

test("the list is fetched again each time the drawer opens, and after an answer", async ({
  page,
}) => {
  let fetched = 0;
  page.on("request", (r) => {
    if (r.method() === "GET" && r.url().endsWith("/api/conversations")) fetched += 1;
  });
  await page.locator("#menu-btn").click();
  await expect.poll(() => fetched).toBe(1);
  await page.keyboard.press("Escape");
  await page.locator("#menu-btn").click();
  await expect.poll(() => fetched).toBe(2);
  await page.keyboard.press("Escape");
  // A finished answer may have named or reordered a conversation.
  await ask(page, "what is a token?");
  await answer(page, "A piece of a word.");
  await expect(page.locator("#send")).toHaveAttribute("data-mode", "send");
  await expect.poll(() => fetched).toBe(3);
});

test("deleting a conversation removes its row and says so", async ({ page }) => {
  await page.locator("#menu-btn").click();
  await expect(rows(page)).toHaveCount(3);
  await page.getByRole("button", { name: /^Delete conversation: I pasted the notes/ }).click();
  await expect(rows(page)).toHaveCount(2);
  await expect(page.locator("#sr-status")).toHaveText(/^Deleted conversation I pasted the notes/);
  await expect(rail(page)).toBeVisible();
  // It is gone from the server too.
  await page.reload();
  await page.locator("#menu-btn").click();
  await expect(rows(page)).toHaveCount(2);
});

test("deleting the conversation on the sheet clears the sheet", async ({ page }) => {
  await page.locator("#menu-btn").click();
  await rows(page).first().click();
  await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
  await page.locator("#menu-btn").click();
  await page.getByRole("button", { name: /^Delete conversation: Show me every figure/ }).click();
  await expect(page.locator("#main")).toHaveAttribute("data-view", "empty");
  await expect(question(page)).toHaveCount(0);
});

test("with nothing stored it says so", async ({ page }) => {
  await page.locator("#menu-btn").click();
  for (let left = 3; left > 0; left--) {
    await expect(rows(page)).toHaveCount(left);
    await page
      .getByRole("button", { name: /^Delete conversation: / })
      .first()
      .click();
  }
  await expect(page.locator("#convo-list")).toHaveText("No conversations yet.");
});

test("at phone width the page links are in the drawer, not the top bar", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeHidden();
  await page.locator("#menu-btn").click();
  const pages = rail(page).getByRole("navigation", { name: "Pages" });
  await expect(pages).toBeVisible();
  await pages.getByRole("link", { name: "About" }).click();
  await expect(page).toHaveURL(/\/about$/);
  await expect(page.locator("#rail")).toHaveCount(0);
});
