/* The cover's shaders, GLSL ES 3.00.
 *
 * Six small programs:
 *
 *   noise    run once, into a tiling texture every other program reads. A
 *            noise lookup is then one filtered fetch with mipmaps behind it,
 *            which is what makes the aurora affordable on a phone and keeps
 *            its far end from shimmering.
 *   sky      the night sky and the aurora, into a buffer smaller than the
 *            screen: the light is soft and survives being enlarged.
 *   blit     that buffer to the screen, with a lens's vignette and the grain
 *            that keeps a dark gradient from banding.
 *   star     the star field, as points, at the screen's own resolution.
 *   line     the links drawn between stars.
 *   ground   whatever stands in front of the sky.
 *
 * The aurora is drawn the way it exists: thin curtains standing on a wavy
 * line along the ground, lit by altitude. A view ray is cut by a stack of
 * level sheets between the curtain's hem and its top. Between one sheet and
 * the next the ray travels some way across the ground plan, and how much
 * curtain it passed through on the way is worked out exactly rather than
 * sampled, so a curtain far thinner than the gap between sheets is still
 * drawn whole: one clean lower edge, not a stack of ledges. Rays, folds, and
 * a curtain seen edge-on being denser than one seen face-on all fall out of
 * that.
 */

/** One oversized triangle that covers the target, with no vertex buffer. */
export const COVER_VERT = `#version 300 es
void main() {
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

export const NOISE_SIZE = 512;

export const NOISE_FRAG = `#version 300 es
precision highp float;
uniform float u_size;
out vec4 o;

// A value for each lattice point from integer arithmetic, which is exact, and
// wrapped at the period so the texture tiles without a seam.
float hash(ivec2 p, int period, uint seed) {
  ivec2 w = p - period * ivec2(floor(vec2(p) / float(period)));
  uvec2 q = uvec2(w) * uvec2(1597334673u, 3812015801u);
  uint n = (q.x ^ q.y ^ (seed * 2654435761u)) * 1597334673u;
  n ^= n >> 15;
  n *= 2246822519u;
  n ^= n >> 13;
  return float(n >> 8) * (1.0 / 16777216.0);
}

float value(vec2 p, int period, uint seed) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
  ivec2 c = ivec2(i);
  return mix(
    mix(hash(c, period, seed), hash(c + ivec2(1, 0), period, seed), u.x),
    mix(hash(c + ivec2(0, 1), period, seed), hash(c + ivec2(1, 1), period, seed), u.x),
    u.y);
}

float fbm(vec2 uv, int period, int octaves, uint seed) {
  float amp = 0.5;
  float total = 0.0;
  float sum = 0.0;
  for (int k = 0; k < octaves; k++) {
    sum += amp * value(uv * float(period), period, seed + uint(k) * 101u);
    total += amp;
    amp *= 0.5;
    period *= 2;
  }
  return sum / total;
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_size;
  // Four unrelated fields. The first two are what curtains are bent with, and
  // are kept smooth, two octaves and no more: a curtain's place is found
  // between sheets that are some way apart, and a bend with fine wrinkles in
  // it cannot be followed across that gap. The third is fine, for rays; the
  // fourth is rough, for everything else.
  o = vec4(fbm(uv, 4, 2, 11u), fbm(uv, 4, 2, 57u), fbm(uv, 16, 4, 131u), fbm(uv, 8, 5, 211u));
}`;

export const SKY_FRAG = `#version 300 es
precision highp float;
precision highp sampler2D;

uniform vec2 u_res;        // the buffer, in pixels
uniform vec2 u_half;       // half the view, in units of its shorter side
uniform float u_focal;
uniform vec2 u_pitch;      // cos, sin: how far up the view is tipped
uniform vec2 u_yaw;        // cos, sin: how the arcs lie across the view
uniform float u_time;
uniform int u_steps;
uniform float u_quant;     // 1 where the buffer holds 8 bits a channel
uniform vec2 u_grain;      // moves each frame, so the grain does not sit still
uniform sampler2D u_noise;
uniform sampler2D u_prev;  // the frame before this one
uniform float u_blend;     // how much of this frame goes into the result
out vec4 o;

