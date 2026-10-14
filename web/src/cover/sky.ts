/* The sky: everything on the cover that is drawn rather than written.
 *
 * Each frame is two steps. The night and the aurora go into a buffer smaller
 * than the screen, because the light is soft and that is by far the most
 * expensive thing here. Then the screen: that buffer enlarged, the stars as
 * points at full resolution, the links drawn between them, and whatever stands
 * in front of the sky.
 *
 * How small that buffer is, and how many sheets the aurora is cut into, are
 * not fixed. The time each frame takes is watched, and the detail is stepped
 * down until the display's own rate is held, then back up if there is room.
 * Smooth comes before sharp.
 *
 * The scaffolding follows lib/aurora.ts: one oversized triangle with no vertex
 * buffer, a capped device pixel ratio, and a lost context handled rather than
 * left to break the page.
 */

import { onFrame, reduced } from "../lib/frame";
import { ANCHORS } from "./pattern";
import {
  BLIT_FRAG,
  COVER_VERT,
  GROUND_FRAG,
  LINE_FRAG,
  LINE_VERT,
  NOISE_FRAG,
  NOISE_SIZE,
  SKY_FRAG,
  STAR_FRAG,
  STAR_VERT,
} from "./shaders";
import { buildStars, STAR_STRIDE } from "./stars";

/** What stands in front of the sky. The last has nothing: the view looks up. */
export const SCENES = ["fjord", "treeline", "open"] as const;

/** The most links that can be on screen at once. */
export const MAX_SEGMENTS = 64;

/** The canvas as laid out, in CSS pixels. A unit is its shorter side. */
export interface View {
  width: number;
  height: number;
  unit: number;
}

/** What is drawn over the stars. Written by whoever handles the pointer, read here. */
export interface Overlay {
  /** x0, y0, x1, y1 and alpha for each link, in units. */
  segments: Float32Array;
  count: number;
  /** How lit each anchor is, 0 to 1 and a little over. */
  glow: Float32Array;
  /** False when there is nothing to draw, so a still sky can stay still. */
  active: boolean;
}

export interface SkyStats {
  /** Mean milliseconds a frame, over the last window. */
  frame: number;
  /** The frame time being aimed for. */
  goal: number;
  /** The aurora buffer's size as a share of the canvas. */
  scale: number;
  steps: number;
}

export interface Sky {
  readonly view: View;
  readonly overlay: Overlay;
  readonly stats: SkyStats;
  scene: number;
  /** Called at the top of every frame, before anything is drawn. */
  onStep: ((now: number, dt: number) => void) | null;
  release(): void;
}

// Detail, from most to least. Scale multiplies the starting size of the
// aurora's buffer.
const LEVELS = [
  { scale: 1, steps: 26 },
  { scale: 0.84, steps: 24 },
  { scale: 0.7, steps: 20 },
  { scale: 0.58, steps: 18 },
  { scale: 0.48, steps: 16 },
  { scale: 0.4, steps: 14 },
] as const;
const WINDOW = 45; // frames between looks at how it is going
const SLOWEST_GOAL = 16.9; // ms: a 60 Hz display, with a little slack
const FASTEST_GOAL = 6.9; // ms: 144 Hz

// The shader's clock starts here, at a moment when the arcs sit well.
const OPENING = 38;
const LINE_PAD = 16; // CSS px of quad either side of a link, for its glow

type Locations<T extends string> = Record<T, WebGLUniformLocation | null>;

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.warn("[sky] shader failed:", gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function link(gl: WebGL2RenderingContext, vert: string, frag: string): WebGLProgram | null {
  const vs = compile(gl, gl.VERTEX_SHADER, vert);
  const fs = compile(gl, gl.FRAGMENT_SHADER, frag);
  if (!vs || !fs) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.warn("[sky] link failed:", gl.getProgramInfoLog(program));
    return null;
  }
  return program;
}

function locate<T extends string>(
  gl: WebGL2RenderingContext,
  program: WebGLProgram,
  names: readonly T[],
): Locations<T> {
  const found = {} as Locations<T>;
  for (const name of names) found[name] = gl.getUniformLocation(program, `u_${name}`);
  return found;
}

