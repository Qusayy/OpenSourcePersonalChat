import { expect, test } from "@playwright/test";
import { FOREIGN_PAGE } from "../support";
import { cannedRun, runFrames, storedResults } from "./fixtures";

/* The Benchmark page: what it shows before anything has been run, a run from
   start to finish, a stored history, and its charts. */

const INSECURE = Boolean(process.env.AURORA_E2E_INSECURE);

test.describe("before any run", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/bench");
  });

  test("opens on what the server already knows", async ({ page }) => {
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("How fast this server writes");
    // Said before anything is read as a result.
    await expect(page.getByRole("note")).toContainText("This server is in mock mode");
    await expect(page.locator("#run-status")).toHaveText(
      "Nothing is stored in mock mode. A run takes about two minutes.",
    );
    await expect(page.locator("#run-btn")).toBeEnabled();

    const suite = page.locator("#suite");
    await expect(suite).toContainText("Short prompt~32 tok × 3");
    await expect(suite).toContainText("Long prompt~1024 tok × 2");
    await expect(suite).toContainText("Generated128 tok each");
  });

  test("leaves out what was not measured instead of printing dashes", async ({ page }) => {
    await expect(page.locator("#tiles")).toBeHidden();
    await expect(page.locator("#tiles-empty")).toHaveText("Run the suite to fill this in.");
    await expect(page.locator("#chart-gen")).toHaveText("Run the suite to fill this in.");
    await expect(page.locator("#chart-ttft")).toHaveText("Run the suite to fill this in.");
    await expect(page.locator("#history-chart")).toBeHidden();
    await expect(page.locator("#history-empty")).toHaveText("No benchmark runs recorded yet.");
    await expect(page.locator("#case-table tbody tr")).toHaveCount(1);
    await expect(page.locator("#case-table tbody")).toHaveText("No run recorded yet.");
  });

  test("still places the reference machines, with this server unmeasured", async ({ page }) => {
    const rows = page.locator("#chart-ref .bar-row");
    await expect(rows).toHaveCount(5);
    await expect(rows.first()).toContainText("This servernot measured yet");
    await expect(rows.first().locator(".strip b")).toHaveCount(0);
    await expect(rows.nth(1)).toContainText("2 vCPU shared cloud2 threads");
    await expect(rows.nth(1).locator(".val")).toHaveText("6–12");
    // The scale ends a little past the fastest reference: 70 × 1.08.
    await expect(page.locator("#ref-max")).toHaveText("76");
  });

  test("estimates memory part by part, and says it is an estimate", async ({ page }) => {
    const memory = page.locator("#memory");
    await expect(memory).toContainText("It is not a measurement");
    const segments = memory.locator(".stack span");
    await expect(segments).toHaveCount(4);
    await expect(memory.locator(".stack")).toHaveAttribute(
      "aria-label",
      /^Estimated memory use: Weights .* megabytes, KV cache .* megabytes, Compute buffers 200 megabytes, Python \+ FastAPI \+ uvicorn 120 megabytes$/,
    );
    const widths = await segments.evaluateAll((els) =>
      els.map((el) => Number.parseFloat((el as HTMLElement).style.width)),
    );
    expect(widths.reduce((a, b) => a + b, 0)).toBeCloseTo(100, 0);
    await expect(memory).toContainText("Compute buffers 200 MB · scales with n_batch");
    await expect(memory).toContainText(/Estimated with the model loaded: \d\.\d\d GB/);
    await expect(memory).toContainText("A 7B model at the same quantisation is roughly 4.4 GB");
    await expect(memory).toContainText("This is mock mode: the weights are not loaded");
  });

  test("a link in the contents goes to its part of the page", async ({ page }) => {
    await page.getByRole("navigation", { name: "On this page" }).getByText("Memory budget").click();
    await expect(page).toHaveURL(/\/bench#memory$/);
    await expect(page.locator("#memory h2")).toBeInViewport();
    // And arriving at that address directly lands there too.
    await page.goto("/about");
    await page.goto("/bench#history");
    await expect(page.locator("#history h2")).toBeInViewport();
  });
});

