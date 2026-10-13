/* The arithmetic behind the charts, kept apart from the drawing so it can be
 * tested: where a bar ends, where a range sits, where the history line runs.
 *
 * Charts are hand-rolled. A charting library would be a bigger download than
 * the rest of this page, on a server whose whole premise is that it fits in
 * 4 GB.
 */

import type { BenchReference, BenchRun } from "@/lib/types";

/* ---------------------------------------------------------------- bars -- */

export interface BarRow {
  label: string;
  /** A second line under the label. */
  sub?: string;
  value: number;
  /** Shown after the value: a range, say. */
  extra?: string;
  /** The fainter of an ordered pair (a p95 beside its p50). */
  alt?: boolean;
}

/** What the longest bar is measured against: a given ceiling, or a little
 *  more than the largest value so the longest bar does not touch the end. */
export function barTop(rows: readonly BarRow[], max?: number): number {
  return max || Math.max(...rows.map((r) => r.value)) * 1.12;
}

/** A bar's length as a share of the track. Never quite nothing: a value that
 *  rounds to an empty bar still shows that there is a bar. */
export function barShare(value: number, top: number): string {
  return Math.max(0.015, value / top).toFixed(4);
}

/* -------------------------------------------------------------- ranges -- */

/**
 * A published range as numbers. Reference rows are written as text ("6–12",
 * "500+"); a single figure is read as that figure and a quarter more.
 */
export function parseRange(text: unknown): [lo: number, hi: number] {
  const m = String(text).match(/(\d+(?:\.\d+)?)\s*[–-]?\s*(\d+(?:\.\d+)?)?/);
  if (!m) return [0, 0];
  const lo = Number.parseFloat(m[1] as string);
  return [lo, m[2] ? Number.parseFloat(m[2]) : lo * 1.25];
}

export interface RangeRow extends BenchReference {
  /** Where the band starts, as a percentage of the strip. */
  left: string;
  /** How wide the band is, as a percentage of the strip; at least one. */
  width: string;
}

export interface RangeChart {
  /** The value at the right-hand end of every strip. */
  max: number;
  rows: RangeRow[];
  /** Where this server's measured value sits, as a percentage; null if not measured. */
  here: string | null;
}

/** Lay the reference machines, and this one if it has been measured, on one scale. */
export function rangeChart(
  reference: readonly BenchReference[],
  measured?: number | null,
): RangeChart {
  const ranges = reference.map((r) => parseRange(r.gen));
  const max = Math.max(...ranges.map((r) => r[1]), measured || 0) * 1.08;
  return {
    max,
    here: measured ? ((100 * measured) / max).toFixed(2) : null,
    rows: reference.map((r, i) => {
      const [lo, hi] = ranges[i] as [number, number];
      return {
        ...r,
        left: ((100 * lo) / max).toFixed(2),
        width: Math.max(1, (100 * (hi - lo)) / max).toFixed(2),
      };
    }),
  };
}

/* ------------------------------------------------------------- history -- */

export const HISTORY_HEIGHT = 190;
const PAD = { l: 42, r: 16, t: 14, b: 28 };
const TICKS = 4;

export interface HistoryPoint {
  x: string;
  y: string;
  run: BenchRun;
}

export interface HistoryGeometry {
  width: number;
  height: number;
  /** Horizontal rules and the value each stands for. */
  grid: Array<{ y: string; labelY: string; value: string }>;
  /** Where the rules start and end, and where their labels are anchored. */
  left: number;
  right: number;
  labelX: number;
  /** The line through every point, and the same line closed down to the axis. */
  line: string;
  area: string;
  points: HistoryPoint[];
  /** The newest point's label: its text and where it goes. */
  last: { x: string; y: string; value: number };
  /** The baseline for the "oldest" and "newest" captions. */
  captionY: number;
}

/** The runs a history chart draws: those with a rate, oldest first, the last sixty. */
export function historyPoints(runs: readonly BenchRun[]): BenchRun[] {
  return runs
    .filter((r) => r.gen_tps)
    .slice()
    .reverse()
    .slice(-60);
}

/**
 * The history chart, in real pixels for a box `clientWidth` wide. A fixed
 * viewBox stretched to the column scales its labels with it: 10px on a
 * desktop became 5px on a phone. Null when there are fewer than two points:
 * one point is not a line.
 */
export function historyGeometry(
  pts: readonly BenchRun[],
  clientWidth: number,
): HistoryGeometry | null {
  if (pts.length < 2) return null;
  const W = Math.max(280, Math.round(clientWidth));
  const H = HISTORY_HEIGHT;
  const rate = (p: BenchRun) => p.gen_tps as number;
  const max = Math.max(...pts.map(rate)) * 1.15;
  const x = (i: number) => PAD.l + (i * (W - PAD.l - PAD.r)) / (pts.length - 1);
  const y = (v: number) => H - PAD.b - (v / max) * (H - PAD.t - PAD.b);

  const grid = Array.from({ length: TICKS + 1 }, (_, t) => {
    const v = (max / TICKS) * t;
    return { y: y(v).toFixed(1), labelY: (y(v) + 3).toFixed(1), value: v.toFixed(0) };
  });

  const line = pts
    .map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(rate(p)).toFixed(1)}`)
    .join(" ");
  const end = pts.length - 1;
  const last = pts[end] as BenchRun;

  return {
    width: W,
    height: H,
    grid,
    left: PAD.l,
    right: W - PAD.r,
    labelX: PAD.l - 8,
    line,
    area: `${line} L${x(end).toFixed(1)} ${H - PAD.b} L${PAD.l} ${H - PAD.b} Z`,
    points: pts.map((run, i) => ({ x: x(i).toFixed(1), y: y(rate(run)).toFixed(1), run })),
    // Direct-label the newest point only; a number on every point is noise.
    last: { x: (x(end) - 6).toFixed(1), y: (y(rate(last)) - 10).toFixed(1), value: rate(last) },
    captionY: H - 6,
  };
}

/* -------------------------------------------------------------- budget -- */

/** A segment's share of the stacked bar, as a percentage to two places. */
export function budgetShare(mb: number, total: number): number {
  return Math.round((100 * 100 * mb) / total) / 100;
}
