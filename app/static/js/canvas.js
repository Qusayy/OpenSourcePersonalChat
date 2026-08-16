/* The living canvas.
 *
 * A hand-written WebGL2 fragment shader — no library, no build step, same as
 * the rest of this project. Domain-warped fbm noise makes slow aurora ribbons
 * in the site's palette.
 *
 * The point of it: `u_energy` spikes on every token the model emits and decays
 * back down. The background visibly surges while the model is writing and
 * settles when it stops, so the atmosphere is driven by real generation rather
 * than a timer.
 *
 * Fallbacks, in order:
 *   no WebGL2            -> leave the CSS gradient layers in place, do nothing
 *   prefers-reduced-motion -> draw exactly one frame, never loop
 *   tab hidden           -> cancel the frame loop entirely
 */

const VERT = `#version 300 es
void main() {
  // One oversized triangle covers the viewport with no vertex buffer at all.
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;

uniform vec2  u_res;
uniform float u_time;
uniform float u_energy;   // 0..1, driven by the token stream
out vec4 fragColor;

const vec3 BASE   = vec3(0.024, 0.027, 0.059);
const vec3 INDIGO = vec3(0.388, 0.400, 0.945);
const vec3 VIOLET = vec3(0.659, 0.333, 0.969);
const vec3 CYAN   = vec3(0.133, 0.827, 0.933);
const vec3 ROSE   = vec3(0.957, 0.447, 0.714);

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 5; i++) {
    v += a * noise(p);
    p *= 2.02;
    a *= 0.5;
  }
  return v;
}

void main() {
  vec2 p = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;
  float t = u_time * 0.035;
  float e = u_energy;

  // Domain warping: noise sampled at coordinates that are themselves noise.
  // Two rounds is what turns round blobs into ribbons.
  vec2 q = vec2(fbm(p * 1.4 + t), fbm(p * 1.4 + vec2(5.2, 1.3) - t));
  vec2 r = vec2(
    fbm(p * 1.9 + 2.4 * q + vec2(1.7, 9.2) + t * 0.35),
    fbm(p * 1.9 + 2.4 * q + vec2(8.3, 2.8) + t * 0.28)
  );
  float f = fbm(p * 1.15 + 2.2 * r + t * 0.2);

  vec3 col = BASE;
  col = mix(col, INDIGO, clamp(f * f * 2.6, 0.0, 1.0));
  col = mix(col, VIOLET, clamp(length(q) * 0.85, 0.0, 1.0));
  col = mix(col, CYAN,   clamp(r.x * r.x * 1.5, 0.0, 1.0));
  col = mix(col, ROSE,   clamp(r.y * r.y * 0.45 * (0.4 + e), 0.0, 1.0));

  // Energy brightens and sharpens the ribbons while tokens arrive.
  col *= 0.52 + 0.85 * e;
  col += vec3(0.06, 0.05, 0.12) * e * smoothstep(0.35, 0.85, f);

  float vig = 1.0 - 0.78 * length(p * vec2(0.62, 1.0));
  col *= clamp(vig, 0.0, 1.0);

  // Ordered dither: large flat gradients band badly on 8-bit displays.
  float dither = (hash(gl_FragCoord.xy) - 0.5) / 255.0;
  fragColor = vec4(col + dither, 1.0);
}`;

