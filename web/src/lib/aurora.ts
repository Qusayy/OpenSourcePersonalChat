/* The aurora.
 *
 * A strip of light that rises from the top edge of the status line while this
 * visitor's request is being read or written, and at no other time. How bright
 * it is and how high it reaches follow the measured tokens per second, as a
 * share of the fastest steady rate this page has seen, with a small lift as
 * each token lands; while the model is still reading the prompt it holds a
 * slow, low swell. It is the live colour along the line, turning violet where
 * it reaches highest, and it obeys the same rule as everything else that wears
 * the live colour: if nothing is in progress, it is not there. (The curtains
 * under the top bar, curtains.ts, are the aurora at rest; this is the aurora
 * at work.)
 *
 * Two renderers drive off one energy value:
 *
 *   css   a strip whose opacity is the energy. Works everywhere, and is what
 *         shows until (or unless) the shader is ready.
 *   gl    a WebGL2 fragment shader: curtains with rays, on a transparent
 *         canvas. Half resolution, DPR capped, and only while energy > 0.
 *
 * Under reduced motion there is no animation at all: a still, faint tint for
 * as long as the request runs.
 *
 * The WebGL scaffolding (one oversized triangle, half-resolution buffer, the
 * way context loss is handled) comes from the background this project used to
 * have. The shader itself is new, and so are four things that one got wrong
 * for this job: it wrote opaque pixels, it eased per frame (so it ran twice as
 * fast on a 120 Hz screen), it never stopped, and its noise had steps in it
 * (see `hash` in gl.ts, which the curtains share).
 *
 * Nothing happens when this module is imported. `mountAurora` is given the
 * strip's element once it exists and returns the way to let go of it; until
 * then, and after, `aurora.phase`, `aurora.rate` and `aurora.pulse` do nothing.
 */

import { onFrame, reduced } from "./frame";
import { buildProgram, fitCanvas, isDark, NOISE, type RGB, readColours, type Size } from "./gl";

const FRAG = `#version 300 es
precision highp float;

uniform vec2  u_res;
uniform float u_time;
uniform float u_energy;   // 0..1
uniform vec3  u_colour;   // the live colour, as sRGB: low down, along the line
uniform vec3  u_high;     // the violet the light turns toward as it climbs
uniform float u_peak;     // the most alpha any pixel may reach
uniform float u_curve;    // below 1, lifts the middle of the range; the top stays put
out vec4 fragColor;
${NOISE}
void main() {
  vec2 uv = gl_FragCoord.xy / u_res;        // origin bottom-left: 0 is the status line
  // Horizontal distance in strip-heights, so a curtain is the same width on a
  // phone and on a wide monitor rather than stretched to fit.
  float x = gl_FragCoord.x / u_res.y;
  float t = u_time;
  float e = u_energy;

  // The whole curtain wanders slowly sideways; within it, broad rays climb.
  // Every scale here is wide (the narrowest feature is about a quarter of the
  // strip's height across), so neighbouring columns never differ by a step.
  float wander = soft(vec2(x * 0.35 + t * 0.03, t * 0.05));
  float fold   = soft(vec2(x * 0.7 + wander * 1.6 - t * 0.04, 7.3));
  float rays   = soft(vec2(x * 1.9 + wander * 2.2, uv.y * 0.55 - t * 0.22));

  // How high each column reaches. Energy lifts the whole curtain.
  float reach = (0.3 + 0.7 * fold) * (0.46 + 0.78 * e);
  float body  = 1.0 - smoothstep(0.0, reach, uv.y);
  float a = pow(body, 1.7) * (0.6 + 0.5 * rays);
  a += 0.25 * exp(-uv.y * 12.0);            // a brighter hem along the status line
  // The tallest column must still end in air, never against the strip's edge.
  a *= 1.0 - smoothstep(0.72, 1.0, uv.y);
  // On a dark sheet a faint green is close to no green at all, so there the
  // middle of the range is lifted. 1 raised to anything is still 1: the
  // ceiling, which is what the text over it has to be read against, holds.
  a = pow(clamp(a, 0.0, 1.0), u_curve) * e * u_peak;

  // Dither against banding, but only where there is light to dither: a
  // transparent pixel must stay exactly transparent.
  a += (grain(gl_FragCoord.xy + t) - 0.5) * (2.0 / 255.0) * step(0.004, a);
  // The dither must not carry a pixel past the ceiling it was promised.
  a = clamp(a, 0.0, u_peak);

  // Green along the line, violet where the light reaches highest, as a real
  // aurora is green low down and turns violet above.
  vec3 colour = mix(u_colour, u_high, smoothstep(0.12, 0.7, uv.y / reach));
  fragColor = vec4(colour * a, a);          // premultiplied
}`;