// Altitudes in hundreds of kilometres: the hem of a curtain is near 100 km.
const float HEM = 1.0;
const float REACH = 2.8;
const float SPACING = 4.2;
const float NEAREST = 1.9;     // how far north the arc nearest the viewer stands
const float TEXELS = ${NOISE_SIZE}.0;
const float ROOT_PI = 1.7724539;

// Linear light. Oxygen's green line dominates; its red line shows higher up,
// where the air is thin enough to let it; nitrogen edges the very bottom.
const vec3 GREEN = vec3(0.050, 1.000, 0.190);
const vec3 RED = vec3(1.000, 0.050, 0.340);
const vec3 HEMLINE = vec3(1.000, 0.240, 0.620);

// Interleaved gradient noise: a different offset for each pixel, so the
// sheets of one ray do not line up with its neighbour's into visible bands.
float ign(vec2 p) {
  return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715))));
}

float arcSeed(float id) {
  return fract(sin(id * 127.1 + 3.7) * 43758.5453);
}

// Within a few thousandths of the error function, which is nearer than needed.
float erf(float x) {
  float x2 = x * x;
  return sign(x) * sqrt(1.0 - exp(-x2 * (1.2732395 + 0.147 * x2) / (1.0 + 0.147 * x2)));
}

// The ground plan at a point: how far across the arcs it lies, and how far
// along them. Flat ground would give back the point itself; this bends it
// with a few slow waves of unrelated lengths, the longest a meander thirty
// units long and the shortest a fold of under three. Waves and not noise from
// the texture, because a curtain's place has to be found exactly, and a
// formula can be asked for its value anywhere.
vec2 plan(vec2 Q, float t) {
  return vec2(
    Q.y + 1.90 * sin(Q.x * 0.21 + 0.6 * sin(Q.y * 0.13 + t * 0.011) + t * 0.013)
        + 1.10 * sin(Q.x * 0.47 - Q.y * 0.19 + 1.7 - t * 0.021)
        + 0.55 * sin(Q.x * 1.05 + Q.y * 0.42 + 4.1 + t * 0.034)
        + 0.26 * sin(Q.x * 2.30 - Q.y * 0.90 + 2.2 - t * 0.050),
    Q.x + 0.90 * sin(Q.y * 0.31 + Q.x * 0.11 + 0.8 + t * 0.009)
        + 0.40 * sin(Q.y * 0.83 - Q.x * 0.37 + 3.3));
}

// A smooth curve from p0 to p1 that leaves heading m0 and arrives heading m1:
// its value at u, and how fast it is changing there.
vec2 curve(float p0, float m0, float p1, float m1, float u) {
  float u2 = u * u;
  float u3 = u2 * u;
  return vec2(
    (2.0 * u3 - 3.0 * u2 + 1.0) * p0 + (u3 - 2.0 * u2 + u) * m0 + (3.0 * u2 - 2.0 * u3) * p1 + (u3 - u2) * m1,
    6.0 * (u2 - u) * (p0 - p1) + (3.0 * u2 - 4.0 * u + 1.0) * m0 + (3.0 * u2 - 2.0 * u) * m1);
}

// Never zero, and never the other sign.
float nonzero(float x) {
  return abs(x) > 1e-5 ? x : (x < 0.0 ? -1e-5 : 1e-5);
}

/// How thin an arc is at a place along its length: from about one kilometre
// to five.
float arcWidth(float seed, float run) {
  float w = 0.5 + 0.5 * sin(run * 0.57 + seed * 31.0);
  return 0.010 + 0.045 * w * w;
}

