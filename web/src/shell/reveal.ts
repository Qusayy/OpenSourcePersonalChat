/* Charts draw themselves when scrolled to.
 *
 * An element starts drawn. Once <html> carries `reveal-ready` (the shell adds
 * it, and removes it for printing), the stylesheet holds a chart undrawn until
 * it also carries `in-view`, which this module adds the first time the chart
 * is on screen. With reduced motion, or without IntersectionObserver, it is
 * added at once and the chart is simply there.
 */

import { type RefObject, useEffect } from "react";
import { reduced } from "@/lib/frame";

let observer: IntersectionObserver | null | undefined;

function revealObserver(): IntersectionObserver | null {
  if (observer !== undefined) return observer;
  observer =
    "IntersectionObserver" in window && !reduced()
      ? new IntersectionObserver(
          (entries) => {
            for (const entry of entries) {
              if (!entry.isIntersecting) continue;
              entry.target.classList.add("in-view");
              observer?.unobserve(entry.target);
            }
          },
          { threshold: 0.2 },
        )
      : null;
  return observer;
}

/** Have `el` draw itself the next time it is on screen (now, if it already is). */
export function reveal(el: HTMLElement | null | undefined): void {
  if (!el) return;
  const io = revealObserver();
  if (!io) {
    el.classList.add("in-view");
    return;
  }
  el.classList.remove("in-view");
  void el.offsetWidth; // commit the undrawn state before it can be drawn
  io.observe(el);
}

/** Stop waiting for `el` (it is leaving the page). */
export function unreveal(el: HTMLElement | null | undefined): void {
  if (el) revealObserver()?.unobserve(el);
}

/**
 * Reveal the element a ref points at, and again whenever `redrawOn` changes
 * (new data means the chart draws itself again).
 */
export function useReveal(ref: RefObject<HTMLElement | null>, redrawOn?: unknown): void {
  // biome-ignore lint/correctness/useExhaustiveDependencies: `redrawOn` is the caller's signal to draw again
  useEffect(() => {
    const el = ref.current;
    reveal(el);
    return () => unreveal(el);
  }, [ref, redrawOn]);
}
