/* The stars that can be linked, and how a chain of them is written down.
 *
 * Positions are in units of the view's shorter side, measured from its centre
 * with y upward, so the figure they make is the same shape on a phone held
 * upright and on a wide monitor. They are spaced so that a fingertip's worth
 * of screen separates any two of them on a 390px phone.
 *
 * The order of this list is the identity of each star: a chain is a list of
 * indices into it. Moving a star changes what a pattern looks like; reordering
 * or removing one changes which pattern it is. Neither should happen once a
 * pattern has been enrolled.
 */

export interface Anchor {
  x: number;
  y: number;
  /** Apparent magnitude: lower is brighter. */
  mag: number;
  /** 0 is blue-white, 0.5 white, 1 warm. */
  tint: number;
}

export const ANCHORS: readonly Anchor[] = [
  { x: -0.36, y: 0.37, mag: -0.2, tint: 0.34 },
  { x: -0.17, y: 0.42, mag: 0.5, tint: 0.52 },
  { x: 0.04, y: 0.35, mag: -0.7, tint: 0.22 },
  { x: 0.26, y: 0.41, mag: 0.3, tint: 0.78 },
  { x: 0.4, y: 0.27, mag: -0.1, tint: 0.46 },
  { x: -0.4, y: 0.2, mag: 0.6, tint: 0.61 },
  { x: -0.21, y: 0.24, mag: -0.5, tint: 0.4 },
  { x: 0.12, y: 0.19, mag: 0.4, tint: 0.3 },
  { x: 0.3, y: 0.12, mag: -0.9, tint: 0.56 },
  { x: -0.3, y: 0.04, mag: 0.1, tint: 0.86 },
  { x: -0.08, y: 0.08, mag: 0.6, tint: 0.26 },
  { x: 0.41, y: -0.04, mag: 0.2, tint: 0.5 },
  { x: -0.39, y: -0.14, mag: -0.4, tint: 0.44 },
  { x: -0.16, y: -0.1, mag: 0.3, tint: 0.7 },
  { x: 0.07, y: -0.06, mag: -0.6, tint: 0.36 },
  { x: 0.24, y: -0.18, mag: 0.5, tint: 0.58 },
];

/** A chain shorter than this is a doodle and never leaves the browser. */
export const MIN_STARS = 7;
/** Longer than anyone would draw; the server refuses more. */
export const MAX_STARS = 48;

/** The bytes that are hashed, as text. The server builds the same string. */
export function canonical(chain: readonly number[]): string {
  return `aurora-gate:v1:${chain.join(",")}`;
}
