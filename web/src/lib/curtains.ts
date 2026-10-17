/* The curtains.
 *
 * The aurora at rest: curtains of light hanging from the top bar across the
 * full width of the window, behind the headline of the empty chat page and the
 * head of the benchmark and about pages. Green at the hem and violet above it,
 * as a real aurora is. They sway slowly while nothing is happening. When a
 * conversation starts they fade, and the light at the status line (aurora.ts)
 * takes over; a new chat brings them back.
 *
 * Unlike that light, this one is there while nothing is in progress, so it is
 * held to a tighter budget. It draws at half resolution, at most about thirty
 * times a second, asks for a low-power context and refuses one drawn on the
 * processor. It stops asking for frames whenever it cannot be seen: a hidden
 * tab, a page scrolled past it, a conversation under way.
 *
 * How it is drawn, as written to `data-mode`:
 *
 *   wait   the shader has not been tried yet. Nothing is shown: the curtains
 *          fade in once it is ready, rather than jump from a stand-in.
 *   gl     the shader.
 *   still  a fixed tint from the stylesheet, in the same colours and under the
 *          same ceiling: reduced motion, no WebGL2, a software renderer, a
 *          shader that would not build, or a context that was lost.
 *
 * Nothing happens when this module is imported. `mountCurtains` is given the
 * band's element once it exists and returns the way to let go of it; until
 * then, and after, `curtains.rest` does nothing.
 */

import { onFrame, reduced } from "./frame";
import {
  buildProgram,
  density,
  fitCanvas,
  isDark,
  NOISE,
  type RGB,
  readColours,
  type Size,
  software,
} from "./gl";

const FRAG = `#version 300 es
precision highp float;

uniform vec2  u_res;
uniform float u_unit;     // buffer pixels per unit of curtain, so a curtain is
                          // the same width on every page and every screen
uniform float u_time;
uniform float u_energy;   // 0..1
uniform vec3  u_low;      // the green, at the hem
uniform vec3  u_high;     // the violet, above it
uniform float u_peak;     // the most alpha any pixel may reach
uniform float u_curve;    // below 1, lifts the middle of the range; the top stays put
out vec4 fragColor;
${NOISE}

// One curtain at depth d (0 under the top bar, 1 at the band's lower edge).
// Its hem hangs lower in some places than others and drifts. The light is
// brightest in a thin glow along the hem and falls away upward, never to
// nothing; fine rays run up it, and it is brighter where the sheet folds
// toward the viewer. Also gives how far up the curtain the point is: 0 at the
// hem, 1 at the top bar.
float curtain(float x, float d, float t, float seed, out float up) {
  float wander = soft(vec2(x * 0.22 + t * 0.015 + seed, t * 0.025 + seed));
  float fold   = soft(vec2(x * 0.5 + wander * 1.6 - t * 0.02, seed * 3.1));
  float hem    = 0.34 + 0.48 * fold;
  up = clamp(1.0 - d / hem, 0.0, 1.0);

  // Rays: fine streaks that run the height of the curtain and slide along it.
  float fine  = noise(vec2(x * 16.0 + wander * 6.0 - t * 0.25, d * 0.8 + seed));
  float broad = noise(vec2(x * 7.0 + wander * 4.0 + t * 0.12, d * 0.5 + seed + 3.0));
  float rays  = smoothstep(0.25, 0.95, 0.6 * fine + 0.4 * broad);
  // Where the sheet turns toward the viewer it is seen edge on, and brighter.
  float ridge = 1.0 - abs(2.0 * soft(vec2(x * 1.4 + wander * 2.0 - t * 0.03, seed + 9.0)) - 1.0);
  ridge *= ridge;

  // Under the hem, a short edge and then air.
  float hung = 1.0 - smoothstep(hem - 0.015, hem + 0.05, d);
  float body = 0.22 + 0.4 * exp(-up * 2.5) + 0.6 * exp(-up * 9.0);
  // The rays are sharpest low down and soften as they climb.
  float streak = mix(0.25 + 0.75 * rays, 1.0, 0.5 * up);
  return 1.7 * hung * body * streak * (0.55 + 0.65 * ridge);
}

void main() {
  float d = 1.0 - gl_FragCoord.y / u_res.y;
  float x = gl_FragCoord.x / u_unit;
  float t = u_time;

  // A near curtain, and a fainter one behind it that hangs higher and moves
  // more slowly.
  float upNear, upFar;
  float near = curtain(x, d, t, 0.0, upNear);
  float far  = 0.7 * curtain(x * 0.8 + 11.0, d * 1.3, t * 0.8, 5.3, upFar);

  // Green at the hem, violet higher up; the far curtain, being higher, is
  // more of it violet.
  vec3 cNear = mix(u_low, u_high, smoothstep(0.2, 0.85, upNear));
  vec3 cFar  = mix(u_low, u_high, smoothstep(0.0, 0.55, upFar));
  near = clamp(near, 0.0, 1.0);
  far = clamp(far, 0.0, 1.0);
  float a = near + far * (1.0 - near);
  vec3 colour = (cNear * near + cFar * far * (1.0 - near)) / max(a, 1e-4);

  // Hung from the bar, not cut off by it.
  a *= smoothstep(0.0, 0.08, d);
  // On a dark sheet a faint green is close to no green at all, so there the
  // middle of the range is lifted. 1 raised to anything is still 1: the
  // ceiling, which is what the text over it has to be read against, holds.
  a = pow(clamp(a, 0.0, 1.0), u_curve) * u_energy * u_peak;

  // Dither against banding, but only where there is light to dither: a
  // transparent pixel must stay exactly transparent.
  a += (grain(gl_FragCoord.xy + t) - 0.5) * (2.0 / 255.0) * step(0.004, a);
  // The dither must not carry a pixel past the ceiling it was promised.
  a = clamp(a, 0.0, u_peak);

  fragColor = vec4(colour * a, a);        // premultiplied
}`;