const SKY_UNIFORMS = [
  "res",
  "half",
  "focal",
  "pitch",
  "yaw",
  "time",
  "steps",
  "quant",
  "grain",
  "prev",
  "blend",
  "noise",
] as const;
const BLIT_UNIFORMS = ["sky", "size", "half", "frame"] as const;
const STAR_UNIFORMS = ["scale", "dpr", "time", "horizon", "density", "maxPoint"] as const;
const LINE_UNIFORMS = ["scale", "unit"] as const;
const GROUND_UNIFORMS = ["sky", "noise", "size", "half", "horizon", "time", "scene"] as const;

interface Programs {
  sky: WebGLProgram;
  blit: WebGLProgram;
  star: WebGLProgram;
  line: WebGLProgram;
  ground: WebGLProgram;
  skyAt: Locations<(typeof SKY_UNIFORMS)[number]>;
  blitAt: Locations<(typeof BLIT_UNIFORMS)[number]>;
  starAt: Locations<(typeof STAR_UNIFORMS)[number]>;
  lineAt: Locations<(typeof LINE_UNIFORMS)[number]>;
  groundAt: Locations<(typeof GROUND_UNIFORMS)[number]>;
}

let mounted: Sky | null = null;

/**
 * Take charge of the canvas and start drawing. Returns null where there is no
 * WebGL2 to draw with, and the page falls back to what its stylesheet paints.
 */
