/* The aurora.
 *
 * A strip of light that rises from the top edge of the status line while this
 * visitor's request is being read or written, and at no other time. How bright
 * it is and how high it reaches follow the measured tokens per second, as a
 * share of the fastest steady rate this page has seen, with a small lift as
 * each token lands; while the model is still reading the prompt it holds a
 * slow, low swell. It is the page's one luminous thing, and it is the live
 * colour, so it obeys the same rule as everything else that wears that colour:
 * if nothing is in progress, it is not there.
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
 * (see `hash`).
 */

import { onFrame, REDUCED } from "./motion.js";

const host = document.querySelector("#aurora");
const canvas = host?.querySelector("canvas");

const VERT = `#version 300 es
void main() {
  // One oversized triangle covers the strip with no vertex buffer at all.
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;

uniform vec2  u_res;
uniform float u_time;
uniform float u_energy;   // 0..1
uniform vec3  u_colour;   // the live colour, as sRGB
uniform float u_peak;     // the most alpha any pixel may reach
uniform float u_curve;    // below 1, lifts the middle of the range; the top stays put
out vec4 fragColor;

// A value for each point of the lattice, from integer arithmetic, which is
// exact. The usual float hash (fract of a large product) is not: the same
// lattice point, reached as i + 1 from one cell and as i from the next,
// could round differently and come out as two unrelated values. That is a
// step in the noise at a cell's edge, and it drew the light as hard-edged
// vertical bands.
float hash(vec2 p) {
  uvec2 q = uvec2(ivec2(p)) * uvec2(1597334673u, 3812015801u);
  uint n = (q.x ^ q.y) * 1597334673u;
  return float(n >> 8) * (1.0 / 16777216.0);
}

// For the dither only, where a value that changes every frame is wanted and
// continuity means nothing.
float grain(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}

// Two octaves, renormalised to 0..1: broad shapes, with one finer layer for
// the rays.
float soft(vec2 p) {
  return (noise(p) + 0.5 * noise(p * 2.03 + 17.1)) / 1.5;
}

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

  fragColor = vec4(u_colour * a, a);        // premultiplied
}`;

// The most alpha the effect may reach, per scheme. Set so that secondary text
// lying over the brightest part still clears 4.5:1 against what is behind it.
const PEAK = { light: 0.24, dark: 0.24 };
// How the range below that ceiling is spread (the shader's u_curve). Light is
// left as it falls; dark is lifted, or most of the curtain would be too close
// to the sheet to see.
const CURVE = { light: 1, dark: 0.55 };
// Half resolution, upscaled by CSS: the light is soft, and this is the single
// biggest saving a fragment-heavy effect has.
const SCALE = 0.5;
// Brightness is the rate as a share of the fastest steady rate this page has
// seen, not of any fixed number: a machine that writes six tokens a second and
// one that writes sixty are both at full brightness when writing at their own
// full speed. Until a first answer has run long enough to say what that speed
// is, the rate is measured against itself, which is to say at full.
const RATE_FLOOR = 1; // tok/s; below this, "full speed" would be noise
const STEADY_AFTER = 2500; // ms of writing before a rate is taken to be steady

let gl = null;
let uniforms = null;
let glOk = false;
let colour = [0, 0.4, 0.2];
let peak = PEAK.light;
let curve = CURVE.light;
let width = 0;
let height = 0;

let waiting = false;
let writing = false;
let rate = 0;
let steady = 0; // the rate, smoothed over the last few samples
let best = 0; // the fastest steady rate seen on this page
let writingSince = 0;
let lift = 0; // the per-token pulse, decaying
let energy = 0;
let off = null; // unsubscribes the frame loop
const started = performance.now();

/** `--live` is an oklch() token; a shader needs numbers. Paint one pixel with
 *  it and read the pixel back. Also decides which scheme's ceiling applies. */
function resolveColour() {
  const probe = document.createElement("canvas");
  probe.width = probe.height = 1;
  const ctx = probe.getContext("2d", { willReadFrequently: true });
  if (!ctx) return;
  ctx.fillStyle = getComputedStyle(host).color;
  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  colour = [r / 255, g / 255, b / 255];
  ctx.clearRect(0, 0, 1, 1);
  ctx.fillStyle = getComputedStyle(document.body).backgroundColor;
  ctx.fillRect(0, 0, 1, 1);
  const [sr, sg, sb] = ctx.getImageData(0, 0, 1, 1).data;
  const dark = sr + sg + sb < 384;
  peak = dark ? PEAK.dark : PEAK.light;
  curve = dark ? CURVE.dark : CURVE.light;
  host.style.setProperty("--peak", String(peak));
}

