import { expect, test } from "@playwright/test";
import { forward, reseed } from "../support";
import { ask, fakeChat, openStored, posts, question } from "./helpers";

/* The composer: the input, Send, and the two ways to start over. There is
   nothing to choose before sending: no persona, no skill, no "/" palette. */

test.beforeEach(async ({ page }) => {
  reseed();
  await fakeChat(page);
});

test.describe("the input", () => {
  test("Enter sends; Shift and Enter adds a line", async ({ page }) => {
    await page.goto("/");
    const input = page.locator("#input");
    await input.fill("first line");
    await input.press("Shift+Enter");
    await input.pressSequentially("second line");
    await expect(input).toHaveValue("first line\nsecond line");
    expect(await posts(page)).toHaveLength(0);
    await input.press("Enter");
    await expect
      .poll(async () => (await posts(page)).map((p) => p.message))
      .toEqual(["first line\nsecond line"]);
    await expect(input).toHaveValue("");
  });

  test("nothing is sent for an empty or blank message", async ({ page }) => {
    await page.goto("/");
    await page.locator("#input").press("Enter");
    await page.locator("#input").fill("   ");
    await page.locator("#send").click();
    expect(await posts(page)).toHaveLength(0);
    await expect(page.locator("#main")).toHaveAttribute("data-view", "empty");
  });

  test("grows with its text up to 208px, then scrolls, and shrinks back", async ({ page }) => {
    await page.goto("/");
    const input = page.locator("#input");
    const height = () => input.evaluate((el) => el.getBoundingClientRect().height);
    const one = await height();
    await input.fill("one\ntwo\nthree");
    const three = await height();
    expect(three).toBeGreaterThan(one);
    await input.fill(Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n"));
    expect(await height()).toBeCloseTo(208, 1);
    expect(await input.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
    await input.fill("");
    expect(await height()).toBeCloseTo(one, 1);
  });

  test("takes at most 8000 characters and counts down the last 500", async ({ page }) => {
    await page.goto("/");
    const input = page.locator("#input");
    const count = page.locator("#char-count");
    await expect(input).toHaveAttribute("maxlength", "8000");
    await input.fill("a".repeat(7499));
    await expect(count).toBeHidden();
    await input.fill("a".repeat(7500));
    await expect(count).toBeVisible();
    await expect(count).toHaveText(/^500 characters left$/);
    await input.fill("a".repeat(8000));
    await expect(count).toHaveText(/^0 characters left$/);
    // At the ceiling the count takes the fault colour.
    const colours = await page.evaluate(() => {
      const probe = document.createElement("span");
      probe.style.color = "var(--fault)";
      document.body.appendChild(probe);
      const fault = getComputedStyle(probe).color;
      probe.remove();
      const el = document.querySelector("#char-count");
      return { fault, count: el ? getComputedStyle(el).color : "" };
    });
    expect(colours.count).toBe(colours.fault);
    await input.fill("short again");
    await expect(count).toBeHidden();
  });

  test("says why nothing can be sent while the model is not ready", async ({ page }) => {
    let status = "loading";
    await page.route("**/api/health", async (route) => {
      const response = await forward(route);
      const body = await response.json();
      body.engine.status = status;
      await route.fulfill({ response, json: body });
    });
    await page.goto("/");
    await expect(page.locator("#send")).toBeDisabled();
    await expect(page.locator("#input")).toHaveAttribute(
      "placeholder",
      "The model is still loading…",
    );
    status = "error";
    await expect(page.locator("#input")).toHaveAttribute(
      "placeholder",
      "The model could not be loaded — nothing can be sent right now",
      { timeout: 6000 },
    );
    status = "ready";
    await expect(page.locator("#send")).toBeEnabled({ timeout: 6000 });
    await expect(page.locator("#input")).toHaveAttribute("placeholder", "Ask something…");
  });

  test("has a label, and a hint that also says what Enter does", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByLabel(/^Message /)).toHaveAttribute("id", "input");
    await expect(page.locator("#input")).toHaveAttribute("aria-describedby", "input-hint");
    await expect(page.locator("#input-hint")).toHaveText(
      "Shift Enter for a new line. Enter sends.",
    );
  });
});

test.describe("one agent", () => {
  test("a message is sent as it is, with nothing chosen beside it", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#persona-row")).toHaveCount(0);
    await expect(page.locator("#skill-grid")).toHaveCount(0);
    await expect(page.locator("#skill-active")).toHaveCount(0);
    await ask(page, "write a loop");
    const sent = (await posts(page)).at(-1);
    expect(Object.keys(sent ?? {}).sort()).toEqual(["conversation_id", "message"]);
  });

  test("a slash is only a character: no palette opens", async ({ page }) => {
    await page.goto("/");
    await page.locator("#input").fill("/");
    await page.locator("#input").press("End");
    await expect(page.locator("#palette")).toHaveCount(0);
    await expect(page.locator('[role="listbox"]')).toHaveCount(0);
  });
});

test.describe("starting over", () => {
  test("New chat clears the sheet and the context, and the next message starts fresh", async ({
    page,
  }) => {
    await page.goto("/");
    await openStored(page, 1);
    await expect(question(page)).toHaveCount(1);
    await expect(page.locator("#ctx-value")).toHaveText("360");
    await page.locator("#new-chat").click();
    await expect(page.locator("#main")).toHaveAttribute("data-view", "empty");
    await expect(question(page)).toHaveCount(0);
    await expect(page.locator("#ctx-value")).toHaveText("0");
    await expect(page.locator("#input")).toBeFocused();
    await expect(page.locator("#hero")).toBeVisible();
    await ask(page, "fresh");
    await expect
      .poll(async () => (await posts(page)).at(-1))
      .toMatchObject({
        conversation_id: null,
      });
  });

  test("Ctrl+K does the same from the keyboard", async ({ page }) => {
    await page.goto("/");
    await openStored(page, 2);
    await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
    await page.keyboard.press("Control+k");
    await expect(page.locator("#main")).toHaveAttribute("data-view", "empty");
    await expect(page.locator("#input")).toBeFocused();
  });

  test("neither does anything while an answer is being written", async ({ page }) => {
    await page.goto("/");
    await ask(page, "hello");
    await expect(page.locator("#send")).toHaveAttribute("data-mode", "stop");
    await page.locator("#new-chat").click();
    await page.keyboard.press("Control+k");
    await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
    await expect(question(page)).toHaveText("hello");
  });
});