export function mountSky(canvas: HTMLCanvasElement): Sky | null {
  mounted?.release();

  const context = canvas.getContext("webgl2", {
    alpha: false,
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: "high-performance",
  });
  if (!context || context.isContextLost()) return null;
  const gl: WebGL2RenderingContext = context;

  const view: View = { width: 0, height: 0, unit: 1 };
  const overlay: Overlay = {
    segments: new Float32Array(MAX_SEGMENTS * 5),
    count: 0,
    glow: new Float32Array(ANCHORS.length),
    active: false,
  };
  const stats: SkyStats = { frame: 0, goal: 0, scale: 0, steps: LEVELS[0].steps };

  const stars = buildStars();
  const starCount = stars.length / STAR_STRIDE;
  const halos = new Float32Array(ANCHORS.length * STAR_STRIDE);
  const lines = new Float32Array(MAX_SEGMENTS * 6 * 6);

  let programs: Programs | null = null;
  let coverVao: WebGLVertexArrayObject | null = null;
  let starVao: WebGLVertexArrayObject | null = null;
  let haloVao: WebGLVertexArrayObject | null = null;
  let haloBuffer: WebGLBuffer | null = null;
  let lineVao: WebGLVertexArrayObject | null = null;
  let lineBuffer: WebGLBuffer | null = null;
  let noise: WebGLTexture | null = null;
  // Two buffers, written in turn. Each frame of the aurora is blended into
  // the one before, so the grain a single frame carries is averaged away
  // over the next few. The light moves slowly enough not to trail.
  const skyTextures: (WebGLTexture | null)[] = [null, null];
  const skyTargets: (WebGLFramebuffer | null)[] = [null, null];
  let turn = 0; // which of the two this frame writes
  let fresh = true; // there is no earlier frame worth blending with
  let ticks = 0; // frames drawn
  let settling = 0; // frames a still sky has yet to draw before it rests
  let float = false; // whether the buffers hold half floats
  let maxPoint = 64;

  let lost = false;
  let stale = true; // the layout has changed and must be read again
  let dpr = 1;
  let base = 0.5; // the aurora buffer's size at full detail, as a share of the canvas
  let bufferW = 0;
  let bufferH = 0;
  let level = 0;
  let scene = 0;
  let shown = false;
  let wasActive = false;
  let repaint = false; // a still sky has been asked to draw once more
  const still = reduced();
  const started = performance.now();

  // The governor's books.
  const recent = new Float32Array(WINDOW);
  const failed = new Uint8Array(LEVELS.length);
  let samples = 0;
  let total = 0;
  let warming = 30; // frames not counted: the first ones compile and upload
  let calm = 0;
  let goal = 0;

  function texture(width: number, height: number, mipped: boolean): WebGLTexture | null {
    const made = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, made);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      float ? gl.RGBA16F : gl.RGBA8,
      width,
      height,
      0,
      gl.RGBA,
      float ? gl.HALF_FLOAT : gl.UNSIGNED_BYTE,
      null,
    );
    const wrap = mipped ? gl.REPEAT : gl.CLAMP_TO_EDGE;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, wrap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, wrap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(
      gl.TEXTURE_2D,
      gl.TEXTURE_MIN_FILTER,
      mipped ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR,
    );
    return made;
  }

  /** Fill the noise texture. Once, and again only if the context is rebuilt. */
  function bakeNoise(): boolean {
    const program = link(gl, COVER_VERT, NOISE_FRAG);
    if (!program) return false;
    noise = texture(NOISE_SIZE, NOISE_SIZE, true);
    const target = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, target);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, noise, 0);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.deleteFramebuffer(target);
      gl.deleteProgram(program);
      return false;
    }
    gl.viewport(0, 0, NOISE_SIZE, NOISE_SIZE);
    // biome-ignore lint/correctness/useHookAtTopLevel: WebGL's useProgram, not a React hook
    gl.useProgram(program);
    gl.uniform1f(gl.getUniformLocation(program, "u_size"), NOISE_SIZE);
    gl.bindVertexArray(coverVao);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.deleteFramebuffer(target);
    gl.deleteProgram(program);
    gl.bindTexture(gl.TEXTURE_2D, noise);
    gl.generateMipmap(gl.TEXTURE_2D);
    return true;
  }

  function points(data: Float32Array, usage: number): [WebGLVertexArrayObject, WebGLBuffer] {
    const vao = gl.createVertexArray();
    const buffer = gl.createBuffer();
    gl.bindVertexArray(vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, data, usage);
    const stride = STAR_STRIDE * 4;
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, stride, 0);
    gl.enableVertexAttribArray(1);
    gl.vertexAttribPointer(1, 4, gl.FLOAT, false, stride, 8);
    return [vao, buffer];
  }

  function build(): boolean {
    float = !!gl.getExtension("EXT_color_buffer_float");
    const range = gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE) as Float32Array | null;
    maxPoint = range ? range[1]! : 64;

    coverVao = gl.createVertexArray();
    if (!bakeNoise()) {
      // Some drivers offer float buffers and then refuse them.
      if (!float) return false;
      float = false;
      if (!bakeNoise()) return false;
    }

    const sky = link(gl, COVER_VERT, SKY_FRAG);
    const blit = link(gl, COVER_VERT, BLIT_FRAG);
    const star = link(gl, STAR_VERT, STAR_FRAG);
    const line = link(gl, LINE_VERT, LINE_FRAG);
    const ground = link(gl, COVER_VERT, GROUND_FRAG);
    if (!sky || !blit || !star || !line || !ground) return false;
    programs = {
      sky,
      blit,
      star,
      line,
      ground,
      skyAt: locate(gl, sky, SKY_UNIFORMS),
      blitAt: locate(gl, blit, BLIT_UNIFORMS),
      starAt: locate(gl, star, STAR_UNIFORMS),
      lineAt: locate(gl, line, LINE_UNIFORMS),
      groundAt: locate(gl, ground, GROUND_UNIFORMS),
    };

    [starVao] = points(stars, gl.STATIC_DRAW);
    [haloVao, haloBuffer] = points(halos, gl.DYNAMIC_DRAW);

    lineVao = gl.createVertexArray();
    lineBuffer = gl.createBuffer();
    gl.bindVertexArray(lineVao);
    gl.bindBuffer(gl.ARRAY_BUFFER, lineBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, lines, gl.DYNAMIC_DRAW);
    for (let i = 0; i < 3; i++) {
      gl.enableVertexAttribArray(i);
      gl.vertexAttribPointer(i, 2, gl.FLOAT, false, 24, i * 8);
    }
    gl.bindVertexArray(null);

    for (let i = 0; i < 2; i++) {
      skyTextures[i] = texture(2, 2, false);
      skyTargets[i] = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, skyTargets[i]!);
      gl.framebufferTexture2D(
        gl.FRAMEBUFFER,
        gl.COLOR_ATTACHMENT0,
        gl.TEXTURE_2D,
        skyTextures[i]!,
        0,
      );
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    bufferW = bufferH = 0;
    return true;
  }

  /** Size the aurora's buffer for the canvas and the current level of detail. */
  function sizeBuffer(): void {
    const share = base * LEVELS[level]!.scale;
    const w = Math.max(2, Math.round(canvas.width * share));
    const h = Math.max(2, Math.round(canvas.height * share));
    stats.scale = share;
    stats.steps = LEVELS[level]!.steps;
    if (w === bufferW && h === bufferH) return;
    bufferW = w;
    bufferH = h;
    for (const made of skyTextures) {
      gl.bindTexture(gl.TEXTURE_2D, made);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        float ? gl.RGBA16F : gl.RGBA8,
        w,
        h,
        0,
        gl.RGBA,
        float ? gl.HALF_FLOAT : gl.UNSIGNED_BYTE,
        null,
      );
    }
    // New buffers hold nothing to blend with.
    fresh = true;
  }

  function setLevel(next: number): void {
    level = next;
    sizeBuffer();
    warming = 12; // a new buffer's first frames say nothing about its steady cost
    samples = 0;
    total = 0;
  }

  /** Read the layout, if it has changed. True when there is something to draw on. */
  function layout(): boolean {
    if (!stale) return view.width > 0;
    stale = false;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) {
      view.width = 0;
      return false;
    }
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    view.width = w;
    view.height = h;
    view.unit = Math.min(w, h);
    const cw = Math.round(w * dpr);
    const ch = Math.round(h * dpr);
    if (canvas.width !== cw || canvas.height !== ch) {
      canvas.width = cw;
      canvas.height = ch;
    }
    // A dense screen hides a half-size buffer; a plain one needs more of it.
    base = dpr >= 1.5 ? 0.5 : 0.7;
    // What a level cost at the old size says little about the new one.
    failed.fill(0);
    calm = 0;
    setLevel(level);
    return true;
  }

  /** Step the detail down when frames run long, and back up when there is room. */
  function govern(dt: number): void {
    if (warming > 0) {
      warming--;
      return;
    }
    recent[samples++] = dt;
    total += dt;
    if (samples < WINDOW) return;
    const mean = total / samples;
    recent.sort();
    const quick = recent[Math.floor(WINDOW * 0.2)]!;
    samples = 0;
    total = 0;
    // The display's own interval is the quick end of what was seen, never
    // slower than 60 Hz: a page that only ever managed 30 is not on a 30 Hz
    // screen, it is struggling.
    if (!goal || quick < goal * 0.8) goal = Math.min(SLOWEST_GOAL, Math.max(FASTEST_GOAL, quick));
    stats.frame = mean;
    stats.goal = goal;

    if (mean > goal * 1.28) {
      failed[level] = 1;
      calm = 0;
      if (level < LEVELS.length - 1) setLevel(level + 1);
      else if (goal < SLOWEST_GOAL && mean < SLOWEST_GOAL * 1.28) {
        // A fast display this machine cannot feed. Sixty steady frames with
        // detail beat a ragged hundred without.
        goal = SLOWEST_GOAL;
        failed.fill(0);
      }
    } else if (mean < goal * 1.1 && level > 0 && !failed[level - 1]) {
      if (++calm >= 3) {
        calm = 0;
        setLevel(level - 1);
      }
    } else calm = 0;
  }

  function fillHalos(): number {
    let n = 0;
    for (let i = 0; i < ANCHORS.length; i++) {
      const g = overlay.glow[i]!;
      if (g < 0.004) continue;
      const a = ANCHORS[i]!;
      const o = n * STAR_STRIDE;
      halos[o] = a.x;
      halos[o + 1] = a.y;
      halos[o + 2] = 3 + 2.2 * g;
      halos[o + 3] = 0.5 * g;
      halos[o + 4] = a.tint;
      halos[o + 5] = 0;
      n++;
    }
    return n;
  }

  /** Six vertices a link: a quad wide enough for its glow, capped at both ends. */
  function fillLines(): number {
    const pad = LINE_PAD / view.unit;
    let n = 0;
    for (let i = 0; i < overlay.count; i++) {
      const s = i * 5;
      const x0 = overlay.segments[s]!;
      const y0 = overlay.segments[s + 1]!;
      const x1 = overlay.segments[s + 2]!;
      const y1 = overlay.segments[s + 3]!;
      const alpha = overlay.segments[s + 4]!;
      const length = Math.hypot(x1 - x0, y1 - y0);
      if (length < 1e-5 || alpha <= 0) continue;
      const dx = (x1 - x0) / length;
      const dy = (y1 - y0) / length;
      const nx = -dy * pad;
      const ny = dx * pad;
      const ax = x0 - dx * pad;
      const ay = y0 - dy * pad;
      const bx = x1 + dx * pad;
      const by = y1 + dy * pad;
      let o = n * 36;
      // Two triangles: start-left, start-right, end-left; end-left, start-right, end-right.
      for (let v = 0; v < 6; v++) {
        const end = v === 2 || v === 3 || v === 5;
        const side = v === 1 || v === 4 || v === 5 ? -1 : 1;
        lines[o++] = (end ? bx : ax) + nx * side;
        lines[o++] = (end ? by : ay) + ny * side;
        lines[o++] = pad * side;
        lines[o++] = end ? length + pad : -pad;
        lines[o++] = length;
        lines[o++] = alpha;
      }
      n++;
    }
    return n;
  }

  function draw(seconds: number): void {
    const p = programs;
    if (!p) return;
    const halfW = view.width / view.unit / 2;
    const halfH = view.height / view.unit / 2;
    // A tall view is given a longer lens, or its top edge would look past
    // the zenith and stretch everything there.
    const focal = Math.max(0.72, 0.92 * halfH);
    const open = scene === 2;
    const tall = view.height > view.width;
    const share = scene === 1 ? 0.085 : tall ? 0.15 : 0.18;
    const horizon = open ? -10 : halfH * (2 * share - 1);
    const pitch = open ? 1.36 : Math.atan(-horizon / focal);
    const yaw = scene === 0 ? 0.55 : scene === 1 ? -0.4 : 0.2;

    gl.disable(gl.BLEND);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, noise);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindVertexArray(coverVao);

    // The night and the aurora, into one buffer, blended with the other.
    const skyTexture = skyTextures[turn]!;
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, skyTextures[1 - turn]!);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, skyTargets[turn]!);
    gl.viewport(0, 0, bufferW, bufferH);
    gl.bindTexture(gl.TEXTURE_2D, null);
    // biome-ignore lint/correctness/useHookAtTopLevel: WebGL's useProgram, not a React hook
    gl.useProgram(p.sky);
    gl.uniform2f(p.skyAt.res, bufferW, bufferH);
    gl.uniform2f(p.skyAt.half, halfW, halfH);
    gl.uniform1f(p.skyAt.focal, focal);
    gl.uniform2f(p.skyAt.pitch, Math.cos(pitch), Math.sin(pitch));
    gl.uniform2f(p.skyAt.yaw, Math.cos(yaw), Math.sin(yaw));
    gl.uniform1f(p.skyAt.time, seconds);
    gl.uniform1i(p.skyAt.steps, LEVELS[level]!.steps);
    gl.uniform1f(p.skyAt.quant, float ? 0 : 1);
    // A different offset each frame, cycling, for the blend to average.
    const shift = 5.588238 * (ticks % 64);
    gl.uniform2f(p.skyAt.grain, shift, shift);
    gl.uniform1i(p.skyAt.noise, 1);
    gl.uniform1i(p.skyAt.prev, 2);
    // A buffer of bytes cannot hold a slow blend: small steps round away.
    gl.uniform1f(p.skyAt.blend, fresh ? 1 : float ? 0.2 : 0.4);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    fresh = false;
    turn = 1 - turn;
    ticks++;

    // The screen.
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.bindTexture(gl.TEXTURE_2D, skyTexture);
    // biome-ignore lint/correctness/useHookAtTopLevel: WebGL's useProgram, not a React hook
    gl.useProgram(p.blit);
    gl.uniform1i(p.blitAt.sky, 0);
    gl.uniform2f(p.blitAt.size, canvas.width, canvas.height);
    gl.uniform2f(p.blitAt.half, halfW, halfH);
    gl.uniform1f(p.blitAt.frame, still ? 0 : (seconds * 60) % 1024);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    // Light adds to light: stars show through the aurora, links through both.
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE);
    const scaleX = (2 * view.unit) / view.width;
    const scaleY = (2 * view.unit) / view.height;
    // biome-ignore lint/correctness/useHookAtTopLevel: WebGL's useProgram, not a React hook
    gl.useProgram(p.star);
    gl.uniform2f(p.starAt.scale, scaleX, scaleY);
    gl.uniform1f(p.starAt.dpr, dpr);
    gl.uniform1f(p.starAt.time, seconds);
    gl.uniform1f(p.starAt.horizon, horizon);
    // The same number of stars to a screenful of pixels, whatever its size.
    gl.uniform1f(p.starAt.density, Math.min(1, Math.max(0.1, (view.unit / 1080) ** 2)));
    gl.uniform1f(p.starAt.maxPoint, maxPoint);
    gl.bindVertexArray(starVao);
    gl.drawArrays(gl.POINTS, 0, starCount);

    if (overlay.active) {
      const lit = fillHalos();
      if (lit) {
        gl.bindVertexArray(haloVao);
        gl.bindBuffer(gl.ARRAY_BUFFER, haloBuffer);
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, halos, 0, lit * STAR_STRIDE);
        gl.drawArrays(gl.POINTS, 0, lit);
      }
      const drawn = fillLines();
      if (drawn) {
        // biome-ignore lint/correctness/useHookAtTopLevel: WebGL's useProgram, not a React hook
        gl.useProgram(p.line);
        gl.uniform2f(p.lineAt.scale, scaleX, scaleY);
        gl.uniform1f(p.lineAt.unit, view.unit);
        gl.bindVertexArray(lineVao);
        gl.bindBuffer(gl.ARRAY_BUFFER, lineBuffer);
        gl.bufferSubData(gl.ARRAY_BUFFER, 0, lines, 0, drawn * 36);
        gl.drawArrays(gl.TRIANGLES, 0, drawn * 6);
      }
    }

    if (!open) {
      gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      // biome-ignore lint/correctness/useHookAtTopLevel: WebGL's useProgram, not a React hook
      gl.useProgram(p.ground);
      gl.uniform1i(p.groundAt.sky, 0);
      gl.uniform1i(p.groundAt.noise, 1);
      gl.uniform2f(p.groundAt.size, canvas.width, canvas.height);
      gl.uniform2f(p.groundAt.half, halfW, halfH);
      gl.uniform1f(p.groundAt.horizon, horizon);
      gl.uniform1f(p.groundAt.time, seconds);
      gl.uniform1i(p.groundAt.scene, scene);
      gl.bindVertexArray(coverVao);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    }

    if (!shown) {
      // The stylesheet fades the canvas in from here: no frame is seen before
      // the first whole one.
      shown = true;
      canvas.dataset.ready = "";
    }
  }

  function step(now: number, dt: number): void {
    sky.onStep?.(now, dt);
    if (lost || !programs) return;
    const changed = stale;
    if (!layout()) return;
    if (still) {
      // Reduced motion: the sky holds one moment. It is drawn for as many
      // frames as the blend takes to settle, and again only when something
      // has changed.
      const busy = overlay.active || wasActive;
      wasActive = overlay.active;
      if (!shown || changed || repaint) settling = 24;
      repaint = false;
      if (settling <= 0 && !busy) return;
      settling--;
      draw(OPENING);
      return;
    }
    govern(dt);
    draw(OPENING + (now - started) / 1000);
  }

  const onLost = (event: Event): void => {
    event.preventDefault();
    lost = true;
  };
  const onRestored = (): void => {
    lost = !build();
    stale = true;
  };
  const onResize = (): void => {
    stale = true;
  };
  canvas.addEventListener("webglcontextlost", onLost);
  canvas.addEventListener("webglcontextrestored", onRestored);
  window.addEventListener("resize", onResize);
  const observer = typeof ResizeObserver === "function" ? new ResizeObserver(onResize) : null;
  observer?.observe(canvas);

  if (!build()) {
    canvas.removeEventListener("webglcontextlost", onLost);
    canvas.removeEventListener("webglcontextrestored", onRestored);
    window.removeEventListener("resize", onResize);
    observer?.disconnect();
    return null;
  }

  const off = onFrame(step);

  const sky: Sky = {
    view,
    overlay,
    stats,
    get scene() {
      return scene;
    },
    set scene(next: number) {
      scene = Math.max(0, Math.min(SCENES.length - 1, Math.trunc(next)));
      repaint = true;
      fresh = true;
    },
    onStep: null,
    release() {
      if (mounted !== sky) return;
      mounted = null;
      off();
      observer?.disconnect();
      window.removeEventListener("resize", onResize);
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
    },
  };
  mounted = sky;
  return sky;
}

if (import.meta.hot) import.meta.hot.dispose(() => mounted?.release());