// The same ceiling as the aurora's, and the same spread below it: text over
// the brightest part of either still clears 4.5:1 (see DESIGN.md).
const PEAK = 0.24;
const CURVE = { light: 0.8, dark: 0.55 };
// How bright they hang at rest, and how far that breathes either way.
const REST = 0.85;
const BREATH = 0.08;
// The fewest milliseconds between two drawings: about thirty a second, on a
// 60 Hz screen every other frame. The light moves slowly; more would be spent
// on nothing.
const FRAME_MS = 30;
// CSS pixels to one unit of curtain.
const UNIT = 240;

type UniformName = "res" | "unit" | "time" | "energy" | "low" | "high" | "peak" | "curve";
type Uniforms = Record<UniformName, WebGLUniformLocation | null>;

/** How the band is being drawn, as written to its `data-mode`. */
export type CurtainsMode = "wait" | "gl" | "still";

let host: HTMLElement | null = null;
let canvas: HTMLCanvasElement | null = null;
let mode: CurtainsMode = "wait";

let gl: WebGL2RenderingContext | null = null;
let glCanvas: HTMLCanvasElement | null = null; // the canvas `gl` was made from
let uniforms: Uniforms | null = null;
let glOk = false;
let failed = false; // tried and refused; the still tint for the rest of the page's life
let low: RGB = [0.01, 0.42, 0.2];
let high: RGB = [0.4, 0.27, 0.66];
let curve = CURVE.light;
const size: Size = { width: 0, height: 0 };

let resting = false; // whether the page wants them hung
let energy = 0;
let fade = 1; // 1 with the page at its top, 0 once it is scrolled past the band
let since = 0; // milliseconds since the last drawing
let off: (() => void) | null = null; // unsubscribes the frame loop
let started = 0; // when the band was first mounted; the shader's clock runs from here
let unmount: (() => void) | null = null;