// The most alpha the effect may reach, per scheme. Set so that secondary text
// lying over the brightest part still clears 4.5:1 against what is behind it.
const PEAK = { light: 0.24, dark: 0.24 };
// How the range below that ceiling is spread (the shader's u_curve). Light is
// left as it falls; dark is lifted, or most of the curtain would be too close
// to the sheet to see.
const CURVE = { light: 1, dark: 0.55 };
// Brightness is the rate as a share of the fastest steady rate this page has
// seen, not of any fixed number: a machine that writes six tokens a second and
// one that writes sixty are both at full brightness when writing at their own
// full speed. Until a first answer has run long enough to say what that speed
// is, the rate is measured against itself, which is to say at full.
const RATE_FLOOR = 1; // tok/s; below this, "full speed" would be noise
const STEADY_AFTER = 2500; // ms of writing before a rate is taken to be steady

type UniformName = "res" | "time" | "energy" | "colour" | "high" | "peak" | "curve";
type Uniforms = Record<UniformName, WebGLUniformLocation | null>;

/** How the strip is being drawn, as written to its `data-mode`. */
export type AuroraMode = "still" | "css" | "gl";

let host: HTMLElement | null = null;
let canvas: HTMLCanvasElement | null = null;

let gl: WebGL2RenderingContext | null = null;
let glCanvas: HTMLCanvasElement | null = null; // the canvas `gl` was made from
let uniforms: Uniforms | null = null;
let glOk = false;
let colour: RGB = [0, 0.4, 0.2];
let high: RGB = [0.4, 0.27, 0.66];
let peak = PEAK.light;
let curve = CURVE.light;
const size: Size = { width: 0, height: 0 };

let waiting = false;
let writing = false;
let rate = 0;
let steady = 0; // the rate, smoothed over the last few samples
let best = 0; // the fastest steady rate seen on this page
let writingSince = 0;
let lift = 0; // the per-token pulse, decaying
let energy = 0;
let off: (() => void) | null = null; // unsubscribes the frame loop
let started = 0; // when the strip was first mounted; the shader's clock runs from here
let unmount: (() => void) | null = null;

/** The live colour and the violet, as the shader's numbers. Also decides which
 *  scheme's ceiling applies, from the sheet behind the strip. */
function resolveColour(): void {
  if (!host) return;
  const style = getComputedStyle(host);
  const read = readColours(
    style.color,
    getComputedStyle(document.body).backgroundColor,
    style.getPropertyValue("--aurora-violet").trim(),
  );
  if (!read) return;
  const [low, sheet, violet] = read as [RGB, RGB, RGB];
  colour = low;
  high = violet;
  const dark = isDark(sheet);
  peak = dark ? PEAK.dark : PEAK.light;
  curve = dark ? CURVE.dark : CURVE.light;
  host.style.setProperty("--peak", String(peak));
}

// Canvases that already tell us when their context goes.
const watched = new WeakSet<HTMLCanvasElement>();

function onContextLost(event: Event): void {
  // A lost context is not rebuilt mid-session; the CSS strip takes over and
  // nothing visibly breaks.
  event.preventDefault();
  if (event.currentTarget !== glCanvas) return;
  glOk = false;
  if (host && canvas === glCanvas) host.dataset.mode = "css";
}

/** Create the context and compile the shader. Called when the page is idle,
 *  never in the task that handles the first send: compiling there would land
 *  on the same frames as the composer's move. */
function initGL(): void {
  if (!host || !canvas || glOk || reduced()) return;
  const ctx = canvas.getContext("webgl2", {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: "low-power",
  });
  if (!ctx || ctx.isContextLost()) return; // the CSS strip keeps the job
  gl = ctx;
  glCanvas = canvas;
  const program = buildProgram(ctx, FRAG, "aurora");
  if (!program) return;
  uniforms = {
    res: ctx.getUniformLocation(program, "u_res"),
    time: ctx.getUniformLocation(program, "u_time"),
    energy: ctx.getUniformLocation(program, "u_energy"),
    colour: ctx.getUniformLocation(program, "u_colour"),
    high: ctx.getUniformLocation(program, "u_high"),
    peak: ctx.getUniformLocation(program, "u_peak"),
    curve: ctx.getUniformLocation(program, "u_curve"),
  };
  glOk = true;
  host.dataset.mode = "gl";

  if (!watched.has(canvas)) {
    watched.add(canvas);
    canvas.addEventListener("webglcontextlost", onContextLost);
  }
}

function paint(seconds: number): void {
  if (!host) return;
  // The fallback and the measure of the effect for tests: always set.
  host.style.setProperty("--e", energy.toFixed(3));
  if (!glOk || !gl || !uniforms || !canvas || gl.isContextLost() || !fitCanvas(gl, canvas, size)) {
    return;
  }
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.uniform2f(uniforms.res, size.width, size.height);
  gl.uniform1f(uniforms.time, seconds);
  gl.uniform1f(uniforms.energy, energy);
  gl.uniform3f(uniforms.colour, colour[0], colour[1], colour[2]);
  gl.uniform3f(uniforms.high, high[0], high[1], high[2]);
  gl.uniform1f(uniforms.peak, peak);
  gl.uniform1f(uniforms.curve, curve);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
}

