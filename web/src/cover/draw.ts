/* Linking stars.
 *
 * A pointer that comes within reach of an anchor lights it; carried on to
 * another, it leaves a line between the two. That is all a visitor is told,
 * and it works the same for a mouse, a finger and a pen: one drag through
 * several stars, or a tap on each in turn.
 *
 * The lines stay for a moment after the pointer lifts, so a chain can be
 * carried on, and then fade. A chain long enough to be a pattern is handed to
 * `onChain` when the pointer lifts; anything shorter is only a drawing.
 *
 * Nothing here draws. It keeps `sky.overlay` up to date and the sky draws
 * that, so the lines move on the same frame as everything else.
 */

import { ANCHORS, MAX_STARS, MIN_STARS } from "./pattern";
import { MAX_SEGMENTS, type Sky } from "./sky";

/** How long the lines hold after the pointer lifts, and then take to go. */
const LINGER = 1300;
const FADE = 900;
/** How long after the pointer lifts a chain is taken to be finished. Someone
 *  tapping a pattern in star by star lifts after every one; offering the chain
 *  at each lift would send every beginning of it as a guess of its own, and
 *  guesses are counted. */
const SETTLE = 450;
/** Reach, in units of the view's shorter side, and its floor in CSS pixels. */
const REACH = { mouse: 0.045, touch: 0.072 };
const MIN_REACH = 22;

export interface DrawOptions {
  /** The pointer lifted on a chain of at least MIN_STARS, and stayed lifted. */
  onChain?: (chain: readonly number[]) => void;
}

export interface Draw {
  /** The stars linked so far, oldest first. */
  readonly chain: readonly number[];
  release(): void;
}

export function mountDraw(el: HTMLElement, sky: Sky, options: DrawOptions = {}): Draw {
  const { view, overlay } = sky;
  const chain: number[] = [];
  let pointer: number | null = null; // the one pointer being followed
  let coarse = false; // whether it is a finger
  let px = 0; // where it is, in units
  let py = 0;
  let hover = -1;
  let lifted = 0; // when the pointer last lifted
  let offer: ReturnType<typeof setTimeout> | null = null; // a finished chain, about to be handed on
  let alpha = 0;
  let left = 0;
  let top = 0;

  function measure(): void {
    const box = el.getBoundingClientRect();
    left = box.left;
    top = box.top;
  }

  function locate(event: PointerEvent): void {
    px = (event.clientX - left - view.width / 2) / view.unit;
    py = -(event.clientY - top - view.height / 2) / view.unit;
  }

  /** The anchor within reach of the pointer, or -1. */
  function reach(): number {
    const r = Math.max(coarse ? REACH.touch : REACH.mouse, MIN_REACH / view.unit);
    let best = -1;
    let nearest = r * r;
    for (let i = 0; i < ANCHORS.length; i++) {
      const a = ANCHORS[i]!;
      const d = (a.x - px) ** 2 + (a.y - py) ** 2;
      if (d < nearest) {
        nearest = d;
        best = i;
      }
    }
    return best;
  }

  function linkTo(id: number): void {
    if (id < 0 || chain[chain.length - 1] === id || chain.length >= MAX_STARS) return;
    chain.push(id);
    // A little over full, so a star answers the moment it is caught.
    overlay.glow[id] = 1.6;
  }

  function clear(): void {
    chain.length = 0;
    alpha = 0;
  }

  function down(event: PointerEvent): void {
    if (pointer !== null || (event.pointerType === "mouse" && event.button !== 0)) return;
    pointer = event.pointerId;
    withdraw();
    coarse = event.pointerType !== "mouse";
    el.setPointerCapture?.(event.pointerId);
    measure();
    locate(event);
    // Lines already fading belong to a drawing that is over.
    if (chain.length && performance.now() - lifted > LINGER) clear();
    alpha = 1;
    linkTo(reach());
  }

  function move(event: PointerEvent): void {
    if (pointer === null) {
      if (event.pointerType !== "mouse") return;
      coarse = false;
      measure();
      locate(event);
      hover = reach();
      return;
    }
    if (event.pointerId !== pointer) return;
    // A quick stroke arrives as several positions in one event; taking only
    // the last would step over a star it passed through.
    const steps = event.getCoalescedEvents?.() ?? [];
    for (const step of steps) {
      locate(step);
      linkTo(reach());
    }
    locate(event);
    linkTo(reach());
  }

  function lift(event: PointerEvent, finished: boolean): void {
    if (event.pointerId !== pointer) return;
    pointer = null;
    lifted = performance.now();
    hover = -1;
    if (finished && chain.length >= MIN_STARS) {
      const drawn = chain.slice();
      offer = setTimeout(() => {
        offer = null;
        options.onChain?.(drawn);
      }, SETTLE);
    }
  }

  /** The hand is back on the sky: the chain was not finished after all. */
  function withdraw(): void {
    if (offer !== null) clearTimeout(offer);
    offer = null;
  }

  const up = (event: PointerEvent): void => lift(event, true);
  const cancel = (event: PointerEvent): void => lift(event, false);
  const leave = (): void => {
    hover = -1;
  };
  // A long press on a touch screen asks for a menu over the sky.
  const menu = (event: Event): void => {
    if (coarse) event.preventDefault();
  };

  function step(now: number, dt: number): void {
    if (pointer === null && chain.length) {
      const age = now - lifted;
      if (age > LINGER + FADE) clear();
      else if (age > LINGER) {
        const t = (age - LINGER) / FADE;
        alpha = (1 - t) * (1 - t);
      }
    }

    // Each anchor eases toward how lit it should be. Time-based, so it is the
    // same at 60 and at 120 frames a second.
    const ease = 1 - Math.exp(-dt / 110);
    let any = chain.length > 0;
    for (let i = 0; i < ANCHORS.length; i++) {
      const target = chain.includes(i) ? alpha : i === hover ? 0.55 : 0;
      const g = overlay.glow[i]! + (target - overlay.glow[i]!) * ease;
      overlay.glow[i] = g;
      if (g > 0.004) any = true;
    }

    let n = 0;
    for (let i = 1; i < chain.length && n < MAX_SEGMENTS; i++) {
      const a = ANCHORS[chain[i - 1]!]!;
      const b = ANCHORS[chain[i]!]!;
      const o = n++ * 5;
      overlay.segments[o] = a.x;
      overlay.segments[o + 1] = a.y;
      overlay.segments[o + 2] = b.x;
      overlay.segments[o + 3] = b.y;
      overlay.segments[o + 4] = alpha;
    }
    // The line still in the hand, from the last star to the pointer.
    if (pointer !== null && chain.length && n < MAX_SEGMENTS) {
      const a = ANCHORS[chain[chain.length - 1]!]!;
      const o = n++ * 5;
      overlay.segments[o] = a.x;
      overlay.segments[o + 1] = a.y;
      overlay.segments[o + 2] = px;
      overlay.segments[o + 3] = py;
      overlay.segments[o + 4] = 0.7;
    }
    overlay.count = n;
    overlay.active = any;
  }

  el.addEventListener("pointerdown", down);
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", cancel);
  el.addEventListener("pointerleave", leave);
  el.addEventListener("contextmenu", menu);
  sky.onStep = step;

  return {
    chain,
    release() {
      withdraw();
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", cancel);
      el.removeEventListener("pointerleave", leave);
      el.removeEventListener("contextmenu", menu);
      if (sky.onStep === step) sky.onStep = null;
      overlay.count = 0;
      overlay.glow.fill(0);
      overlay.active = false;
    },
  };
}