vec3 aurora(vec3 rd, float pixel, float jitter) {
  vec3 sum = vec3(0.0);
  float inv = 1.0 / max(rd.y, 0.04);
  float n = float(u_steps);
  float t = u_time;
  // The ray's track over the ground, turned to lie as the arcs do: a point
  // at altitude a is this times (HEM + a).
  vec2 track = vec2(rd.x * u_yaw.x - rd.z * u_yaw.y, rd.x * u_yaw.y + rd.z * u_yaw.x) * inv;
  // Three sheets are in hand at a time: a, b, and the newest, c. The stretch
  // of the ray from a to b is lit once c is known, because what the ground
  // plan does between two sheets is first read off a smooth curve through
  // them, and a curve has to know which way it is heading at both ends.
  vec4 ga = vec4(0.0);     // across, run, count, altitude
  vec4 gb = vec4(0.0);
  vec2 ea = vec2(0.0);     // haze, far
  vec2 eb = vec2(0.0);
  vec2 da = vec2(0.0);     // how fast across and run change with altitude, at a
  // One more sheet than there are gaps (the first lies on the hem itself),
  // and one turn more to close the last stretch.
  for (int i = 0; i <= u_steps + 1; i++) {
    // Sheets crowd toward the hem, where the light is. All but the first are
    // set a little differently for each pixel. A curtain met face-on is
    // drawn the same wherever the sheets fall; one seen edge-on is summed
    // over them, and with every pixel's sheets in the same places the sum
    // shows as ripples. Offset, what is left is grain.
    float f = i == 0 ? 0.0 : (float(i - 1) + jitter) / n;
    float alt = REACH * f * f;
    vec4 gc;
    vec2 ec = eb;
    if (i <= u_steps) {
      vec2 Q = track * (HEM + alt);
      vec2 here = plan(Q, t);
      // An arc every SPACING. The third number kept is how many arcs lie to
      // one side of this point, counting part of the one it is inside: the
      // difference between two points is how much curtain lies between them.
      float cell = (here.x - NEAREST) / SPACING + 0.5;
      float id = floor(cell);
      float d = (fract(cell) - 0.5) * SPACING;
      gc = vec4(here, id + 0.5 + 0.5 * erf(d / arcWidth(arcSeed(id), here.y)), alt);
      // Far arcs sink under the curve of the earth; none stand behind the viewer.
      float ahead = smoothstep(-3.5, -0.5, rd.z * (HEM + alt) * inv);
      ec = vec2(exp(-d * d / 0.35), exp(-dot(Q, Q) * 0.0200) * ahead);
    } else {
      // Past the top sheet the plan is taken to carry straight on.
      gc = vec4(gb.xy + (gb.xy - ga.xy) / (gb.w - ga.w) * (alt - gb.w), gb.z, alt);
    }

    if (i >= 2) {
      float h = gb.w - ga.w;
      float next = gc.w - gb.w;
      vec2 chord = (gb.xy - ga.xy) / h;
      vec2 db = (chord * next + (gc.xy - gb.xy) / next * h) / (h + next);
      if (i == 2) da = 2.0 * chord - db;
      vec2 m0 = da * h;
      vec2 m1 = db * h;

      // The curtains' own light scattered about them: a wide, dim haze.
      vec2 mid = 0.5 * (ea + eb);
      sum += GREEN * (mid.x * mid.y * exp(-(ga.w + gb.w)) * 0.0045 * h * inv);

      // The arc this stretch is nearest, and where along the stretch the ray
      // comes closest to its centre line: on the curve through the sheets,
      // which costs nothing.
      float arc = floor((0.5 * (ga.x + gb.x) - NEAREST) / SPACING + 0.5);
      float goal = arc * SPACING + NEAREST;
      float u = clamp((goal - ga.x) / nonzero(gb.x - ga.x), 0.0, 1.0);
      vec2 guess = vec2(0.0);
      for (int k = 0; k < 2; k++) {
        guess = curve(ga.x, m0.x, gb.x, m1.x, u);
        u = clamp(u - (guess.x - goal) / nonzero(guess.y), 0.0, 1.0);
      }
      guess = curve(ga.x, m0.x, gb.x, m1.x, u);

      // Most stretches of most rays have no curtain anywhere near them, and
      // are done.
      if (abs(guess.x - goal) < 0.2 || abs(gb.z - ga.z) > 1e-4) {
        float seed = arcSeed(arc);
        // Then against the plan itself, which lands on the line where the
        // curve only lands close. Everything a curtain shows is looked up
        // by that place along it and that height up it, and by nothing to
        // do with where the sheets happen to fall: a ray of light drawn
        // from anything less exact comes out in rungs, one to a stretch.
        vec2 exact = plan(track * (HEM + mix(ga.w, gb.w, u)), t);
        float moved = clamp(u - (exact.x - goal) / nonzero(guess.y), 0.0, 1.0);
        float place = exact.y + curve(ga.y, m0.y, gb.y, m1.y, u).y * (moved - u);
        u = moved;
        float width = arcWidth(seed, place);
        float rate = curve(ga.x, m0.x, gb.x, m1.x, u).y;

        // Curtain passed through on this stretch, as a depth of it, two
        // ways. A ray that cuts through an arc passes all of the arc that
        // lies between the two sheets, spread by how steeply it cuts. A ray
        // that only grazes one, or runs along inside it, is measured by
        // looking at the curtain's density at several points along the
        // stretch. The first is exact for a thin arc and blind to a graze;
        // the second sees a graze and steps over a thin arc; each is used
        // where it is right. Both are held to a ceiling: looking straight up
        // a curtain's own height is the brightest view there is, and a
        // sensor has long since clipped by then.
        float depth = abs(gb.z - ga.z) * ROOT_PI / max(abs(rate) / h, 0.09);
        float graze = smoothstep(0.08, 0.22, width / max(abs(rate), 1e-4));
        // How far along the curtain the ray travels while it is inside it.
        // A crossing meets it at a point; a graze runs along it, and sees
        // the average of all the rays it passes rather than any one.
        float smear = 0.0;
        if (graze > 0.0) {
          float inside = 0.0;
          float centre = 0.0;
          for (int k = 0; k < 6; k++) {
            float v = (float(k) + 0.5) / 6.0;
            float off = (curve(ga.x, m0.x, gb.x, m1.x, v).x - goal) / width;
            float dense = exp(-off * off);
            inside += dense;
            centre += dense * v;
          }
          // A grazing ray is lit for the middle of the length it spends in
          // the curtain. The nearest point to the centre line would do for
          // a crossing, but along a graze it jumps from one end of a stretch
          // to the other, and the edge of a fold comes out toothed.
          if (inside > 1e-4) {
            float middle = centre / inside;
            place = mix(place, curve(ga.y, m0.y, gb.y, m1.y, middle).x, graze);
            u = mix(u, middle, graze);
          }
          depth = mix(depth, min(inside / (6.0 * width), 9.0) * h, graze);
          smear = graze * abs(gb.y - ga.y) * inside / 6.0;
        }
        float height = mix(ga.w, gb.w, u);
        float far = mix(ea.y, eb.y, u);

        // An arc is not lit evenly along its length, and no two alike.
        float lit = smoothstep(0.24, 0.62, 0.5
          + 0.27 * sin(place * 0.33 + seed * 40.0 + t * 0.017)
          + 0.21 * sin(place * 0.71 - seed * 23.0 - t * 0.011));
        // Fine structure runs along the curtain and stands up as rays, in
        // clusters, some reaching higher than others. How much of the
        // curtain one pixel takes in picks the mip level: the ground it
        // covers out there, or the length of a graze if that is more.
        float foot = max((HEM + height) * inv * pixel, smear) * TEXELS;
        vec4 fine = textureLod(u_noise, vec2(place * 0.9 + t * 0.011, seed * 7.0), log2(foot * 0.9));
        float clumps = textureLod(u_noise, vec2(place * 0.12 - t * 0.003, seed * 7.0 + 0.37), log2(foot * 0.12)).a;
        float cluster = smoothstep(0.34, 0.64, clumps);
        float rays = (0.34 + 0.66 * smoothstep(0.30, 0.72, fine.b)) * (0.45 + 0.75 * cluster);
        float tall = mix(0.55, 1.45, smoothstep(0.30, 0.70, fine.r)) * (0.8 + 0.5 * cluster);

        // A curtain's lower edge is sharp close to, and softened by all the
        // air in the way when it stands far off: far is 1 near and falls
        // toward 0.
        float soft = 0.02 + 0.10 * (1.0 - far);
        float green = smoothstep(0.0, soft, height) * exp(-height / (0.30 * tall));
        float red = smoothstep(0.25, 1.1, height) * exp(-height / (0.95 * tall)) * 0.16;
        float hem = exp(-height * height / 0.0012) * 0.28 * far;
        sum += (GREEN * green + RED * red + HEMLINE * hem) * (depth * 0.105 * lit * rays * far * inv);
      }
      da = db;
    }
    ga = gb;
    ea = eb;
    gb = gc;
    eb = ec;
  }
  // The thick air near the horizon takes most of it.
  return sum * smoothstep(0.0, 0.11, rd.y);
}

