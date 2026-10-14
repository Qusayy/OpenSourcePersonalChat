import { expect, type Page, type Request, test } from "@playwright/test";
import { ANCHORS } from "../../src/cover/pattern";
import { APP, CHAIN } from "./playwright.config";

/* The cover, and the gate behind it, through a real browser and the real app.

   A visitor is given a sky. Nothing on it says there is anything else, and
   nothing the browser is sent has anything of the site in it. Joining the
   right stars in the right order opens the page that was asked for. */

/** Every request the page makes to the unlock address, as it makes them. */
function watchUnlock(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/unlock") seen.push(request);
  });
  return seen;
}

/** Whatever the page writes to the console as an error, and any it throws. */
function watchErrors(page: Page): string[] {
  const seen: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") seen.push(message.text());
  });
  page.on("pageerror", (error) => seen.push(error.message));
  return seen;
}

async function sky(page: Page): Promise<void> {
  await expect(page.locator("#sky[data-ready]")).toBeVisible();
}

/** Tap the stars of a chain one after another, as a mouse would click them. */
async function tap(page: Page, chain: readonly number[]): Promise<void> {
  const size = page.viewportSize();
  if (!size) throw new Error("the page has no viewport");
  const unit = Math.min(size.width, size.height);
  for (const star of chain) {
    const anchor = ANCHORS[star];
    if (!anchor) throw new Error(`no star ${star}`);
    await page.mouse.click(size.width / 2 + anchor.x * unit, size.height / 2 - anchor.y * unit);
  }
}

for (const path of ["/", "/bench", "/no/such/page"]) {
  test(`${path} is the sky, and nothing of the site`, async ({ page, request }) => {
    const errors = watchErrors(page);
    const response = await page.goto(path);
    expect(response?.status()).toBe(200);
    await sky(page);
    await expect(page).toHaveTitle("Aurora");
    await expect(page.locator("#readout")).toContainText("·");
    // No application frame, none of its data, none of its code.
    await expect(page.locator("#root")).toHaveCount(0);
    await expect(page.locator("#bootstrap")).toHaveCount(0);
    const scripts = await page
      .locator("script[src]")
      .evaluateAll((all) =>
        all.map((script) => new URL((script as HTMLScriptElement).src).pathname),
      );
    expect(scripts).toHaveLength(1);
    expect(scripts[0]).toMatch(/^\/assets\/cover-/);
    expect(errors).toEqual([]);

    // Asked from outside the page, so that a 401 is not itself a console error.
    expect((await request.get(`${APP}/api/conversations`)).status()).toBe(401);
    expect((await request.get(`${APP}/api/health`)).status()).toBe(401);
    expect((await request.get(`${APP}/openapi.json`)).status()).toBe(404);
  });
}

test("a drawing too short to be a pattern never leaves the browser", async ({ page }) => {
  const asked = watchUnlock(page);
  await page.goto("/");
  await sky(page);
  await tap(page, CHAIN.slice(0, 5));
  await page.waitForTimeout(1200);
  expect(asked).toHaveLength(0);
});

test("a wrong pattern is asked about once, and nothing happens", async ({ page }) => {
  const asked = watchUnlock(page);
  const errors = watchErrors(page);
  await page.goto("/");
  await sky(page);
  const answer = page.waitForResponse((res) => new URL(res.url()).pathname === "/api/unlock");
  await tap(page, [...CHAIN].reverse());
  const response = await answer;
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual({ ok: false });
  // Tapped in star by star, and still one question: not one for each star
  // past the seventh.
  expect(asked).toHaveLength(1);
  await page.waitForTimeout(900);
  await sky(page);
  await expect(page.locator("html")).not.toHaveAttribute("data-opening", "");
  expect(errors).toEqual([]);
});

test("the right pattern opens the page that was asked for", async ({ page, browser }) => {
  const asked = watchUnlock(page);
  await page.goto("/bench#memory");
  await sky(page);
  await tap(page, CHAIN);
  await expect(page.locator('html[data-page="bench"]')).toBeAttached({ timeout: 15_000 });
  await expect(page.locator("#root")).toBeAttached();
  expect(new URL(page.url()).pathname + new URL(page.url()).hash).toBe("/bench#memory");
  expect(asked).toHaveLength(1);
  // And stays open as the visitor moves about and comes back.
  await page.goto("/");
  await expect(page.locator('html[data-page="chat"]')).toBeAttached();
  expect(await page.evaluate(async () => (await fetch("/api/conversations")).status)).toBe(200);

  // Someone else, in a browser of their own, is still given the sky.
  const other = await browser.newContext();
  const stranger = await other.newPage();
  await stranger.goto(page.url());
  await sky(stranger);
  await expect(stranger.locator("#root")).toHaveCount(0);
  await other.close();
});