function compile(gl, type, source) {
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

export function startCanvas(canvas) {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const gl = canvas.getContext("webgl2", {
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: "low-power",
    alpha: false,
  });
  if (!gl) return null; // CSS gradient layers stay visible — nothing to undo

  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return null;

  const program = gl.createProgram();
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.warn("[aurora] link failed:", gl.getProgramInfoLog(program));
    return null;
  }
  gl.useProgram(program);

  const uRes = gl.getUniformLocation(program, "u_res");
  const uTime = gl.getUniformLocation(program, "u_time");
  const uEnergy = gl.getUniformLocation(program, "u_energy");
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);

  // Half resolution, upscaled by CSS. On a fragment-heavy shader this is the
  // single biggest saving available and the softness costs nothing visually.
  const SCALE = 0.5;
  let width = 0;
  let height = 0;

  function resize() {
    // Before first layout the canvas reports 0×0. Drawing then would render a
    // single pixel that the browser upscales across the whole viewport — a
    // flat bright wash instead of an aurora. The animated path self-corrects on
    // the next frame; the reduced-motion path draws once and would keep it.
    if (!canvas.clientWidth || !canvas.clientHeight) return false;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const w = Math.max(1, Math.floor(canvas.clientWidth * dpr * SCALE));
    const h = Math.max(1, Math.floor(canvas.clientHeight * dpr * SCALE));
    if (w === width && h === height) return true;
    width = canvas.width = w;
    height = canvas.height = h;
    gl.viewport(0, 0, w, h);
    return true;
  }

  const IDLE = 0.22;
  let energy = IDLE;
  let target = IDLE;
  let raf = null;
  let start = performance.now();
  let last = 0;
  let generating = false;
  let waiting = false;
  let painted = false;
  let dead = false;

  /* The fallback must be able to come back.
   *
   * `data-canvas="on"` hides the CSS gradient layers. If the GL context is
   * then lost — software renderers, a driver reset, GPU switching, too many
   * live contexts — the page is left with a dead canvas and no background at
   * all, and since the rail, header and composer are translucent glass, the
   * whole interface washes out and text contrast collapses. So the attribute
   * goes on only after a frame has actually been painted, and comes straight
   * back off if the context dies. */
  function useShader(on) {
    if (on) document.documentElement.dataset.canvas = "on";
    else delete document.documentElement.dataset.canvas;
  }

  function draw(now) {
    raf = requestAnimationFrame(draw);
    // 30 fps while idle, 60 while the model is producing tokens.
    const interval = generating ? 0 : 33;
    if (now - last < interval) return;
    last = now;

    if (gl.isContextLost()) return fallback();
    if (!resize()) return;

    if (waiting) {
      // Prefill: the model is reading the prompt and has produced nothing yet.
      // A slow swell says "working" without pretending to be progress, and
      // leaves somewhere for the first token to land.
      const t = (now - start) / 1000;
      target = 0.34 + 0.1 * Math.sin(t * 1.7);
    } else {
      target += (IDLE - target) * 0.02; // pulses decay back toward idle
    }
    energy += (target - energy) * 0.06;

    gl.uniform2f(uRes, width, height);
    gl.uniform1f(uTime, (now - start) / 1000);
    gl.uniform1f(uEnergy, energy);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    if (!painted) {
      painted = true;
      useShader(true);
    }
  }

  function drawOnce() {
    if (gl.isContextLost()) return fallback();
    if (!resize()) {
      requestAnimationFrame(drawOnce); // wait for layout, then paint
      return;
    }
    gl.uniform2f(uRes, width, height);
    gl.uniform1f(uTime, 12.0);
    gl.uniform1f(uEnergy, IDLE);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    painted = true;
    useShader(true);
  }

  function stop() {
    if (raf !== null) cancelAnimationFrame(raf);
    raf = null;
  }

  function play() {
    if (reduced || dead || raf !== null) return;
    last = 0;
    raf = requestAnimationFrame(draw);
  }

  function fallback() {
    if (dead) return;
    dead = true;
    stop();
    useShader(false); // CSS gradient layers take over again
  }

  canvas.addEventListener("webglcontextlost", (event) => {
    // Without preventDefault the browser will not attempt a restore at all.
    event.preventDefault();
    fallback();
  });

  canvas.addEventListener("webglcontextrestored", () => {
    // The program, shaders and VAO all died with the context. Rebuilding them
    // correctly is more risk than the effect is worth mid-session, so the CSS
    // background simply keeps the page.
    console.info("[aurora] GL context restored; staying on the CSS background");
  });

  if (gl.isContextLost()) {
    // Some software rasterisers hand back a context that is already gone.
    fallback();
    return null;
  }

  if (reduced) {
    drawOnce();
  } else {
    play();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else play();
  });
  window.addEventListener("resize", () => {
    if (reduced && !dead) drawOnce();
  });

  return {
    /** Called per token: a spike that decays. */
    pulse(amount = 0.06) {
      target = Math.min(1, target + amount);
    },
    setGenerating(value) {
      generating = !!value;
      if (!value) waiting = false;
      target = value ? Math.max(target, 0.55) : IDLE;
    },
    /** Prefill has begun: hold a slow swell until the first token arrives. */
    setWaiting(value) {
      waiting = !!value;
    },
    stop,
  };
}