vec3 night(vec3 rd) {
  float e = clamp(rd.y, 0.0, 1.0);
  vec3 c = mix(vec3(0.0030, 0.0052, 0.0082), vec3(0.00055, 0.00105, 0.0030), pow(e, 0.45));
  // Airglow: the upper air's own faint green, in a band low in the sky.
  c += vec3(0.0010, 0.0026, 0.0014) * exp(-pow((e - 0.13) / 0.11, 2.0));
  // The Milky Way: a narrow lane, clumped, crossing the sky at a slant.
  vec3 axis = normalize(vec3(0.55, 0.30, -0.78));
  float lane = exp(-pow(dot(rd, axis) / 0.13, 2.0));
  vec2 muv = vec2(atan(rd.x, rd.z), rd.y) * 0.37;
  float clumps = texture(u_noise, muv).a * 0.6 + texture(u_noise, muv * 4.3 + 0.4).b * 0.6;
  c += vec3(0.0013, 0.0014, 0.0020) * lane * smoothstep(0.40, 0.90, clumps) * smoothstep(0.02, 0.30, e);
  return c;
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_res;
  vec2 p = (uv * 2.0 - 1.0) * u_half;
  vec3 rc = normalize(vec3(p, u_focal));
  vec3 rd = vec3(rc.x, rc.y * u_pitch.x + rc.z * u_pitch.y, rc.z * u_pitch.x - rc.y * u_pitch.y);
  float pixel = 2.0 * u_half.y / (u_res.y * u_focal);

  vec3 col = night(rd);
  if (rd.y > 0.0) col += aurora(rd, pixel, 0.25 + 0.75 * ign(gl_FragCoord.xy + u_grain));

  // A sensor's response. Scaling by the strongest channel keeps the colour as
  // it brightens; only the very brightest parts wash toward a pale green, the
  // way an overexposed curtain does.
  col *= 9.0;
  float peak = max(col.r, max(col.g, col.b));
  float mapped = 1.0 - exp(-peak);
  col *= mapped / max(peak, 1e-5);
  col = mix(col, vec3(mapped) * vec3(0.84, 1.0, 0.78), smoothstep(0.72, 1.0, mapped) * 0.38);
  col = pow(col, vec3(1.0 / 2.2));
  col = mix(texture(u_prev, uv).rgb, col, u_blend);
  col += (ign(gl_FragCoord.xy + 17.0 + u_grain) - 0.5) * u_quant / 255.0;
  o = vec4(col, 1.0);
}`;

export const BLIT_FRAG = `#version 300 es
precision highp float;
uniform sampler2D u_sky;
uniform vec2 u_size;       // the canvas, in pixels
uniform vec2 u_half;
uniform float u_frame;     // changes every frame, so the grain does
out vec4 o;

