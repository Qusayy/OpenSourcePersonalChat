/* Live throughput meter + sparkline.
 *
 * Tokens arrive at ~10/s but a DOM write per token still costs a layout pass
 * inside a growing thread. Everything here is sampled on a 250 ms timer from
 * counters the token handler only increments.
 */

// The meter's figures are printed with these; they are re-exported so a caller
// that reads the meter can format what it reads from one place.
export { fmt, fmtInt, fmtMs } from "./format";

const WINDOW_MS = 3000; // rolling window for the instantaneous rate
const HISTORY = 48; // sparkline samples

/** One reading of the meter, taken four times a second. */
export interface MeterSample {
  /** Tokens per second over the rolling window. */
  rate: number;
  /** Tokens counted since `start()`. */
  total: number;
  /** The last readings of `rate`, oldest first. The meter's own array: read it, do not keep it. */
  history: number[];
  /** When this reading was taken, on the `performance.now()` clock. */
  at: number;
}

export interface MeterOptions {
  onSample?: (sample: MeterSample) => void;
}

/** What `stop()` reports: the count, and the average over the whole run. */
export interface MeterTotals {
  total: number;
  avg: number;
}

interface Arrival {
  t: number;
  n: number;
}

export class Meter {
  onSample: ((sample: MeterSample) => void) | undefined;
  samples: Arrival[] = []; // {t, n} token arrivals
  history: number[] = [];
  total = 0;
  timer: ReturnType<typeof setInterval> | null = null;
  startedAt = 0;

  constructor({ onSample }: MeterOptions = {}) {
    this.onSample = onSample;
  }

  start(): void {
    this.samples = [];
    this.history = [];
    this.total = 0;
    this.startedAt = performance.now();
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = setInterval(() => this.tick(), 250);
  }

  token(n = 1): void {
    this.total += n;
    this.samples.push({ t: performance.now(), n });
  }

  rate(): number {
    const now = performance.now();
    let first = this.samples[0];
    while (first && now - first.t > WINDOW_MS) {
      this.samples.shift();
      first = this.samples[0];
    }
    if (!first || this.samples.length < 2) return 0;
    const span = (now - first.t) / 1000;
    if (span <= 0) return 0;
    const count = this.samples.reduce((a, s) => a + s.n, 0);
    return count / span;
  }

  tick(): void {
    const r = this.rate();
    this.history.push(r);
    if (this.history.length > HISTORY) this.history.shift();
    // `at` is when this sample was taken, on the same clock animation frames
    // use, so a consumer can draw between samples instead of only on them.
    this.onSample?.({ rate: r, total: this.total, history: this.history, at: performance.now() });
  }

  stop(): MeterTotals {
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    const elapsed = (performance.now() - this.startedAt) / 1000;
    return { total: this.total, avg: elapsed > 0 ? this.total / elapsed : 0 };
  }
}

export interface SparkOptions {
  /** Scale to the data's own range instead of to zero. */
  autoMin?: boolean;
}

/**
 * Sparkline geometry for a 120x38 viewBox. Returns [linePath, fillPath].
 *
 * `autoMin` scales to the data's own range instead of to zero. Throughput
 * wants a zero baseline (2 tok/s really is half of 4); a temperature series
 * does not — 17-28°C against a zero baseline is a flat line that hides the
 * whole day's shape.
 */
export function sparkPaths(
  values: readonly number[] | null | undefined,
  w = 120,
  h = 38,
  { autoMin = false }: SparkOptions = {},
): [line: string, fill: string] {
  if (!values || values.length < 2) return ["", ""];
  const hi = Math.max(...values, autoMin ? Number.NEGATIVE_INFINITY : 1);
  const lo = autoMin ? Math.min(...values) : 0;
  const span = hi - lo || 1;
  const pad = 3;
  const step = w / (values.length - 1);
  const y = (v: number) => h - pad - ((v - lo) / span) * (h - pad * 2);
  const at = (i: number) => values[i] as number;

  let d = `M0 ${y(at(0)).toFixed(2)}`;
  for (let i = 1; i < values.length; i++) {
    const x0 = (i - 1) * step;
    const x1 = i * step;
    const xm = (x0 + x1) / 2;
    // Smooth the line so a jittery token rate does not look like a seismograph.
    d += ` C${xm.toFixed(2)} ${y(at(i - 1)).toFixed(2)}, ${xm.toFixed(2)} ${y(at(i)).toFixed(
      2,
    )}, ${x1.toFixed(2)} ${y(at(i)).toFixed(2)}`;
  }
  const fill = `${d} L${w} ${h} L0 ${h} Z`;
  return [d, fill];
}