test.describe("a run", () => {
  test("is followed on the page and then drawn", async ({ page }) => {
    await cannedRun(page, runFrames(false));
    await page.goto("/bench");
    await page.locator("#run-btn").click();

    await expect(page.locator("#run-status")).toHaveText(
      "Finished in 118s — median 9.71 tok/s. Mock mode: not stored.",
    );
    await expect(page.locator("#progress")).toHaveAttribute("style", /scaleX\(1\)/);
    await expect(page.locator("#run-btn")).toBeEnabled();

    await expect(page.locator("#tiles")).toBeVisible();
    await expect(page.locator("#tiles-empty")).toBeHidden();
    await expect(page.locator("#t-gen")).toHaveText("9.7");
    await expect(page.locator("#t-prefill")).toHaveText("61");
    await expect(page.locator("#t-ttft")).toHaveText("742ms");
    await expect(page.locator("#t-rss")).toHaveText("1,388 MB");

    const gen = page.locator("#chart-gen .bar-row");
    await expect(gen).toHaveCount(3);
    await expect(gen.first()).toContainText("Short prompt34 prompt tokens");
    await expect(gen.first().locator(".val")).toHaveText("9.84 tok/s · 9.7–9.9");
    await expect(gen.first().locator(".track")).toHaveAttribute(
      "title",
      "Short prompt: 9.84 tok/s",
    );

    // Each case twice: its median, then its p95, the fainter of the pair.
    const ttft = page.locator("#chart-ttft .bar-row");
    await expect(ttft).toHaveCount(6);
    await expect(ttft.nth(0).locator(".track i")).not.toHaveClass(/alt/);
    await expect(ttft.nth(1).locator(".track i")).toHaveClass(/alt/);
    await expect(ttft.nth(1).locator(".val")).toHaveText("781 ms");

    await expect(page.locator("#chart-ref .bar-row").first()).toContainText("measured");
    await expect(page.locator("#chart-ref .strip.self b")).toHaveAttribute(
      "title",
      "measured here: 9.7 tok/s",
    );

    const cells = page.locator("#case-table tbody tr").first().locator("td");
    await expect(page.locator("#case-table tbody tr")).toHaveCount(3);
    await expect(cells).toHaveText([
      "Short prompt",
      "34",
      "128",
      "3",
      "45.8",
      "9.84",
      "742ms",
      "781ms",
    ]);
  });

  test("that the server stored is read back from the server", async ({ page }) => {
    await cannedRun(page, runFrames(true));
    const results = await storedResults(page);
    await page.goto("/bench");
    await expect(page.locator("#t-gen")).toHaveText("9.7");
    const before = results.asked();
    await page.locator("#run-btn").click();
    await expect(page.locator("#run-status")).toHaveText("Finished in 118s — median 9.71 tok/s");
    await expect.poll(results.asked).toBeGreaterThan(before);
  });

  test("that fails says why, on the page and in a toast", async ({ page }) => {
    await cannedRun(page, [
      ["stage", { text: "Warming up (this run is discarded)", pct: 0 }],
      ["error", { message: "The model is still loading." }],
    ]);
    await page.goto("/bench");
    await page.locator("#run-btn").click();
    await expect(page.locator("#run-status")).toHaveText("The model is still loading.");
    await expect(page.locator("#toasts")).toContainText("The model is still loading.");
    await expect(page.locator("#run-btn")).toBeEnabled();
  });

  test("is ended by leaving the page", async ({ page }) => {
    // A run that never finishes: the request is held open.
    await page.route("**/api/bench/run", () => {});
    await page.goto("/bench");
    const dropped = page.waitForEvent("requestfailed", (r) => r.url().includes("/api/bench/run"));
    await page.locator("#run-btn").click();
    await expect(page.locator("#run-btn")).toBeDisabled();
    await expect(page.locator("#run-status")).toHaveText("Starting…");
    await page
      .getByRole("navigation", { name: "Primary" })
      .getByRole("link", { name: "About" })
      .click();
    await expect(page).toHaveURL(/\/about$/);
    await dropped;
  });

  test("for real, start to finish, in mock mode", async ({ page }) => {
    // Once is enough: this is two minutes of the one engine.
    test.skip(INSECURE || FOREIGN_PAGE, "the real run is exercised in the default mode");
    test.setTimeout(360_000);
    await page.goto("/bench");
    await page.locator("#run-btn").click();
    await expect(page.locator("#run-btn")).toBeDisabled();
    await expect(page.locator("#run-status")).toHaveText(/Warming up|Short prompt/);
    // Progress is reported as it goes, not only at the end: the line names
    // the case and the run it is on, and the bar is part of the way along.
    // (Each run's own figures are on the line only for the instant before the
    // next run is announced.)
    await expect(page.locator("#run-status")).toHaveText(/^Medium prompt — run \d of 3$/, {
      timeout: 120_000,
    });
    await expect
      .poll(async () => (await page.locator("#progress").getAttribute("style")) ?? "", {
        timeout: 120_000,
      })
      .toMatch(/scaleX\(0\.\d+\)/);
    await expect(page.locator("#run-status")).toHaveText(
      /^Finished in \d+s — median \d+\.\d\d tok\/s\. Mock mode: not stored\.$/,
      { timeout: 330_000 },
    );
    await expect(page.locator("#run-btn")).toBeEnabled();
    await expect(page.locator("#chart-gen .bar-row")).toHaveCount(3);
    await expect(page.locator("#chart-ttft .bar-row")).toHaveCount(6);
    await expect(page.locator("#case-table tbody tr")).toHaveCount(3);
    await expect(page.locator("#t-gen")).toHaveText(/^\d+\.\d$/);
    // Nothing was kept: a reload is back to an empty page.
    await page.reload();
    await expect(page.locator("#tiles")).toBeHidden();
  });
});

