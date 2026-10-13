import { expect, test } from "@playwright/test";
import { reseed } from "../support";
import { fakeChat, openStored, options, posts } from "./helpers";

/* The "/" palette: typing a slash lists tools (and, in a thread, skills);
   the arrows choose, Tab completes, Enter runs, Escape closes. */

test.beforeEach(async ({ page }) => {
  reseed();
  await fakeChat(page);
  await page.goto("/");
});

const input = (page: import("@playwright/test").Page) => page.locator("#input");
const palette = (page: import("@playwright/test").Page) => page.locator("#palette");
const describedAs = (page: import("@playwright/test").Page) => page.locator("#palette [data-desc]");

test("a slash opens it, with the first command selected and described", async ({ page }) => {
  await expect(palette(page)).toBeHidden();
  await expect(input(page)).toHaveAttribute("aria-expanded", "false");
  await input(page).pressSequentially("/");

  await expect(palette(page)).toBeVisible();
  await expect(palette(page)).toHaveRole("listbox");
  await expect(options(page).first()).toHaveAttribute("aria-selected", "true");
  await expect(options(page).first()).toContainText("/calc");
  await expect(input(page)).toHaveAttribute("aria-expanded", "true");
  await expect(input(page)).toHaveAttribute("aria-controls", "palette");
  await expect(input(page)).toHaveAttribute("aria-activedescendant", "pal-opt-0");
  // What the selected command does is written out in full under the list,
  // and carried by the option itself for a screen reader.
  const text = (await describedAs(page).textContent()) ?? "";
  expect(text.length).toBeGreaterThan(20);
  await expect(options(page).first()).toContainText(text);
});

test("on the empty sheet it offers tools only: the skills are already listed below", async ({
  page,
}) => {
  await input(page).pressSequentially("/");
  await expect(palette(page).locator("[data-group]")).toHaveText(["Tools"]);
  for (const option of await options(page).all()) {
    await expect(option).toContainText("/");
  }
});

test("in a thread it lists skills first, then tools", async ({ page }) => {
  await openStored(page, 2);
  await input(page).pressSequentially("/");
  await expect(palette(page).locator("[data-group]")).toHaveText(["Skills", "Tools"]);
  const skills = await page.locator("#skill-grid button").count();
  expect(skills).toBeGreaterThan(0);
  await expect(options(page).nth(skills)).toContainText("/calc");
  // Above the input here, where there is room; below it on the empty sheet.
  const above = await page.evaluate(() => {
    const p = document.querySelector("#palette")?.getBoundingClientRect();
    const i = document.querySelector("#input")?.getBoundingClientRect();
    return Boolean(p && i && p.bottom <= i.top);
  });
  expect(above).toBe(true);
});

test("the arrows move the selection, round the ends, and the description follows", async ({
  page,
}) => {
  await input(page).pressSequentially("/");
  const count = await options(page).count();
  const first = await describedAs(page).textContent();

  await input(page).press("ArrowDown");
  await expect(options(page).nth(1)).toHaveAttribute("aria-selected", "true");
  await expect(options(page).first()).toHaveAttribute("aria-selected", "false");
  await expect(input(page)).toHaveAttribute("aria-activedescendant", "pal-opt-1");
  await expect(describedAs(page)).not.toHaveText(first ?? "");

  await input(page).press("ArrowUp");
  await input(page).press("ArrowUp");
  await expect(options(page).nth(count - 1)).toHaveAttribute("aria-selected", "true");
  await input(page).press("ArrowDown");
  await expect(options(page).first()).toHaveAttribute("aria-selected", "true");
  // The text caret did not move: the arrows belong to the list while it is open.
  await expect(input(page)).toHaveValue("/");
});

test("typing narrows the list; no match closes it", async ({ page }) => {
  await input(page).pressSequentially("/c");
  const all = await options(page).count();
  await input(page).pressSequentially("alc");
  await expect(options(page)).toHaveCount(1);
  expect(all).toBeGreaterThan(1);
  await input(page).pressSequentially("zzz");
  await expect(palette(page)).toBeHidden();
  await expect(input(page)).toHaveAttribute("aria-expanded", "false");
});