function frame(now: number, dt: number): void {
  if (!host) return;
  const seconds = (now - started) / 1000;
  let goal = 0;
  if (writing) {
    const share = Math.min(1, rate / Math.max(RATE_FLOOR, best, rate));
    goal = 0.35 + 0.65 * share;
  }
  // Reading the prompt: a slow swell that says the model has the request,
  // without pretending to be progress, and leaves somewhere for the first
  // token to land.
  else if (waiting) goal = 0.2 + 0.06 * Math.sin(seconds * 1.7);

  // Time-based, so it behaves the same at 60 and at 120 frames a second.
  lift *= Math.exp(-dt / 180);
  energy += (Math.min(1, goal + lift) - energy) * (1 - Math.exp(-dt / 380));

  if (!writing && !waiting && energy < 0.004) {
    // Faded out: stop asking for frames and take the element away entirely.
    energy = 0;
    host.style.setProperty("--e", "0");
    host.hidden = true;
    off?.();
    off = null;
    return;
  }
  paint(seconds);
}

function wake(): void {
  if (!host) return;
  host.hidden = false;
  if (reduced()) return; // a still tint; nothing to animate
  if (!off) off = onFrame(frame);
}

export const aurora = {
  /**
   * Follow a request phase. "reading" is the model taking in the prompt, and
   * "thinking" and "writing" are the model producing tokens: those light it.
   * "working" is the moment between sending and the server saying which it
   * will be, or a tool at work, and the answer may be "queued", which is
   * someone else's work and not this visitor's, or "asking", which is the
   * visitor's own turn. Nothing is lit until the server has said the model
   * is on it.
   */
  phase(key: string | null | undefined): void {
    if (!host) return;
    const was = writing;
    waiting = key === "reading";
    writing = key === "writing" || key === "thinking";
    if (writing && !was) {
      writingSince = performance.now();
      steady = 0;
    }
    if (!writing) rate = 0;
    if (waiting || writing) wake();
    else if (reduced()) host.hidden = true;
    // otherwise the frame loop fades it out and hides it
  },
  /** The measured rate, in tokens per second; called on each sample, four a second. */
  rate(tokensPerSecond: number | null | undefined): void {
    rate = Math.max(0, tokensPerSecond || 0);
    if (!writing) return;
    steady += (rate - steady) * 0.25;
    // The first samples of an answer are a few tokens over a fraction of a
    // second and overstate the rate; they are not allowed to set the bar.
    if (performance.now() - writingSince >= STEADY_AFTER) best = Math.max(best, steady);
  },
  /** A token has just landed. */
  pulse(): void {
    lift = Math.min(0.25, lift + 0.05);
  },
};

/**
 * Take charge of the strip: `el` is the element, with its `<canvas>` inside.
 * Returns the way to let go of it again.
 *
 * Letting go stops the frames and puts the strip at rest, but leaves the
 * WebGL context alone: a context that is thrown away is not given back, and
 * the same canvas handed over a second time (which is what a development
 * build does to every mounted element) carries on with the shader it had.
 * What the page has learnt about its own full speed is kept as well.
 *
 * A request already in flight is not picked up by mounting; say its phase
 * again with `aurora.phase`.
 */
export function mountAurora(el: HTMLElement): () => void {
  unmount?.();
  host = el;
  canvas = el.querySelector("canvas");
  if (canvas !== glCanvas) {
    // A different canvas: whatever was compiled belongs to the old one.
    gl = null;
    glCanvas = null;
    uniforms = null;
    glOk = false;
    size.width = size.height = 0;
  }
  if (!started) started = performance.now();

  el.dataset.mode = (reduced() ? "still" : glOk ? "gl" : "css") satisfies AuroraMode;
  resolveColour();
  // The live colour differs by scheme, and the scheme can change under us.
  const scheme = window.matchMedia("(prefers-color-scheme: dark)");
  scheme.addEventListener("change", resolveColour);

  let idle: number | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  if (!reduced()) {
    if (typeof window.requestIdleCallback === "function") {
      idle = window.requestIdleCallback(initGL, { timeout: 3000 });
    } else timer = setTimeout(initGL, 1200);
  }

  const release = () => {
    if (unmount !== release) return; // already let go
    unmount = null;
    scheme.removeEventListener("change", resolveColour);
    if (idle !== null) window.cancelIdleCallback(idle);
    if (timer !== null) clearTimeout(timer);
    off?.();
    off = null;
    waiting = writing = false;
    rate = 0;
    lift = 0;
    energy = 0;
    el.style.setProperty("--e", "0");
    el.hidden = true;
    host = null;
    canvas = null;
  };
  unmount = release;
  return release;
}

if (import.meta.hot) import.meta.hot.dispose(() => unmount?.());