function compile(type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.warn("[aurora] shader failed:", gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

/** Create the context and compile the shader. Called when the page is idle,
 *  never in the task that handles the first send: compiling there would land
 *  on the same frames as the composer's move. */
function initGL() {
  if (!canvas || glOk || REDUCED) return;
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
  const vs = compile(gl.VERTEX_SHADER, VERT);
  const fs = compile(gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return;
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.warn("[aurora] link failed:", gl.getProgramInfoLog(program));
    return;
  }
  gl.useProgram(program);
  gl.bindVertexArray(gl.createVertexArray());
  gl.clearColor(0, 0, 0, 0);
  uniforms = {
    res: gl.getUniformLocation(program, "u_res"),
    time: gl.getUniformLocation(program, "u_time"),
    energy: gl.getUniformLocation(program, "u_energy"),
    colour: gl.getUniformLocation(program, "u_colour"),
    peak: gl.getUniformLocation(program, "u_peak"),
    curve: gl.getUniformLocation(program, "u_curve"),
  };
  glOk = true;
  host.dataset.mode = "gl";

  canvas.addEventListener("webglcontextlost", (event) => {
    // A lost context is not rebuilt mid-session; the CSS strip takes over and
    // nothing visibly breaks.
    event.preventDefault();
    glOk = false;
    host.dataset.mode = "css";
  });
}

function fit() {
  // Before layout the canvas reports 0×0; drawing then would stretch a single
  // pixel across the strip.
  if (!canvas.clientWidth || !canvas.clientHeight) return false;
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  const w = Math.max(1, Math.floor(canvas.clientWidth * dpr * SCALE));
  const h = Math.max(1, Math.floor(canvas.clientHeight * dpr * SCALE));
  if (w !== width || h !== height) {
    width = canvas.width = w;
    height = canvas.height = h;
    gl.viewport(0, 0, w, h);
  }
  return true;
}

function paint(seconds) {
  // The fallback and the measure of the effect for tests: always set.
  host.style.setProperty("--e", energy.toFixed(3));
  if (!glOk || gl.isContextLost() || !fit()) return;
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.uniform2f(uniforms.res, width, height);
  gl.uniform1f(uniforms.time, seconds);
  gl.uniform1f(uniforms.energy, energy);
  gl.uniform3f(uniforms.colour, colour[0], colour[1], colour[2]);
  gl.uniform1f(uniforms.peak, peak);
  gl.uniform1f(uniforms.curve, curve);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
}

function frame(now, dt) {
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

function wake() {
  if (!host) return;
  host.hidden = false;
  if (REDUCED) return; // a still tint; nothing to animate
  if (!off) off = onFrame(frame);
}

export const aurora = {
  /**
   * Follow a request phase. "reading" is the model taking in the prompt and
   * "writing" is the model producing tokens: those two light it. "working" is
   * the moment between sending and the server saying which it will be, and
   * the answer may be "queued", which is someone else's work and not this
   * visitor's. Nothing is lit until the server has said the model is on it.
   */
  phase(key) {
    if (!host) return;
    const was = writing;
    waiting = key === "reading";
    writing = key === "writing";
    if (writing && !was) {
      writingSince = performance.now();
      steady = 0;
    }
    if (!writing) rate = 0;
    if (waiting || writing) wake();
    else if (REDUCED) host.hidden = true;
    // otherwise the frame loop fades it out and hides it
  },
  /** The measured rate, in tokens per second; called on each sample, four a second. */
  rate(tokensPerSecond) {
    rate = Math.max(0, tokensPerSecond || 0);
    if (!writing) return;
    steady += (rate - steady) * 0.25;
    // The first samples of an answer are a few tokens over a fraction of a
    // second and overstate the rate; they are not allowed to set the bar.
    if (performance.now() - writingSince >= STEADY_AFTER) best = Math.max(best, steady);
  },
  /** A token has just landed. */
  pulse() {
    lift = Math.min(0.25, lift + 0.05);
  },
};

if (host) {
  host.dataset.mode = REDUCED ? "still" : "css";
  resolveColour();
  // The live colour differs by scheme, and the scheme can change under us.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", resolveColour);
  if (!REDUCED) {
    if ("requestIdleCallback" in window) window.requestIdleCallback(initGL, { timeout: 3000 });
    else setTimeout(initGL, 1200);
  }
}
