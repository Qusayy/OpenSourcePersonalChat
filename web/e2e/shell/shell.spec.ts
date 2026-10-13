import { expect, test } from "@playwright/test";
import { APP, FOREIGN_PAGE, fixMachine, forward } from "../support";

/* The frame every page shares: the top bar, the status line, moving between
   pages, and what the server says about a page that does not exist. */

test.describe("the status line", () => {
  test("shows the machine as measured, once the first sample is in", async ({ page }) => {
    await fixMachine(page);
    await page.goto("/about");
    const line = page.locator("#status");
    await expect(line).toHaveAttribute("data-phase", "ready");
    await expect(line.locator("#b-state")).toHaveText("ready");
    await expect(line.locator("#cpu-count")).toHaveText("2");
    await expect(line.locator("#cpu-noun")).toHaveText("cores");
    await expect(line.locator("#cpu-bars i")).toHaveCount(2);
    await expect(line.locator("#cpu-pct")).toHaveText("5%");
    await expect(line.locator("#ram-used")).toHaveText("2.1");
    await expect(line.locator("#ram-total")).toHaveText("3.8 GB");
    await expect(line).toHaveAttribute("data-ram", "ok");
    // Mock mode says so, always.
    await expect(line.locator("#b-mock")).toBeVisible();
  });

  test("keeps polling: a later sample replaces an earlier one", async ({ page }) => {
    let cpu = 5;
    await page.route("**/api/telemetry", async (route) => {
      const response = await forward(route);
      const body = await response.json();
      body.system = { ...body.system, cpus: 2, cores_pct: [cpu, cpu], cpu_pct: cpu };
      await route.fulfill({ response, json: body });
    });
    await page.goto("/about");
    await expect(page.locator("#cpu-pct")).toHaveText("5%");
    cpu = 73;
    // Every three seconds at rest.
    await expect(page.locator("#cpu-pct")).toHaveText("73%", { timeout: 8000 });
  });

  test("more than eight cores are drawn as one bar", async ({ page }) => {
    await fixMachine(page, {
      at: 1,
      source: "procfs",
      cpus: 12,
      cpu_pct: 40,
      cores_pct: Array.from({ length: 12 }, () => 40),
      mem_total_mb: 16000,
      mem_used_mb: 15000,
      rss_mb: 1380,
      limit_mb: 3072,
      limit_high_mb: 2600,
      cgroup_mb: 1410,
      load: [1, 1, 1],
    });
    await page.goto("/about");
    await expect(page.locator("#cpu-count")).toHaveText("12");
    await expect(page.locator("#cpu-bars")).toHaveClass(/agg/);
    await expect(page.locator("#cpu-bars i")).toHaveCount(1);
    // Memory nearly full takes the caution colour.
    await expect(page.locator("#status")).toHaveAttribute("data-ram", "high");
  });

  test("has the conversation's figures on the chat page only", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#status .st-ctx")).toBeVisible();
    await page.goto("/about");
    await expect(page.locator("#status .st-ctx")).toHaveCount(0);
  });

  test("at phone width keeps the rest behind Details", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await fixMachine(page);
    await page.goto("/about");
    const more = page.locator("#status-more");
    await expect(more).toHaveText("Details");
    await expect(more).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("#status-extra")).toBeHidden();
    await more.click();
    await expect(more).toHaveText("Hide");
    await expect(more).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("#status-extra")).toContainText("this app holds 1.3 GB");
    await expect(page.locator("#status-extra")).toContainText("service limit 2.5 GB");
  });
});

test.describe("moving between pages", () => {
  test("does not reload, and says where you are", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => {
      (window as unknown as { __same: boolean }).__same = true;
    });
    const nav = page.getByRole("navigation", { name: "Primary" });
    await nav.getByRole("link", { name: "Benchmark" }).click();
    await expect(page).toHaveURL(/\/bench$/);
    await expect(page).toHaveTitle(/^Benchmark — /);
    await expect(page.locator("html")).toHaveAttribute("data-page", "bench");
    await expect(nav.getByRole("link", { name: "Benchmark" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await nav.getByRole("link", { name: "About" }).click();
    await expect(page).toHaveTitle("About — Aurora");
    // Focus goes to the new page's content, as a load would have left it.
    await expect(page.locator("#main")).toBeFocused();
    await page.goBack();
    await expect(page).toHaveURL(/\/bench$/);
    expect(await page.evaluate(() => (window as unknown as { __same?: boolean }).__same)).toBe(
      true,
    );
  });

  test("the skip link is the first stop and goes to the content", async ({ page }) => {
    await page.goto("/about");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main")).toBeFocused();
  });
});

test.describe("what the server sends", () => {
  // These are about the app's own responses; the dev server's are not them.
  test.skip(FOREIGN_PAGE, "the page under test is not served by the app");

  test("a page that does not exist is the chat page, with a 404", async ({ page }) => {
    const response = await page.goto("/no/such/page");
    expect(response?.status()).toBe(404);
    await expect(page.locator("html")).toHaveAttribute("data-page", "chat");
    await expect(page.locator("#status")).toBeVisible();
  });

  test("the first values are in the page, with no prompts and no paths", async ({ request }) => {
    const html = await (await request.get(`${APP}/`)).text();
    const found = html.match(/<script type="application\/json" id="bootstrap">(.*?)<\/script>/s);
    expect(found).not.toBeNull();
    const boot = JSON.parse(found?.[1] ?? "null");
    expect(boot.info.model).toBeTruthy();
    expect(Object.keys(boot.personas[0]).sort()).toEqual(["blurb", "id", "name"]);
    expect(html).not.toContain(".gguf");
  });

  test("a missing file is a 404, never the page", async ({ request }) => {
    const response = await request.get(`${APP}/assets/index-gone.js`);
    expect(response.status()).toBe(404);
    expect(await response.text()).not.toContain("<html");
  });
});

test("nothing is logged to the console on any page", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning") problems.push(message.text());
  });
  page.on("pageerror", (error) => problems.push(String(error)));
  for (const path of ["/", "/bench", "/about"]) {
    await page.goto(path);
    await expect(page.locator("#status")).toBeVisible();
  }
  expect(problems).toEqual([]);
});
