/* Motion primitives.
 *
 * An entrance for things that arrive in the thread, which starts from the
 * element's own visible state, so if the animation never runs — a hidden tab,
 * an old engine — the content is simply there. And one shared frame loop for
 * the few things that move continuously.
 */

let sampled: boolean | null = null;

/**
 * Whether the visitor has asked for reduced motion.
 *
 * Asked once, the first time anything needs to know, and held for the life of
 * the page: the aurora chooses how it will draw from this, and a page that
 * changed its mind halfway through an answer would be worse than one that
 * waits for the next load. Nothing is read when this module is imported, so it
 * loads where there is no window.
 */
export function reduced(): boolean {
  if (sampled === null) {
    sampled =
      typeof window !== "undefined" && typeof window.matchMedia === "function"
        ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
        : false;
  }
  return sampled;
}

/** The arrival curve, the same one the stylesheets call --ease. */
export const EASE = "cubic-bezier(0.16, 1, 0.3, 1)";

export interface EnterOptions {
  /** Milliseconds to hold the first frame before moving. */
  delay?: number;
  /** How far below its place the element starts, in pixels. */
  distance?: number;
}

/** Bring an element in from just below. Under reduced motion it just appears. */
export function enter(el: Element, { delay = 0, distance = 8 }: EnterOptions = {}): void {
  if (reduced() || !el.animate) return;
  el.animate(
    [
      { opacity: 0, translate: `0 ${distance}px` },
      { opacity: 1, translate: "0 0" },
    ],
    // `backwards` holds the first frame through the delay, so a staggered
    // card does not flash in at full opacity before its turn.
    { duration: 240, delay, easing: EASE, fill: "backwards" },
  );
}

/** Called every frame with the time now and the milliseconds since the last frame. */
export type FrameFn = (now: number, dt: number) => void;

// One animation-frame loop for everything that moves continuously (the rate
// trace, the aurora). It runs only while something is subscribed, so at rest
// the page asks for no frames at all.
const subscribers = new Set<FrameFn>();
let raf = 0;
let previous = 0;

function tick(now: number): void {
  // Capped, so a tab coming back from the background does not deliver one
  // enormous step to things that integrate over time.
  const dt = Math.min(64, now - previous);
  previous = now;
  for (const fn of subscribers) fn(now, dt);
  raf = subscribers.size ? requestAnimationFrame(tick) : 0;
}

/** Call `fn(now, dt)` every frame until the returned function is called. */
export function onFrame(fn: FrameFn): () => void {
  subscribers.add(fn);
  if (!raf) {
    previous = performance.now();
    raf = requestAnimationFrame(tick);
  }
  return () => {
    subscribers.delete(fn);
  };
}

/**
 * Drop every subscriber and cancel the frame that is pending. For teardown:
 * when the module is replaced during development the old loop must not keep
 * calling into code that has been replaced with it.
 */
export function stopFrames(): void {
  subscribers.clear();
  if (raf) cancelAnimationFrame(raf);
  raf = 0;
}

if (import.meta.hot) import.meta.hot.dispose(stopFrames);