float grain(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_size;
  vec3 c = texture(u_sky, uv).rgb;
  vec2 p = (uv * 2.0 - 1.0) * u_half;
  c *= 1.0 - 0.20 * smoothstep(0.35, 1.15, length(p));
  c += (grain(gl_FragCoord.xy + u_frame) - 0.5) * (1.8 / 255.0);
  o = vec4(c, 1.0);
}`;

export const STAR_VERT = `#version 300 es
layout(location = 0) in vec2 a_pos;    // units of the view's shorter side
layout(location = 1) in vec4 a_data;   // radius in CSS px, brightness, tint, share
uniform vec2 u_scale;      // units to clip space
uniform float u_dpr;
uniform float u_time;
uniform float u_horizon;   // where the horizon is, in the same units
uniform float u_density;   // the share of the field this view draws
uniform float u_maxPoint;
out vec3 v_colour;
out vec2 v_shape;          // the star's radius and its sprite's width, in pixels

void main() {
  float share = a_data.w;
  if (share > u_density) {
    gl_Position = vec4(2.0, 2.0, 0.0, 1.0);
    gl_PointSize = 1.0;
    return;
  }
  gl_Position = vec4(a_pos * u_scale, 0.0, 1.0);
  float up = a_pos.y - u_horizon;

  // Scintillation: two beats that never line up, deeper where the light has
  // more air to cross.
  float low = 1.0 - smoothstep(0.0, 0.55, up);
  float beat = sin(u_time * (2.1 + 3.7 * fract(share * 7.13)) + share * 6.283)
             * sin(u_time * (1.3 + 2.9 * fract(share * 3.71)) + share * 17.0);
  float bright = a_data.y * (1.0 + beat * (0.08 + 0.34 * low));
  bright *= mix(0.2, 1.0, smoothstep(-0.02, 0.24, up));

  // A star narrower than a pixel is drawn a pixel wide and dimmer, or it
  // would flicker as it fell between pixels.
  float r = a_data.x * u_dpr;
  if (r < 0.75) {
    bright *= (r * r) / 0.5625;
    r = 0.75;
  }
  float size = min(u_maxPoint, max(3.0, r * 9.0));
  gl_PointSize = size;
  v_shape = vec2(r, size);

  float tint = a_data.z;
  vec3 cool = vec3(0.68, 0.80, 1.00);
  vec3 warm = vec3(1.00, 0.82, 0.62);
  vec3 hue = tint < 0.5 ? mix(cool, vec3(1.0), tint * 2.0) : mix(vec3(1.0), warm, tint * 2.0 - 1.0);
  v_colour = hue * bright;
}`;

export const STAR_FRAG = `#version 300 es
precision highp float;
in vec3 v_colour;
in vec2 v_shape;
out vec4 o;

