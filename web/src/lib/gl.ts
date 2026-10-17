/* What the two lights share: the aurora rising from the status line
 * (aurora.ts) and the curtains hanging under the top bar (curtains.ts).
 *
 * Both are one fragment shader over one oversized triangle, drawn at half
 * resolution on a transparent canvas, in colours read from the stylesheet.
 * Nothing here runs when the module is imported.
 */

/** One oversized triangle covers the canvas with no vertex buffer at all. */
export const VERT = `#version 300 es
void main() {
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

/** Noise for both shaders, pasted into each after its uniforms. */
export const NOISE = `
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
`;

// Half resolution, upscaled by CSS: the light is soft, and this is the single
// biggest saving a fragment-heavy effect has.
export const SCALE = 0.5;
const DPR_CAP = 1.5;

/** The pixels per CSS pixel a canvas is drawn at. */
export function density(): number {
  return Math.min(window.devicePixelRatio || 1, DPR_CAP) * SCALE;
}

function compile(
  ctx: WebGL2RenderingContext,
  type: number,
  source: string,
  tag: string,
): WebGLShader | null {
  const shader = ctx.createShader(type);
  if (!shader) return null;
  ctx.shaderSource(shader, source);
  ctx.compileShader(shader);
  if (!ctx.getShaderParameter(shader, ctx.COMPILE_STATUS)) {
    console.warn(`[${tag}] shader failed:`, ctx.getShaderInfoLog(shader));
    ctx.deleteShader(shader);
    return null;
  }
  return shader;
}

/** Compile and link `frag` over the shared triangle, and make it the one in
 *  use, cleared to transparent. Null, with a warning, when it will not build. */
export function buildProgram(
  ctx: WebGL2RenderingContext,
  frag: string,
  tag: string,
): WebGLProgram | null {
  const vs = compile(ctx, ctx.VERTEX_SHADER, VERT, tag);
  const fs = compile(ctx, ctx.FRAGMENT_SHADER, frag, tag);
  if (!vs || !fs) return null;
  const program = ctx.createProgram();
  ctx.attachShader(program, vs);
  ctx.attachShader(program, fs);
  ctx.linkProgram(program);
  if (!ctx.getProgramParameter(program, ctx.LINK_STATUS)) {
    console.warn(`[${tag}] link failed:`, ctx.getProgramInfoLog(program));
    return null;
  }
  // biome-ignore lint/correctness/useHookAtTopLevel: WebGL's useProgram, not a React hook
  ctx.useProgram(program);
  ctx.bindVertexArray(ctx.createVertexArray());
  ctx.clearColor(0, 0, 0, 0);
  return program;
}

/** The size a canvas was last drawn at, kept by whoever owns it. */
export interface Size {
  width: number;
  height: number;
}

/**
 * Match the canvas's buffer to its box at the drawing density, and the
 * viewport to the buffer. False while the box has no size: before layout the
 * canvas reports 0×0, and drawing then would stretch one pixel across it.
 */
export function fitCanvas(ctx: WebGL2RenderingContext, el: HTMLCanvasElement, size: Size): boolean {
  if (!el.clientWidth || !el.clientHeight) return false;
  const k = density();
  const w = Math.max(1, Math.floor(el.clientWidth * k));
  const h = Math.max(1, Math.floor(el.clientHeight * k));
  if (w !== size.width || h !== size.height) {
    size.width = el.width = w;
    size.height = el.height = h;
    ctx.viewport(0, 0, w, h);
  }
  return true;
}

export type RGB = [number, number, number];

/**
 * The colours are oklch() tokens; a shader needs numbers. Each is painted on
 * one pixel of a 2D canvas and read back, in the order given, as 0..1 sRGB.
 * Null where there is no 2D canvas to paint on.
 */
export function readColours(...css: string[]): RGB[] | null {
  const probe = document.createElement("canvas");
  probe.width = probe.height = 1;
  const ctx = probe.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  return css.map((colour) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = colour;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data as unknown as RGB;
    return [r / 255, g / 255, b / 255];
  });
}

/** Whether a sheet of this colour is the dark one. */
export function isDark([r, g, b]: RGB): boolean {
  return (r + g + b) * 255 < 384;
}

/** Whether WebGL is being drawn on the processor rather than a graphics card,
 *  by the names the software renderers go by. */
export function software(gl: WebGL2RenderingContext): boolean {
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  const name = String(gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : gl.RENDERER) ?? "");
  return /swiftshader|llvmpipe|softpipe|software|basic render/i.test(name);
}