test.describe("with a stored history", () => {
  test.beforeEach(async ({ page }) => {
    await storedResults(page);
    await page.goto("/bench");
    await expect(page.locator("#t-gen")).toHaveText("9.7");
  });

  test("bars are drawn to scale once they are on screen", async ({ page }) => {
    const chart = page.locator("#chart-gen");
    await chart.scrollIntoViewIfNeeded();
    await expect(chart).toHaveClass(/in-view/);
    // 9.84 of 9.84 × 1.12.
    await expect(chart.locator(".track i").first()).toHaveAttribute("style", /--v:\s*0\.8929/);
    // It grows from the baseline to that length.
    await expect
      .poll(() =>
        chart
          .locator(".track i")
          .first()
          .evaluate((el) => getComputedStyle(el).transform),
      )
      .toMatch(/^matrix\(0\.89/);
  });

  test("the history is one line, drawn at the width it is given", async ({ page }) => {
    const chart = page.locator("#history-chart");
    await chart.scrollIntoViewIfNeeded();
    await expect(chart).toBeVisible();
    await expect(page.locator("#history-empty")).toBeHidden();
    await expect(chart.locator(".pt")).toHaveCount(12);
    await expect(chart.locator(".series")).toHaveAttribute("pathLength", "1");
    // Only the newest point is labelled; the rest say what they are on hover.
    await expect(chart.locator(".lbl.strong")).toHaveText("9.9 tok/s");
    await expect(chart.locator(".pt title").first()).toHaveText(/ — 9\.13 tok\/s · /);
    await expect(chart).toHaveClass(/in-view/);

    const width = async () => {
      const box = await chart.evaluate((el) => Math.round(el.clientWidth));
      const viewBox = (await chart.getAttribute("viewBox")) ?? "";
      return { box, drawn: Number(viewBox.split(" ")[2]) };
    };
    const wide = await width();
    expect(wide.drawn).toBe(wide.box);

    // At another width it is drawn again, in pixels, and does not replay its arrival.
    await page.setViewportSize({ width: 700, height: 800 });
    await expect.poll(async () => (await width()).drawn).toBeLessThan(wide.drawn);
    const narrow = await width();
    expect(narrow.drawn).toBe(narrow.box);
    await expect(chart).toHaveClass(/in-view/);
    const label = await chart
      .locator(".lbl")
      .first()
      .evaluate((el) => getComputedStyle(el).fontSize);
    expect(label).toBe("13px");
  });

  test("on a phone the table is a list, each figure with its heading", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const cell = page.locator("#case-table tbody tr").first().locator("td").nth(5);
    await cell.scrollIntoViewIfNeeded();
    await expect(cell).toHaveText("9.84");
    expect(await cell.evaluate((el) => getComputedStyle(el, "::before").content)).toBe(
      '"Gen tok/s"',
    );
    expect(await cell.evaluate((el) => getComputedStyle(el).display)).toBe("flex");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });

  test("with reduced motion the charts are simply there", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.reload();
    await expect(page.locator("#t-gen")).toHaveText("9.7");
    // Not scrolled to, and already drawn.
    await expect(page.locator("#memory .stack")).toHaveClass(/in-view/);
    await expect(page.locator("#chart-gen")).toHaveClass(/in-view/);
    const transition = await page
      .locator("#chart-gen .track i")
      .first()
      .evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(transition).toBe("0s");
  });
});