void main() {
  float r = v_shape.x;
  float reach = length(gl_PointCoord - 0.5);
  float d = reach * v_shape.y;
  // The profile a lens and the air give a point of light: a tight core on
  // wide, faint wings. A bright star's wings are its glow; a faint star's
  // are below anything a screen can show.
  float m = 1.0 + d * d / (r * r);
  float a = 1.0 / (m * m * sqrt(m));
  a *= 1.0 - smoothstep(0.40, 0.5, reach);
  o = vec4(v_colour * a, 1.0);
}`;

export const LINE_VERT = `#version 300 es
layout(location = 0) in vec2 a_pos;    // units
layout(location = 1) in vec2 a_local;  // across, along: units from the segment's start
layout(location = 2) in vec2 a_seg;    // length, alpha
uniform vec2 u_scale;
out vec2 v_local;
out vec2 v_seg;

void main() {
  gl_Position = vec4(a_pos * u_scale, 0.0, 1.0);
  v_local = a_local;
  v_seg = a_seg;
}`;

export const LINE_FRAG = `#version 300 es
precision highp float;
in vec2 v_local;
in vec2 v_seg;
uniform float u_unit;      // CSS pixels to a unit
out vec4 o;

void main() {
  // Distance to the segment, as a capsule, in CSS pixels.
  float along = clamp(v_local.y, 0.0, v_seg.x);
  float d = length(vec2(v_local.x, v_local.y - along)) * u_unit;
  float core = exp(-d * d / 0.8);
  float glow = 0.16 * exp(-d / 4.0);
  o = vec4(vec3(0.72, 0.89, 1.00) * (core + glow) * v_seg.y, 1.0);
}`;

export const GROUND_FRAG = `#version 300 es
precision highp float;
precision highp sampler2D;

uniform sampler2D u_sky;
uniform sampler2D u_noise;
uniform vec2 u_size;       // the canvas, in pixels
uniform vec2 u_half;
uniform float u_horizon;   // units
uniform float u_time;
uniform int u_scene;       // 0 fjord, 1 treeline
out vec4 o;               // premultiplied

