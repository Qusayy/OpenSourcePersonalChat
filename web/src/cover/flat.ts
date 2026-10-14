/* The sky where there is no WebGL2 to draw it with.
 *
 * No aurora and no foreground: a plain night, the same stars in the same
 * places, and the same lines between them. It is not there to be looked at.
 * It is there so that the stars can still be joined, on whatever device the
 * site's owner happens to be holding.
 *
 * The night and its stars are painted once, into a canvas of their own, and
 * copied to the screen; a frame is drawn only while a line is on it.
 */

import { onFrame } from "../lib/frame";
import { ANCHORS } from "./pattern";
import { MAX_SEGMENTS, mountSky, type Overlay, type Sky, type SkyStats, type View } from "./sky";
import { buildStars, STAR_STRIDE } from "./stars";

/**
 * The drawn sky if this browser can draw it, the plain one if it cannot.
 * Returns the sky with the canvas it is on, which may not be the one handed
 * in: a canvas that has been asked for one kind of context will not give
 * another, so the plain sky is given a canvas of its own in the same place.
 */
export function mountAnySky(
  canvas: HTMLCanvasElement,
): { sky: Sky; canvas: HTMLCanvasElement } | null {
  const drawn = mountSky(canvas);
  if (drawn) return { sky: drawn, canvas };
  const plain = canvas.cloneNode(false) as HTMLCanvasElement;
  canvas.replaceWith(plain);
  const flat = mountFlatSky(plain);
  return flat ? { sky: flat, canvas: plain } : null;
}

export function mountFlatSky(canvas: HTMLCanvasElement): Sky | null {
  const ctx = canvas.getContext("2d", { alpha: false });
  const backdrop = document.createElement("canvas");
  const back = backdrop.getContext("2d", { alpha: false });
  if (!ctx || !back) return null;

  const view: View = { width: 0, height: 0, unit: 1 };
  const overlay: Overlay = {
    segments: new Float32Array(MAX_SEGMENTS * 5),
    count: 0,
    glow: new Float32Array(ANCHORS.length),
    active: false,
  };
  const stats: SkyStats = { frame: 0, goal: 0, scale: 1, steps: 0 };
  const stars = buildStars();
  let dpr = 1;
  let stale = true;
  let wasActive = false;

  /** Units to canvas pixels. */
  const px = (x: number): number => (view.width / 2 + x * view.unit) * dpr;
  const py = (y: number): number => (view.height / 2 - y * view.unit) * dpr;

  function paintBackdrop(): void {
    if (!back) return;
    const w = canvas.width;
    const h = canvas.height;
    backdrop.width = w;
    backdrop.height = h;
    const night = back.createLinearGradient(0, h, 0, 0);
    night.addColorStop(0, "#0b1620");
    night.addColorStop(0.38, "#050a14");
    night.addColorStop(1, "#03060c");
    back.fillStyle = night;
    back.fillRect(0, 0, w, h);
    // The same share of the field as the drawn sky shows on a view this size.
    const density = Math.min(1, Math.max(0.1, (view.unit / 1080) ** 2));
    for (let o = 0; o < stars.length; o += STAR_STRIDE) {
      if (stars[o + 5]! > density) continue;
      const x = px(stars[o]!);
      const y = py(stars[o + 1]!);
      if (x < 0 || y < 0 || x > w || y > h) continue;
      const radius = Math.max(0.6, stars[o + 2]! * dpr * 0.8);
      back.globalAlpha = Math.min(1, stars[o + 3]! * 0.7);
      back.fillStyle = "#ffffff";
      back.beginPath();
      back.arc(x, y, radius, 0, Math.PI * 2);
      back.fill();
    }
    back.globalAlpha = 1;
  }

  function layout(): void {
    stale = false;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) {
      view.width = 0;
      return;
    }
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    view.width = w;
    view.height = h;
    view.unit = Math.min(w, h);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    paintBackdrop();
  }

  function draw(): void {
    if (!ctx) return;
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(backdrop, 0, 0);
    if (!overlay.active) return;
    // Light adds to light, as it does in the drawn sky.
    ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < ANCHORS.length; i++) {
      const g = overlay.glow[i]!;
      if (g < 0.004) continue;
      const a = ANCHORS[i]!;
      const x = px(a.x);
      const y = py(a.y);
      const reach = (10 + 8 * g) * dpr;
      const halo = ctx.createRadialGradient(x, y, 0, x, y, reach);
      halo.addColorStop(0, `rgb(210 232 255 / ${Math.min(1, 0.55 * g)})`);
      halo.addColorStop(1, "rgb(210 232 255 / 0)");
      ctx.fillStyle = halo;
      ctx.fillRect(x - reach, y - reach, reach * 2, reach * 2);
    }
    ctx.lineCap = "round";
    ctx.lineWidth = 1.5 * dpr;
    for (let i = 0; i < overlay.count; i++) {
      const s = i * 5;
      ctx.strokeStyle = `rgb(184 227 255 / ${Math.min(1, overlay.segments[s + 4]!)})`;
      ctx.beginPath();
      ctx.moveTo(px(overlay.segments[s]!), py(overlay.segments[s + 1]!));
      ctx.lineTo(px(overlay.segments[s + 2]!), py(overlay.segments[s + 3]!));
      ctx.stroke();
    }
  }

  function step(now: number, dt: number): void {
    sky.onStep?.(now, dt);
    const changed = stale;
    if (changed) layout();
    if (!view.width) return;
    const busy = overlay.active || wasActive;
    wasActive = overlay.active;
    if (!changed && !busy) return;
    draw();
    canvas.dataset.ready = "";
  }

  const onResize = (): void => {
    stale = true;
  };
  window.addEventListener("resize", onResize);
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(onResize) : null;
  observer?.observe(canvas);
  const off = onFrame(step);

  const sky: Sky = {
    view,
    overlay,
    stats,
    // One sky here, whatever is asked for.
    scene: 0,
    onStep: null,
    release() {
      off();
      observer?.disconnect();
      window.removeEventListener("resize", onResize);
    },
  };
  return sky;
}
