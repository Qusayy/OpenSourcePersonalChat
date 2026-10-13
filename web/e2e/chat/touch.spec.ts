import { expect, test } from "@playwright/test";

/* On a touch screen, focus in a text field raises the keyboard. Loading the
   chat page puts focus in the field, as it always has. Coming to it by a tap
   on a link must not: the keyboard would cover the page that was just asked
   for. Focus goes to the page's content instead, like any change of page. */

test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });

test("coming to Chat by a link does not put focus in the field", async ({ page }) => {
  await page.goto("/about");
  test.skip(
    !(await page.evaluate(() => window.matchMedia("(pointer: coarse)").matches)),
    "this browser does not report a coarse pointer under touch emulation",
  );
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Chat" }).tap();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator("#input")).toBeVisible();
  await expect(page.locator("#input")).not.toBeFocused();
  await expect(page.locator("#main")).toBeFocused();
});

test("loading Chat still puts focus in the field", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#input")).toBeFocused();
});
