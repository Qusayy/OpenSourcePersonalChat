/* Live throughput meter + sparkline.
 *
 * Tokens arrive at ~10/s but a DOM write per token still costs a layout pass
 * inside a growing thread. Everything here is sampled on a 250 ms timer from
 * counters the token handler only increments.
 */

const WINDOW_MS = 3000; // rolling window for the instantaneous rate
const HISTORY = 48; // sparkline samples

export class Meter {
  constructor({ onSample } = {}) {
    this.onSample = onSample;
    this.samples = []; // {t, n} token arrivals
    this.history = [];
    this.total = 0;
    this.timer = null;
    this.startedAt = 0;
  }

  start() {
    this.samples = [];
    this.history = [];
    this.total = 0;
    this.startedAt = performance.now();
    clearInterval(this.timer);
    this.timer = setInterval(() => this.tick(), 250);
  }

  token(n = 1) {
    this.total += n;
    this.samples.push({ t: performance.now(), n });
  }

  rate() {
    const now = performance.now();
    while (this.samples.length && now - this.samples[0].t > WINDOW_MS) this.samples.shift();
    if (this.samples.length < 2) return 0;
    const span = (now - this.samples[0].t) / 1000;
    if (span <= 0) return 0;
    const count = this.samples.reduce((a, s) => a + s.n, 0);
    return count / span;
  }

  tick() {
    const r = this.rate();
    this.history.push(r);
    if (this.history.length > HISTORY) this.history.shift();
    this.onSample?.({ rate: r, total: this.total, history: this.history });
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
    const elapsed = (performance.now() - this.startedAt) / 1000;
    return { total: this.total, avg: elapsed > 0 ? this.total / elapsed : 0 };
  }
}

/* Sparkline geometry for a 120x38 viewBox. Returns [linePath, fillPath]. */
export function sparkPaths(values, w = 120, h = 38) {
  if (!values || values.length < 2) return ["", ""];
  const max = Math.max(...values, 1);
  const pad = 3;
  const step = w / (values.length - 1);
  const y = (v) => h - pad - (v / max) * (h - pad * 2);

  let d = `M0 ${y(values[0]).toFixed(2)}`;
  for (let i = 1; i < values.length; i++) {
    const x0 = (i - 1) * step;
    const x1 = i * step;
    const xm = (x0 + x1) / 2;
    // Smooth the line so a jittery token rate does not look like a seismograph.
    d += ` C${xm.toFixed(2)} ${y(values[i - 1]).toFixed(2)}, ${xm.toFixed(2)} ${y(
      values[i]
    ).toFixed(2)}, ${x1.toFixed(2)} ${y(values[i]).toFixed(2)}`;
  }
  const fill = `${d} L${w} ${h} L0 ${h} Z`;
  return [d, fill];
}

export function fmt(n, digits = 1) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toFixed(digits);
}

export function fmtInt(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Math.round(n).toLocaleString();
}

export function fmtMs(ms) {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
}
