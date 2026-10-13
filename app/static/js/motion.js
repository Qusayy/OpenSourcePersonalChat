/* Motion primitives.
 *
 * There is one: an entrance for things that arrive in the thread. It starts
 * from the element's own visible state, so if the animation never runs — a
 * hidden tab, an old engine — the content is simply there.
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
