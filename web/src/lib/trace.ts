/* The rate, live.
 *
 * The meter measures four times a second. Shown raw, the number flips and the
 * line jerks four times a second. So what is on screen moves every frame
 * toward what was last measured: the figure eases to the new rate and the
 * line is drawn a quarter of a second behind now, which is exactly enough for
 * its head to glide from one sample to the next. Nothing is invented: every
 * point on the line is a measured sample or lies on the straight line between
 * two of them.
 */

import { fmt } from "./format";
import { onFrame, reduced } from "./frame";

export const TRACE_SPAN = 12000; // ms of history across the full width
export const TRACE_LAG = 260; // drawn this far behind now; just over one sample interval

export interface TraceOptions {
  /** The canvas the line is drawn on. Asked for each time, so it may come and go. */
  canvas: () => HTMLCanvasElement | null | undefined;
  /** The element that carries the tokens-per-second figure as its text. */
  figure: () => HTMLElement | null | undefined;
  /** Told each measured rate as it arrives, before anything is drawn from it. */
  onRate?: (rate: number) => void;
}

export interface Trace {
  /** A request's first token has landed: forget the last line and start drawing. */
  start(): void;
  /** The request is over: stop drawing and wipe the line. */
  stop(): void;
  /** A measured rate, and when it was measured on the `performance.now()` clock. */
  sample(rate: number, at: number): void;
}

interface Sample {
  t: number;
  v: number;
}

export function createTrace({ canvas, figure, onRate }: TraceOptions): Trace {
  let samples: Sample[] = [];
  let shown = 0;
  let target = 0;
  let top = 1;
  let ink = "";
  let off: (() => void) | null = null;
  let ctx: CanvasRenderingContext2D | null = null;
  let w = 0;
  let h = 0;

  /** Match the canvas's pixels to its box. False while it has no box (hidden). */
  function sizeCanvas(): boolean {
    const c = canvas();
    if (!c?.clientWidth || !c.clientHeight) return false;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cw = Math.round(c.clientWidth * dpr);
    const ch = Math.round(c.clientHeight * dpr);
    if (c.width !== cw || c.height !== ch) {
      c.width = cw;
      c.height = ch;
    }
    ctx = c.getContext("2d");
    if (!ctx) return false;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    w = c.clientWidth;
    h = c.clientHeight;
    return true;
  }

  /** The measured rate at time `t`: a sample, or the line between two. */
  function rateAt(t: number): number {
    const s = samples;
    const first = s[0] as Sample;
    if (t <= first.t) return first.v;
    for (let i = 1; i < s.length; i++) {
      const b = s[i] as Sample;
      if (t <= b.t) {
        const a = s[i - 1] as Sample;
        return a.v + (b.v - a.v) * ((t - a.t) / (b.t - a.t || 1));
      }
    }
    return (s[s.length - 1] as Sample).v;
  }

  function draw(now: number): void {
    if (!sizeCanvas() || !ctx) return;
    ctx.clearRect(0, 0, w, h);
    if (samples.length < 2) return;

    const head = now - TRACE_LAG;
    // Same scale as before: against the tallest sample, or 1 tok/s. The scale
    // eases as well, so a new peak does not make the whole line jump.
    const peak = Math.max(1, ...samples.map((s) => s.v));
    top += (peak - top) * (reduced() ? 1 : 0.1);
    const pad = 3;
    const right = w - 3;
    const x = (t: number) => right - ((head - t) / TRACE_SPAN) * right;
    const y = (v: number) => h - pad - (Math.min(v, top) / top) * (h - pad * 2);

    ctx.beginPath();
    let drawing = false;
    for (const s of samples) {
      if (s.t > head) break;
      const px = x(s.t);
      if (px < -6) continue;
      if (drawing) ctx.lineTo(px, y(s.v));
      else ctx.moveTo(px, y(s.v));
      drawing = true;
    }
    const nowV = rateAt(head);
    if (drawing) ctx.lineTo(right, y(nowV));
    else ctx.moveTo(right, y(nowV));
    ctx.strokeStyle = ctx.fillStyle = ink;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = ctx.lineCap = "round";
    ctx.stroke();
    // where the rate is now
    ctx.beginPath();
    ctx.arc(right, y(nowV), 2.5, 0, Math.PI * 2);
    ctx.fill();
  }

  function frame(now: number, dt: number): void {
    shown += (target - shown) * (1 - Math.exp(-dt / 140));
    const text = fmt(shown, 1);
    const el = figure();
    if (el && el.textContent !== text) el.textContent = text;
    draw(now);
  }

  return {
    start() {
      samples = [];
      shown = target = 0;
      top = 1;
      const c = canvas();
      ink = c ? getComputedStyle(c).color : "";
      if (!reduced() && !off) off = onFrame(frame);
    },

    stop() {
      off?.();
      off = null;
      samples = [];
      if (ctx) ctx.clearRect(0, 0, w, h);
    },

    sample(rate, at) {
      samples.push({ t: at, v: rate });
      while (samples.length > 2 && at - (samples[0] as Sample).t > TRACE_SPAN + 1000) {
        samples.shift();
      }
      target = rate;
      onRate?.(rate);
      if (reduced()) {
        // No frame loop: the figure and the line step from sample to sample.
        const el = figure();
        if (el) el.textContent = fmt(rate, 1);
        draw(at + TRACE_LAG);
      }
    },
  };
}
