/* The conversation's half of the status line: what a request is costing while
 * it is in flight, and how full the context is.
 *
 * These figures change many times a second (the rate eases every frame, the
 * trace scrolls), so they are written straight to the elements rather than
 * through a render. The status line hands its elements over with
 * `bindFigures` when the chat page is on screen; the chat page drives them
 * with the functions below. With no elements bound the functions still keep
 * the phase and the aurora right, and simply have nowhere to print.
 */

import { aurora } from "@/lib/aurora";
import { fmtInt, fmtMs } from "@/lib/format";
import { createTrace } from "@/lib/trace";
import { currentInfo } from "@/state/queries";
import { setPhase } from "@/state/shell";

export interface FigureElements {
  /** The status line itself; carries `data-ctx`. */
  bar: HTMLElement;
  wait: HTMLElement;
  waitLead: HTMLElement;
  waitS: HTMLElement;
  tpsFig: HTMLElement;
  tps: HTMLElement;
  spark: HTMLCanvasElement;
  ttftFig: HTMLElement;
  ttft: HTMLElement;
  ctxValue: HTMLElement;
  ctxFill: HTMLElement;
}

let els: FigureElements | null = null;
// Context use belongs to the conversation, which outlives a visit to another
// page; the elements do not. Kept so it can be printed again when they return.
let contextTokens = 0;

const trace = createTrace({
  canvas: () => els?.spark,
  figure: () => els?.tps,
  onRate: (rate) => aurora.rate(rate),
});

/** Hand over the elements (the chat page came on screen) or take them back. */
export function bindFigures(next: FigureElements | null): void {
  els = next;
  if (!els) return;
  els.tps.textContent = "—";
  els.ttft.textContent = "—";
  paintContext();
}

function paintContext(): void {
  if (!els) return;
  const max = currentInfo().n_ctx || 4096;
  const share = Math.max(0, Math.min(1, contextTokens / max));
  els.ctxValue.textContent = fmtInt(contextTokens);
  els.ctxFill.style.setProperty("--v", share.toFixed(4));
  // Past this the next long answer is likely to push old turns out.
  els.bar.dataset.ctx = share > 0.85 ? "high" : "ok";
}

/** How many tokens of the context the conversation now takes. */
export function setContext(tokens: number): void {
  contextTokens = tokens;
  paintContext();
}

/** A measured rate, and when it was measured on the `performance.now()` clock. */
export function traceSample(rate: number, at: number): void {
  trace.sample(rate, at);
}

/**
 * Take the cost figures off the line. They are there while a request is in
 * flight; once it ends they are printed under the answer they belong to, and
 * showing them here as well would say the same thing twice.
 */
export function resetFigures(): void {
  if (els) {
    els.tpsFig.hidden = els.ttftFig.hidden = true;
    els.tps.textContent = "—";
    els.ttft.textContent = "—";
  }
  trace.stop();
}

/** The first token has landed: the figures have something to report. */
export function showFigures(ttftMs?: number | null): void {
  if (els) els.tpsFig.hidden = false;
  trace.start();
  if (els && ttftMs !== null && ttftMs !== undefined) {
    els.ttft.textContent = fmtMs(ttftMs);
    els.ttftFig.hidden = false;
  }
}

export type LivePhase = "working" | "queued" | "reading" | "writing";

// The status line's state while a request is in flight. Every phase but
// "writing" is a wait, so it counts the seconds: on this hardware a wait is
// long enough that a still label looks like a hang. The count sits with the
// figures on the right, where it can change width without moving anything.
const PHASE_WORDS: Record<LivePhase, string> = {
  working: "working",
  queued: "queued",
  reading: "reading",
  writing: "writing",
};
let phaseClock: ReturnType<typeof setInterval> | undefined;

/**
 * Put the line into a request phase, or pass nothing to end it. `lead` is
 * shown before the seconds while waiting ("2 ahead of you").
 */
export function livePhase(key?: LivePhase | null, lead = ""): void {
  clearInterval(phaseClock);
  phaseClock = undefined;
  if (els) els.wait.hidden = true;
  // The one place the aurora is driven from: it follows the same phase the
  // status line shows, so the two cannot disagree.
  aurora.phase(key);
  if (!key) {
    setPhase(null);
    return;
  }
  setPhase(key, PHASE_WORDS[key]);
  if (key === "writing") return;
  const since = performance.now();
  const paint = () => {
    if (!els) return;
    const s = Math.floor((performance.now() - since) / 1000);
    els.waitLead.textContent = lead;
    els.waitS.textContent = s >= 2 ? `${s} s` : "";
    els.wait.hidden = !lead && s < 2;
  };
  paint();
  phaseClock = setInterval(paint, 1000);
}