float ridged(float n) {
  return 1.0 - abs(n * 2.0 - 1.0);
}

float seedOf(float n) {
  return fract(sin(n * 91.7 + 1.3) * 43758.5453);
}

// A skyline, 0 to 1: broad massifs sharpened into peaks, with crags along
// the edge. The row picks which mountains.
float skyline(float x, float row) {
  float broad = texture(u_noise, vec2(x * 0.16, row)).a;
  float peaks = ridged(texture(u_noise, vec2(x * 0.47 + 0.3, row + 0.21)).a);
  float crags = texture(u_noise, vec2(x * 1.9, row + 0.47)).b - 0.5;
  float h = pow(clamp(broad * 1.7 - 0.38, 0.0, 1.0), 1.3) * (0.45 + 0.55 * peaks);
  return clamp(h + crags * 0.10, 0.0, 1.0);
}

vec3 skyAt(vec2 p) {
  return texture(u_sky, clamp(p / u_half * 0.5 + 0.5, 0.0, 1.0)).rgb;
}

float backTop(float x) {
  return u_horizon + 0.010 + 0.075 * skyline(x, 0.13);
}

float frontTop(float x) {
  float side = smoothstep(0.25, 0.95, abs(x) / u_half.x);
  return u_horizon - 0.004 + (0.03 + 0.15 * skyline(x * 1.3 + 5.0, 0.57)) * side * side;
}

vec4 fjord(vec2 p, float aa) {
  vec3 backCol = vec3(0.036, 0.058, 0.070);
  vec3 frontCol = vec3(0.012, 0.019, 0.022);

  if (p.y >= u_horizon) {
    float back = backTop(p.x);
    float front = frontTop(p.x);
    float mBack = smoothstep(back + aa, back - aa, p.y);
    float mFront = smoothstep(front + aa, front - aa, p.y);
    // Snow on the far slopes takes a little of the light above it.
    vec3 lift = skyAt(vec2(p.x, u_horizon + 0.20));
    float snow = texture(u_noise, vec2(p.x * 1.7, (p.y - u_horizon) * 11.0)).a;
    float height = clamp((p.y - u_horizon) / 0.085, 0.0, 1.0);
    vec3 c = backCol + lift * (0.05 + 0.16 * smoothstep(0.50, 0.72, snow) * height);
    c = mix(c, frontCol + lift * 0.015, mFront);
    float a = max(mBack, mFront);
    return vec4(c * a, a);
  }

  // Water. Depth is how far below the horizon the pixel is; the surface it
  // shows is that much nearer the viewer.
  float depth = u_horizon - p.y;
  float away = 1.0 / (depth + 0.03);
  vec2 wuv = vec2(p.x * away * 0.030, away * 0.070 - u_time * 0.005);
  vec2 rip = texture(u_noise, wuv).ba - 0.5;
  float streaks = texture(u_noise, vec2(p.x * 0.05 + 0.3, away * 0.021 + u_time * 0.002)).a;
  float sway = rip.x * 0.022 * (0.2 + depth * 2.4);

  // The reflection, drawn out downward the way moving water draws out a light.
  vec3 refl = vec3(0.0);
  float land = 0.0;
  for (int k = 0; k < 5; k++) {
    float s = float(k) / 4.0;
    vec2 q = vec2(p.x + sway * (0.5 + s), u_horizon + depth * (0.90 + 0.40 * s) + rip.y * 0.012 + s * 0.02);
    refl += skyAt(q);
    float top = max(backTop(q.x), frontTop(q.x));
    land += smoothstep(top + 0.01, top - 0.01, q.y);
  }
  refl *= 0.2;
  land *= 0.2;
  // Water seen at a glancing angle is a mirror; looked down into, it is dark.
  float mirror = mix(0.80, 0.34, smoothstep(0.0, 0.5, depth));
  vec3 c = mix(refl * vec3(0.82, 0.94, 1.0), frontCol * 1.2, land) * mirror;
  // Bands of ruffled and smooth water.
  c *= 0.78 + 0.44 * smoothstep(0.35, 0.65, streaks);
  c += vec3(0.003, 0.006, 0.009);
  // A dark line where the land meets it.
  c *= 0.5 + 0.5 * smoothstep(0.0, 0.010, depth);
  return vec4(c, 1.0);
}

