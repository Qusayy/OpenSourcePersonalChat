import { expect, test } from "@playwright/test";
import { fixMachine, MACHINE } from "../support";

/* The About page: its facts are read from the machine as the page is looked
   at, and what the machine cannot say is left out rather than dashed. */

const row = (page: import("@playwright/test").Page, term: string) =>
  page.locator("dl > div").filter({ has: page.getByText(term, { exact: true }) });

test("says what runs here, with the model's own name", async ({ page }) => {
  await page.goto("/about");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Everything here runs on one small server",
  );
  const model = await page.evaluate(async () => (await (await fetch("/api/health")).json()).engine);
  await expect(page.locator("header p")).toContainText(`${model.model} at ${model.quant} on a`);
  await expect(row(page, "Params").locator("dd")).toHaveText(model.params);
  await expect(row(page, "Context").locator("dd")).toHaveText(String(model.n_ctx));
  await expect(row(page, "Threads for the model").locator("dd")).toHaveText(
    `${model.n_threads} / ${model.cpu_count ?? "?"}`,
  );
});

test("fills its facts from the machine as measured", async ({ page }) => {
  await fixMachine(page);
  await page.goto("/about");
  await expect(row(page, "CPUs this app can use").locator("dd")).toHaveText("2 cores");
  await expect(row(page, "CPU load now").locator("dd")).toHaveText("5%");
  await expect(row(page, "Memory in use, whole machine").locator("dd")).toHaveText("2.1 / 3.8 GB");
  await expect(row(page, "Memory held by this app").locator("dd")).toHaveText("1.3 GB");
  await expect(row(page, "Memory limit on the service").locator("dd")).toHaveText("2.5 GB");
  await expect(row(page, "Load average, 1 / 5 / 15 min").locator("dd")).toHaveText(
    "1.90 1.20 0.80",
  );
  await expect(page.getByText("Measured now: this process holds 1.35 GB resident.")).toBeVisible();
});

test("keeps them current while the page is open", async ({ page }) => {
  let cpu = 5;
  await fixMachine(page);
  await page.route("**/api/telemetry", async (route) => {
    await route.fulfill({
      json: {
        system: { ...MACHINE, at: Date.now(), cpu_pct: cpu, cores_pct: [cpu, cpu] },
        engine: { status: "ready", busy: false, queued: 0 },
      },
    });
  });
  await page.goto("/about");
  await expect(row(page, "CPU load now").locator("dd")).toHaveText("5%");
  cpu = 88;
  await expect(row(page, "CPU load now").locator("dd")).toHaveText("88%", { timeout: 8000 });
});

test("leaves out a row the machine has nothing to say for", async ({ page }) => {
  await fixMachine(page, {
    ...MACHINE,
    cpus: null,
    cpu_pct: null,
    cores_pct: null,
    rss_mb: null,
    limit_mb: null,
    limit_high_mb: null,
    load: null,
  } as unknown as typeof MACHINE);
  await page.goto("/about");
  // What was measured is there.
  await expect(row(page, "Memory in use, whole machine").locator("dd")).toHaveText("2.1 / 3.8 GB");
  // What was not is absent: no row, no dash.
  for (const term of [
    "CPUs this app can use",
    "CPU load now",
    "Memory held by this app",
    "Memory limit on the service",
    "Load average, 1 / 5 / 15 min",
  ]) {
    await expect(row(page, term)).toHaveCount(0);
  }
  await expect(page.getByText("Measured now")).toHaveCount(0);
  await expect(page.getByText("Estimated with the model loaded")).toBeVisible();
});

test("estimates memory, and links to how a larger model would stand", async ({ page }) => {
  await page.goto("/about");
  const stack = page.locator(".stack");
  await expect(stack.locator("span")).toHaveCount(4);
  await expect(stack).toHaveAttribute("aria-label", /^Estimated memory use: Weights /);
  await stack.scrollIntoViewIfNeeded();
  // Drawn when it is scrolled to.
  await expect(stack).toHaveClass(/in-view/);
  await expect(page.locator("main")).toContainText(/Estimated with the model loaded: \d\.\d\d GB/);
  await expect(page.locator("main")).toContainText("This is mock mode: the weights are not loaded");

  await page.evaluate(() => {
    (window as unknown as { __same: boolean }).__same = true;
  });
  await page.getByRole("link", { name: "How that compares with a larger model" }).click();
  await expect(page).toHaveURL(/\/bench#memory$/);
  await expect(page.locator("#memory h2")).toBeInViewport();
  // The same page, not a reload.
  expect(await page.evaluate(() => (window as unknown as { __same?: boolean }).__same)).toBe(true);
});

test("describes its own front-end as it is", async ({ page }) => {
  await page.goto("/about");
  const stack = page.locator("#stack");
  await expect(stack).toContainText("React and TypeScript, built with Vite into static files");
  await expect(stack).toContainText("five runtime packages and nothing from a CDN");
  await expect(stack).not.toContainText("no build step");
});

test("its contents list goes to each part", async ({ page }) => {
  await page.goto("/about");
  await page
    .getByRole("navigation", { name: "On this page" })
    .getByText("What it cannot do")
    .click();
  await expect(page).toHaveURL(/\/about#limits$/);
  await expect(page.locator("#limits h2")).toBeInViewport();
});
