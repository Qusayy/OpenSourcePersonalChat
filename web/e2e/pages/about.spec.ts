import { expect, test } from "@playwright/test";
import { fixMachine, MACHINE } from "../support";

/* The About page: its facts are read from the machine as the page is looked
   at, and what the machine cannot say is left out rather than dashed. */

const row = (page: import("@playwright/test").Page, term: string) =>
  page.locator("dl > div").filter({ has: page.getByText(term, { exact: true }) });

test("says what runs here, with the model's own name", async ({ page }) => {
  await page.goto("/about");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("What answers you, and how");
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
  await expect(row(page, "Memory held by the app and its model").locator("dd")).toHaveText(
    "1.3 GB",
  );
  await expect(row(page, "Memory limit on the service").locator("dd")).toHaveText("2.5 GB");
  await expect(row(page, "Load average, 1 / 5 / 15 min").locator("dd")).toHaveText(
    "1.90 1.20 0.80",
  );
  await expect(
    page.getByText("Measured now: the app and its model hold 1.35 GB resident."),
  ).toBeVisible();
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
    "Memory held by the app and its model",
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

test("says where the model is held", async ({ page }) => {
  await page.goto("/about");
  const stack = page.locator("#stack");
  await expect(stack).toContainText("llama-cpp-python holds the model");

  // The page asks for the engine's health as it opens; this one is served by
  // a llama-server.
  const health = await page.evaluate(async () => (await fetch("/api/health")).json());
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { ...health, engine: { ...health.engine, runtime: "llama-server" } } }),
  );
  await page.reload();
  await expect(stack).toContainText("llama-server holds the model in a service of its own");
  await expect(stack).not.toContainText("llama-cpp-python");
});

test("says how the Agent works and what it is held to", async ({ page }) => {
  await page.goto("/about");
  const agent = page.locator("#agent");
  // The model decides: it thinks, and calls a tool itself when it needs one.
  await expect(agent).toContainText("It thinks first");
  await expect(agent).toContainText(
    "calls a tool itself, in the format it was trained to write, only when it judges that one is needed, and a greeting needs none",
  );
  await expect(agent).toContainText(
    "at most eight calls a message, never one it has already made, and its last reply cannot call at all",
  );
  await expect(agent).toContainText("write_file");
  await expect(agent).toContainText("edit_file, which changes only the lines it was asked to");
  await expect(agent).toContainText("A file written again exactly as it was is refused");
  // Nothing of the pass that chose a tool before the model answered.
  await expect(agent).not.toContainText(/grammar|three lookups|pattern it knows|skill/);
  await expect(agent).toContainText("It only opens an address you wrote yourself");
  await expect(agent).toContainText(
    "a figure no lookup returned is named in a line under the answer",
  );
  await expect(agent).toContainText("How it ran");
});

test("hangs a shallow band of the curtains at its head", async ({ page }) => {
  await page.goto("/about");
  const band = page.locator("#curtains");
  await expect(band).toBeVisible({ timeout: 8000 });
  expect((await band.boundingBox())?.height).toBe(96);
});

test("its contents list goes to each part", async ({ page }) => {
  await page.goto("/about");
  await page
    .getByRole("navigation", { name: "On this page" })
    .getByText("What it's good for")
    .click();
  await expect(page).toHaveURL(/\/about#limits$/);
  await expect(page.locator("#limits h2")).toBeInViewport();
});