// One row of spruce. Each tree has its own height and place; some places are
// empty. A tree is tiers of boughs that droop and end raggedly, narrowing to
// a thin top.
float spruce(vec2 p, float base, float size, float seed, float aa) {
  float id = floor(p.x / size);
  float mask = 0.0;
  for (int k = -1; k <= 1; k++) {
    float j = id + float(k);
    float r1 = seedOf(j + seed);
    float r2 = seedOf(j * 1.7 + seed);
    float r3 = seedOf(j * 2.3 + seed);
    if (r3 < 0.16) continue;
    float tall = size * (1.7 + 4.1 * r1 * r1);
    float x0 = (j + 0.5 + (r2 - 0.5) * 0.9) * size;
    float y = (p.y - base) / tall;
    if (y > 1.0) continue;
    float tier = fract(y * (6.0 + 7.0 * r3) + r2);
    // Not the same on both sides: no tree is its own mirror image.
    float ragged = texture(u_noise, vec2(step(x0, p.x) * 0.37 + r1, y * 0.9 + r2)).b;
    float taper = pow(1.0 - clamp(y, 0.0, 1.0), 1.2);
    float halfWidth = taper * (0.26 + 0.36 * (1.0 - tier)) * (0.55 + 0.95 * ragged) * (0.7 + 0.5 * r2) * size;
    halfWidth += 0.02 * size * step(y, 0.97);
    mask = max(mask, smoothstep(halfWidth + aa, halfWidth - aa, abs(p.x - x0)));
  }
  return mask;
}

vec4 treeline(vec2 p, float aa) {
  // The ground rolls a little, and the rows of trees stand on it.
  float roll = (texture(u_noise, vec2(p.x * 0.06, 0.83)).a - 0.5) * 0.030;
  float ground = u_horizon + roll;
  float hills = ground + 0.018 + 0.040 * skyline(p.x * 0.7 + 3.0, 0.33);
  float mHills = smoothstep(hills + aa, hills - aa, p.y);
  float far = spruce(p, ground + 0.012, 0.0085, 4.0, aa);
  float mid = spruce(p, ground + 0.002, 0.016, 11.0, aa);
  float near = spruce(p, ground - 0.034, 0.033, 19.0, aa);
  float snow = smoothstep(ground + 0.006 + aa, ground + 0.006 - aa, p.y);

  vec3 lift = skyAt(vec2(p.x, u_horizon + 0.25));
  vec3 hillCol = vec3(0.038, 0.060, 0.072) + lift * 0.04;
  vec3 farCol = vec3(0.026, 0.042, 0.050);
  vec3 midCol = vec3(0.014, 0.023, 0.027);
  vec3 nearCol = vec3(0.005, 0.008, 0.010);
  // Snow takes its colour from the sky over it, and darkens toward the viewer.
  float drift = texture(u_noise, vec2(p.x * 0.8, p.y * 3.1)).a;
  vec3 snowCol = (vec3(0.040, 0.054, 0.066) + lift * 0.20) * (0.72 + 0.5 * drift)
               * mix(1.0, 0.40, smoothstep(0.0, 0.14, ground - p.y));

  vec3 c = hillCol;
  float a = mHills;
  c = mix(c, farCol, far);
  a = max(a, far);
  c = mix(c, snowCol, snow);
  a = max(a, snow);
  c = mix(c, midCol, mid);
  a = max(a, mid);
  c = mix(c, nearCol, near);
  a = max(a, near);
  return vec4(c * a, a);
}

void main() {
  vec2 uv = gl_FragCoord.xy / u_size;
  vec2 p = (uv * 2.0 - 1.0) * u_half;
  float aa = 1.2 * u_half.y / u_size.y;
  o = u_scene == 0 ? fjord(p, aa) : treeline(p, aa);
}`;
