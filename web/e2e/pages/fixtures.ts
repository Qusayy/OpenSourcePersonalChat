import type { Page } from "@playwright/test";
import { forward } from "../support";

/* A benchmark the tests can be exact about. Mock mode stores no runs and
   times a sleep, so a real run never gives the same figures twice; where a
   test asserts a number, the number comes from here. */

const summary = (
  id: string,
  label: string,
  prompt_tokens: number,
  ttft_p50: number,
  ttft_p95: number,
  prefill_tps: number,
  gen_tps: number,
) => ({
  id,
  label,
  prompt_tokens,
  gen_tokens: 128,
  reps: id === "long" ? 2 : 3,
  ttft_p50,
  ttft_p95,
  prefill_tps,
  gen_tps,
  gen_tps_min: Math.round((gen_tps - 0.11) * 100) / 100,
  gen_tps_max: Math.round((gen_tps + 0.11) * 100) / 100,
  rss_mb: 1388,
});

export const CASES = [
  summary("short", "Short prompt", 34, 742, 781.3, 45.8, 9.84),
  summary("medium", "Medium prompt", 261, 4310, 4498.6, 60.6, 9.71),
  summary("long", "Long prompt", 1033, 15240, 15560, 67.8, 9.42),
];

export const LATEST = {
  cases: CASES,
  gen_tps: 9.71,
  prefill_tps: 60.6,
  rss_mb: 1388,
  at: 1791800000,
};

/** Twelve stored runs, newest first, as the API returns them. */
export const HISTORY = Array.from({ length: 12 }, (_, i) => ({
  id: `run${i}`,
  kind: "bench",
  label: ["short", "medium", "long"][i % 3],
  prompt_tokens: 34,
  gen_tokens: 128,
  ttft_ms: 742,
  prefill_tps: 45.8,
  gen_tps: 9.9 - i * 0.07,
  rss_mb: 1388,
  created_at: 1791800000 - i * 3600,
}));

type Frame = [event: string, data: unknown];

/** What a run's stream says, start to finish. */
export function runFrames(stored: boolean): Frame[] {
  return [
    ["stage", { text: "Warming up (this run is discarded)", pct: 0 }],
    ["stage", { text: "Short prompt — run 1 of 3", pct: 0 }],
    ["rep", { case: "short", rep: 1, gen_tps: 9.84, ttft_ms: 742, pct: 13 }],
    ["case", CASES[0]],
    ["case", CASES[1]],
    ["case", CASES[2]],
    ["summary", { ...LATEST, elapsed_s: 118.4, info: {}, stored }],
  ];
}

const sse = (frames: Frame[]) =>
  frames.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");

/** Answer the next benchmark run with these frames instead of running one. */
export async function cannedRun(page: Page, frames: Frame[]): Promise<void> {
  await page.route("**/api/bench/run", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: sse(frames) }),
  );
}

/** Give the page a stored suite and a history, as a server that has run the
 *  benchmark for real would. Returns how many times the results were asked for. */
export async function storedResults(page: Page): Promise<{ asked: () => number }> {
  let asked = 0;
  await page.route("**/api/bench/results", async (route) => {
    asked += 1;
    const response = await forward(route);
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, latest: LATEST, history: HISTORY } });
  });
  return { asked: () => asked };
}