test("Tab completes the command and selects its example arguments; nothing is sent", async ({
  page,
}) => {
  await input(page).pressSequentially("/");
  await input(page).press("Tab");
  await expect(palette(page)).toBeHidden();
  await expect(input(page)).toHaveValue("/calc 1247 * 89");
  await expect(input(page)).toBeFocused();
  const selection = await input(page).evaluate((el: HTMLTextAreaElement) => [
    el.selectionStart,
    el.selectionEnd,
  ]);
  expect(selection).toEqual([6, 15]);
  // Typing replaces the example.
  await input(page).pressSequentially("2 + 2");
  await expect(input(page)).toHaveValue("/calc 2 + 2");
  expect(await posts(page)).toHaveLength(0);
  await expect(page.locator("#main")).toHaveAttribute("data-view", "empty");
});

test("Enter on a tool runs its example at once", async ({ page }) => {
  await input(page).pressSequentially("/");
  await input(page).press("Enter");
  await expect
    .poll(async () => (await posts(page)).map((p) => p.message))
    .toEqual(["/calc 1247 * 89"]);
  await expect(palette(page)).toBeHidden();
  await expect(input(page)).toHaveValue("");
  await expect(page.locator("#main")).toHaveAttribute("data-view", "thread");
});

test("Shift and Enter on a tool completes it without sending", async ({ page }) => {
  await input(page).pressSequentially("/");
  await input(page).press("Shift+Enter");
  await expect(input(page)).toHaveValue("/calc 1247 * 89");
  expect(await posts(page)).toHaveLength(0);
});

test("Enter or Tab on a skill arms it and sends nothing", async ({ page }) => {
  await openStored(page, 2);
  for (const key of ["Enter", "Tab"]) {
    await input(page).pressSequentially("/");
    const name = (await options(page).first().locator("span").nth(1).textContent()) ?? "";
    await input(page).press(key);
    await expect(palette(page)).toBeHidden();
    await expect(input(page)).toHaveValue("");
    await expect(page.locator("#skill-active")).toContainText(`Skill${name}`);
    await expect(input(page)).toBeFocused();
    await page.locator("#skill-active").getByRole("button", { name: "Clear" }).click();
  }
  expect(await posts(page)).toHaveLength(0);
});

test("Escape closes it and leaves what was typed", async ({ page }) => {
  // A tool that works offline: with outbound requests switched off, as they
  // are for these tests, the ones that need the network are not offered.
  await input(page).pressSequentially("/cl");
  await expect(palette(page)).toBeVisible();
  await input(page).press("Escape");
  await expect(palette(page)).toBeHidden();
  await expect(input(page)).toHaveValue("/cl");
  await expect(input(page)).not.toHaveAttribute("aria-activedescendant", /.+/);
});

test("it closes once arguments are being typed, and when the input loses focus", async ({
  page,
}) => {
  await input(page).pressSequentially("/calc");
  await expect(palette(page)).toBeVisible();
  await input(page).pressSequentially(" ");
  await expect(palette(page)).toBeHidden();

  await input(page).fill("");
  await input(page).pressSequentially("/");
  await expect(palette(page)).toBeVisible();
  await page.locator("#hero h1").click();
  await expect(palette(page)).toBeHidden();
});

test("pointing at a command describes it; clicking takes it, with focus kept in the input", async ({
  page,
}) => {
  await input(page).pressSequentially("/");
  const selected = await describedAs(page).textContent();
  const third = options(page).nth(2);
  await third.hover();
  await expect(describedAs(page)).not.toHaveText(selected ?? "");
  // Leaving goes back to the one the keyboard has selected.
  await page.locator("#hero h1").hover();
  await expect(describedAs(page)).toHaveText(selected ?? "");

  const cmd = ((await third.locator("span").nth(1).textContent()) ?? "").split(" ")[0];
  await third.click();
  await expect(palette(page)).toBeHidden();
  await expect(input(page)).toBeFocused();
  await expect(input(page)).toHaveValue(new RegExp(`^\\${cmd} `));
  expect(await posts(page)).toHaveLength(0);
});
