/* The star field, as one buffer of points.
 *
 * Every star is placed once, from a fixed seed, over a square larger than any
 * screen, in the same units the anchors use. A view shows whichever part of
 * that square it covers, so turning a phone or resizing a window uncovers more
 * sky rather than dealing a new one.
 *
 * Six numbers a star: x, y, radius in CSS pixels, peak brightness, tint, and a
 * random share. The share does two jobs: it is the star's twinkle phase, and
 * it decides whether the star is drawn at all on a small view, where the same
 * count of stars would be packed into far fewer pixels.
 */

import { ANCHORS } from "./pattern";

export const STAR_STRIDE = 6;
/** Half the side of the square the field covers. */
const EXTENT = 1.35;
const SCENERY = 12000;
const FAINTEST = 6.5;
const BRIGHTEST = 1.4;
/** Scenery keeps this far from an anchor, so nothing sits where a link lands. */
const CLEARING = 0.03;

/** A small seeded generator: the field must be the same on every load. */
function mulberry(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function radius(mag: number): number {
  return 0.42 + 0.95 * 10 ** (-0.4 * (mag - 0.8) * 0.33);
}

function brightness(mag: number): number {
  return Math.min(2.4, 0.22 + 1.6 * 10 ** (-0.4 * (mag - 0.8) * 0.45));
}

function nearAnchor(x: number, y: number): boolean {
  for (const a of ANCHORS) {
    if (Math.hypot(a.x - x, a.y - y) < CLEARING) return true;
  }
  return false;
}

/** The anchors first, in their own order, then the scenery. */
export function buildStars(): Float32Array {
  const random = mulberry(0x51a2c0de);
  const data = new Float32Array((ANCHORS.length + SCENERY) * STAR_STRIDE);
  let o = 0;
  for (const a of ANCHORS) {
    data[o++] = a.x;
    data[o++] = a.y;
    data[o++] = radius(a.mag);
    data[o++] = brightness(a.mag);
    data[o++] = a.tint;
    // An anchor is drawn on every view, and a share of zero is always in.
    data[o++] = 0;
  }
  // Faint stars far outnumber bright ones: the count below a magnitude grows
  // about threefold for each step fainter.
  const lo = 10 ** (0.5 * BRIGHTEST);
  const hi = 10 ** (0.5 * FAINTEST);
  for (let i = 0; i < SCENERY; i++) {
    let x = 0;
    let y = 0;
    do {
      x = (random() * 2 - 1) * EXTENT;
      y = (random() * 2 - 1) * EXTENT;
    } while (nearAnchor(x, y));
    const mag = Math.log10(lo + random() * (hi - lo)) / 0.5;
    data[o++] = x;
    data[o++] = y;
    data[o++] = radius(mag);
    data[o++] = brightness(mag);
    data[o++] = random();
    // Never exactly zero, which is the anchors' mark.
    data[o++] = 0.0001 + random() * 0.9999;
  }
  return data;
}
