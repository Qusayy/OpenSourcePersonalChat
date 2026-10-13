/* Motion primitives.
 *
 * An entrance for things that arrive in the thread, which starts from the
 * element's own visible state, so if the animation never runs — a hidden tab,
 * an old engine — the content is simply there. And one shared frame loop for
 * the few things that move continuously.
 */

export const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The arrival curve, the same one the stylesheets call --ease. */
export const EASE = "cubic-bezier(0.16, 1, 0.3, 1)";

/** Bring an element in from just below. Under reduced motion it just appears. */
export function enter(el, { delay = 0, distance = 8 } = {}) {
  if (REDUCED || !el.animate) return;
  el.animate(
    [
      { opacity: 0, translate: `0 ${distance}px` },
      { opacity: 1, translate: "0 0" },
    ],
    // `backwards` holds the first frame through the delay, so a staggered
    // card does not flash in at full opacity before its turn.
    { duration: 240, delay, easing: EASE, fill: "backwards" }
  );
}

// One animation-frame loop for everything that moves continuously (the rate
// trace, the aurora). It runs only while something is subscribed, so at rest
// the page asks for no frames at all.
const subscribers = new Set();
let raf = 0;
let previous = 0;

function tick(now) {
  // Capped, so a tab coming back from the background does not deliver one
  // enormous step to things that integrate over time.
  const dt = Math.min(64, now - previous);
  previous = now;
  for (const fn of subscribers) fn(now, dt);
  raf = subscribers.size ? requestAnimationFrame(tick) : 0;
}

/** Call `fn(now, dt)` every frame until the returned function is called. */
export function onFrame(fn) {
  subscribers.add(fn);
  if (!raf) {
    previous = performance.now();
    raf = requestAnimationFrame(tick);
  }
  return () => subscribers.delete(fn);
}