function setMode(next: CurtainsMode): void {
  mode = next;
  if (host) host.dataset.mode = next;
}

/** The green and the violet, as the shader's numbers, and which scheme's
 *  spread applies, from the sheet behind the band. */
function resolveColour(): void {
  if (!host) return;
  const style = getComputedStyle(host);
  const read = readColours(
    style.color,
    getComputedStyle(document.body).backgroundColor,
    style.getPropertyValue("--aurora-violet").trim(),
  );
  if (!read) return;
  const [green, sheet, violet] = read as [RGB, RGB, RGB];
  low = green;
  high = violet;
  curve = isDark(sheet) ? CURVE.dark : CURVE.light;
}

/** Whether frames are wanted now, and whether the band is on the page. */
function sync(): void {
  if (!host) return;
  if (mode !== "gl") {
    off?.();
    off = null;
    host.hidden = mode === "wait" || !resting;
    return;
  }
  const wanted = resting || energy > 0;
  host.hidden = !wanted;
  const run = wanted && fade > 0 && !document.hidden;
  if (run && !off) {
    since = FRAME_MS; // draw on the first frame, not the second
    off = onFrame(frame);
  } else if (!run && off) {
    off();
    off = null;
  }
}

// Canvases that already tell us when their context goes.
const watched = new WeakSet<HTMLCanvasElement>();

function onContextLost(event: Event): void {
  // A lost context is not rebuilt mid-session; the still tint takes over.
  event.preventDefault();
  if (event.currentTarget !== glCanvas) return;
  glOk = false;
  failed = true;
  if (host && canvas === glCanvas) {
    setMode("still");
    sync();
  }
}

/** Fall back to the still tint, for good. */
function refuse(): void {
  failed = true;
  setMode("still");
  sync();
}

/** Create the context and compile the shader. Called when the page is idle,
 *  never in the task that handles the first paint or the first send. */
function initGL(): void {
  if (!host || !canvas || glOk || failed || reduced()) return;
  const ctx = canvas.getContext("webgl2", {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: "low-power",
    // No context at all rather than one drawn on the processor, where a light
    // that is on all the time would cost every frame the page has.
    failIfMajorPerformanceCaveat: true,
  });
  if (!ctx || ctx.isContextLost()) {
    refuse();
    return;
  }
  if (software(ctx)) {
    // The flag above is not always honoured; given back, and the still tint.
    ctx.getExtension("WEBGL_lose_context")?.loseContext();
    refuse();
    return;
  }
  gl = ctx;
  glCanvas = canvas;
  const program = buildProgram(ctx, FRAG, "curtains");
  if (!program) {
    refuse();
    return;
  }
  uniforms = {
    res: ctx.getUniformLocation(program, "u_res"),
    unit: ctx.getUniformLocation(program, "u_unit"),
    time: ctx.getUniformLocation(program, "u_time"),
    energy: ctx.getUniformLocation(program, "u_energy"),
    low: ctx.getUniformLocation(program, "u_low"),
    high: ctx.getUniformLocation(program, "u_high"),
    peak: ctx.getUniformLocation(program, "u_peak"),
    curve: ctx.getUniformLocation(program, "u_curve"),
  };
  glOk = true;
  setMode("gl");
  if (!watched.has(canvas)) {
    watched.add(canvas);
    canvas.addEventListener("webglcontextlost", onContextLost);
  }
  sync();
}

function paint(seconds: number): void {
  if (!host) return;
  // The measure of the effect for tests: always set.
  host.style.setProperty("--e", energy.toFixed(3));
  if (!glOk || !gl || !uniforms || !canvas || gl.isContextLost() || !fitCanvas(gl, canvas, size)) {
    return;
  }
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.uniform2f(uniforms.res, size.width, size.height);
  gl.uniform1f(uniforms.unit, UNIT * density());
  gl.uniform1f(uniforms.time, seconds);
  gl.uniform1f(uniforms.energy, energy);
  gl.uniform3f(uniforms.low, low[0], low[1], low[2]);
  gl.uniform3f(uniforms.high, high[0], high[1], high[2]);
  gl.uniform1f(uniforms.peak, PEAK);
  gl.uniform1f(uniforms.curve, curve);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
}

