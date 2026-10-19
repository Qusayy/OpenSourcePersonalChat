import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type { APIResponse, Page, Route } from "@playwright/test";
import { APP, DB, PYTHON, REPO, SERVER_ENV } from "../playwright.config";

export { APP, FOREIGN_PAGE, INSECURE } from "../playwright.config";

/**
 * Let a routed request through to the app and return its answer, so a test
 * can change part of it. Use this instead of `route.fetch()`: that call is
 * made from the test process, which cannot resolve the made-up hostname of an
 * insecure-context run, so the request is sent to the app's real address.
 */
export function forward(route: Route): Promise<APIResponse> {
  const url = new URL(route.request().url());
  return route.fetch({ url: APP + url.pathname + url.search });
}

/**
 * Put the database back to the four seeded conversations (e2e/seed.py). The
 * server keeps running; it reads conversations per request. Call it from a
 * `beforeEach` in any spec that sends, deletes, or counts conversations.
 */
export function reseed(): void {
  mkdirSync(dirname(DB), { recursive: true });
  execFileSync(PYTHON, ["web/e2e/seed.py"], {
    cwd: REPO,
    env: { ...process.env, ...SERVER_ENV },
    stdio: "pipe",
  });
}

/** What the page is told about the machine when a test fixes it. */
export const MACHINE = {
  at: 1,
  source: "procfs",
  cpus: 2,
  cpu_pct: 5,
  cores_pct: [6, 4],
  mem_total_mb: 3900,
  mem_used_mb: 2150,
  rss_mb: 1380,
  limit_mb: 3072,
  limit_high_mb: 2600,
  cgroup_mb: 1410,
  load: [1.9, 1.2, 0.8],
};

/**
 * Replace the machine's figures with fixed ones, shaped like the server this
 * runs on (two cores, about 4 GB), so a test can assert what the status line
 * says. Call before `page.goto`. Everything else the API returns is real.
 */
export async function fixMachine(page: Page, machine: typeof MACHINE = MACHINE): Promise<void> {
  let n = 1;
  for (const path of ["**/api/telemetry", "**/api/health"]) {
    await page.route(path, async (route) => {
      const response = await forward(route);
      const body = await response.json();
      body.system = { ...machine, at: ++n };
      await route.fulfill({ response, json: body });
    });
  }
}

/** The status line's phase: ready, working, queued, reading, writing, held… */
export const phase = (page: Page) => page.locator("#status").getAttribute("data-phase");