function frame(now: number, dt: number): void {
  if (!host) return;
  since += dt;
  if (since < FRAME_MS) return;
  const step = since;
  since = 0;
  const seconds = (now - started) / 1000;
  const goal = resting ? REST + BREATH * Math.sin(seconds * 0.6) : 0;
  // Time-based, so it behaves the same at any frame rate: a slow arrival, a
  // quicker leave.
  energy += (goal - energy) * (1 - Math.exp(-step / (resting ? 900 : 200)));
  if (!resting && energy < 0.004) {
    // Faded out: stop asking for frames and take the element away entirely.
    energy = 0;
    host.style.setProperty("--e", "0");
    sync();
    return;
  }
  paint(seconds);
}

/** The page's scroller has moved: the band fades as the page leaves it. The
 *  page itself never scrolls; its sheet does, and the sheet is <main> or the
 *  one element directly inside it. Anything scrolling deeper (the composer's
 *  field, a code block) is not the page moving. */
function onScroll(event: Event): void {
  const el = event.target;
  if (!host || !(el instanceof HTMLElement)) return;
  if (el.id !== "main" && el.parentElement?.id !== "main") return;
  const height = host.clientHeight || 1;
  fade = Math.max(0, 1 - el.scrollTop / height);
  host.style.setProperty("--fade", fade.toFixed(3));
  sync();
}

export const curtains = {
  /**
   * Whether the page wants the curtains hung. The empty chat page and the
   * long pages do; a conversation does not. Each change starts the page at
   * its top, so the band is unfaded.
   */
  rest(on: boolean): void {
    if (!host) return;
    resting = on;
    fade = 1;
    host.style.setProperty("--fade", "1");
    sync();
  },
};

/**
 * Take charge of the band: `el` is the element, with its `<canvas>` inside.
 * Returns the way to let go of it again.
 *
 * As with the aurora, letting go stops the frames but leaves the WebGL
 * context alone, so the same canvas mounted again (as a development build
 * does) carries on with the shader it had.
 */
export function mountCurtains(el: HTMLElement): () => void {
  unmount?.();
  host = el;
  canvas = el.querySelector("canvas");
  if (canvas !== glCanvas) {
    // A different canvas: whatever was compiled belongs to the old one.
    gl = null;
    glCanvas = null;
    uniforms = null;
    glOk = false;
    failed = false;
    size.width = size.height = 0;
  }
  if (!started) started = performance.now();

  setMode(reduced() || failed ? "still" : glOk ? "gl" : "wait");
  resolveColour();
  const scheme = window.matchMedia("(prefers-color-scheme: dark)");
  scheme.addEventListener("change", resolveColour);
  document.addEventListener("visibilitychange", sync);
  document.addEventListener("scroll", onScroll, { capture: true, passive: true });

  let idle: number | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  if (mode === "wait") {
    if (typeof window.requestIdleCallback === "function") {
      idle = window.requestIdleCallback(initGL, { timeout: 3000 });
    } else timer = setTimeout(initGL, 1200);
  }
  sync();

  const release = () => {
    if (unmount !== release) return; // already let go
    unmount = null;
    scheme.removeEventListener("change", resolveColour);
    document.removeEventListener("visibilitychange", sync);
    document.removeEventListener("scroll", onScroll, { capture: true });
    if (idle !== null) window.cancelIdleCallback(idle);
    if (timer !== null) clearTimeout(timer);
    off?.();
    off = null;
    resting = false;
    energy = 0;
    fade = 1;
    el.style.setProperty("--e", "0");
    el.hidden = true;
    host = null;
    canvas = null;
  };
  unmount = release;
  return release;
}

if (import.meta.hot) import.meta.hot.dispose(() => unmount?.());
