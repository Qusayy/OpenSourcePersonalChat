import{i as e,r as t}from"./frame-ByRsHuUW.js";var n=[{x:-.36,y:.37,mag:-.2,tint:.34},{x:-.17,y:.42,mag:.5,tint:.52},{x:.04,y:.35,mag:-.7,tint:.22},{x:.26,y:.41,mag:.3,tint:.78},{x:.4,y:.27,mag:-.1,tint:.46},{x:-.4,y:.2,mag:.6,tint:.61},{x:-.21,y:.24,mag:-.5,tint:.4},{x:.12,y:.19,mag:.4,tint:.3},{x:.3,y:.12,mag:-.9,tint:.56},{x:-.3,y:.04,mag:.1,tint:.86},{x:-.08,y:.08,mag:.6,tint:.26},{x:.41,y:-.04,mag:.2,tint:.5},{x:-.39,y:-.14,mag:-.4,tint:.44},{x:-.16,y:-.1,mag:.3,tint:.7},{x:.07,y:-.06,mag:-.6,tint:.36},{x:.24,y:-.18,mag:.5,tint:.58}],r=`#version 300 es
void main() {
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`,i=`#version 300 es
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
}`,a=`#version 300 es
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
const float TEXELS = 512.0;
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
}`,o=`#version 300 es
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
}`,s=`#version 300 es
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
}`,c=`#version 300 es
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
}`,l=`#version 300 es
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
}`,u=`#version 300 es
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
}`,d=`#version 300 es
precision highp float;
precision highp sampler2D;

uniform sampler2D u_sky;
uniform sampler2D u_noise;
uniform vec2 u_size;       // the canvas, in pixels
uniform vec2 u_half;
uniform float u_horizon;   // units
uniform float u_time;
out vec4 o;               // premultiplied

float ridged(float n) {
  return 1.0 - abs(n * 2.0 - 1.0);
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

void main() {
  vec2 uv = gl_FragCoord.xy / u_size;
  vec2 p = (uv * 2.0 - 1.0) * u_half;
  float aa = 1.2 * u_half.y / u_size.y;
  o = fjord(p, aa);
}`,f=1.35,p=12e3,m=6.5,h=1.4,g=.03;function _(e){let t=e>>>0;return()=>{t=t+1831565813>>>0;let e=t;return e=Math.imul(e^e>>>15,e|1),e^=e+Math.imul(e^e>>>7,e|61),((e^e>>>14)>>>0)/4294967296}}function v(e){return .42+.95*10**(-.4*(e-.8)*.33)}function y(e){return Math.min(2.4,.22+1.6*10**(-.4*(e-.8)*.45))}function b(e,t){for(let r of n)if(Math.hypot(r.x-e,r.y-t)<g)return!0;return!1}function x(){let e=_(1369620702),t=new Float32Array((n.length+p)*6),r=0;for(let e of n)t[r++]=e.x,t[r++]=e.y,t[r++]=v(e.mag),t[r++]=y(e.mag),t[r++]=e.tint,t[r++]=0;let i=10**(.5*h),a=10**(.5*m);for(let n=0;n<p;n++){let n=0,o=0;do n=(e()*2-1)*f,o=(e()*2-1)*f;while(b(n,o));let s=Math.log10(i+e()*(a-i))/.5;t[r++]=n,t[r++]=o,t[r++]=v(s),t[r++]=y(s),t[r++]=e(),t[r++]=1e-4+e()*.9999}return t}var S=[{scale:1,steps:26},{scale:.84,steps:24},{scale:.7,steps:20},{scale:.58,steps:18},{scale:.48,steps:16},{scale:.4,steps:14}],C=45,w=16.9,ee=6.9,T=38,te=16;function E(e,t,n){let r=e.createShader(t);return r?(e.shaderSource(r,n),e.compileShader(r),e.getShaderParameter(r,e.COMPILE_STATUS)?r:(console.warn(`[sky] shader failed:`,e.getShaderInfoLog(r)),e.deleteShader(r),null)):null}function D(e,t,n){let r=E(e,e.VERTEX_SHADER,t),i=E(e,e.FRAGMENT_SHADER,n);if(!r||!i)return null;let a=e.createProgram();return e.attachShader(a,r),e.attachShader(a,i),e.linkProgram(a),e.getProgramParameter(a,e.LINK_STATUS)?a:(console.warn(`[sky] link failed:`,e.getProgramInfoLog(a)),null)}function O(e,t,n){let r={};for(let i of n)r[i]=e.getUniformLocation(t,`u_${i}`);return r}var ne=[`res`,`half`,`focal`,`pitch`,`yaw`,`time`,`steps`,`quant`,`grain`,`prev`,`blend`,`noise`],re=[`sky`,`size`,`half`,`frame`],ie=[`scale`,`dpr`,`time`,`horizon`,`density`,`maxPoint`],ae=[`scale`,`unit`],oe=[`sky`,`noise`,`size`,`half`,`horizon`,`time`];function se(e){let t=e.getExtension(`WEBGL_debug_renderer_info`),n=String(e.getParameter(t?t.UNMASKED_RENDERER_WEBGL:e.RENDERER)??``);return/swiftshader|llvmpipe|softpipe|software|basic render/i.test(n)}var k=null;function A(f){k?.release();let p=f.getContext(`webgl2`,{alpha:!1,antialias:!1,depth:!1,stencil:!1,powerPreference:`high-performance`,failIfMajorPerformanceCaveat:!0});if(!p||p.isContextLost())return null;let m=p;if(se(m))return m.getExtension(`WEBGL_lose_context`)?.loseContext(),null;let h={width:0,height:0,unit:1},g={segments:new Float32Array(320),count:0,glow:new Float32Array(n.length),active:!1},_={frame:0,goal:0,scale:0,steps:S[0].steps},v=x(),y=v.length/6,b=new Float32Array(n.length*6),E=new Float32Array(2304),A=null,j=null,ce=null,le=null,ue=null,M=null,N=null,P=null,F=[null,null],I=[null,null],L=0,R=!0,de=0,z=0,B=!1,fe=64,V=!1,H=!0,U=1,W=.5,G=0,K=0,q=0,pe=!1,me=!1,he=e(),ge=performance.now(),_e=new Float32Array(C),J=new Uint8Array(S.length),Y=0,X=0,ve=30,ye=0,Z=0,Q=0;function be(e,t,n){let r=m.createTexture();m.bindTexture(m.TEXTURE_2D,r),m.texImage2D(m.TEXTURE_2D,0,B?m.RGBA16F:m.RGBA8,e,t,0,m.RGBA,B?m.HALF_FLOAT:m.UNSIGNED_BYTE,null);let i=n?m.REPEAT:m.CLAMP_TO_EDGE;return m.texParameteri(m.TEXTURE_2D,m.TEXTURE_WRAP_S,i),m.texParameteri(m.TEXTURE_2D,m.TEXTURE_WRAP_T,i),m.texParameteri(m.TEXTURE_2D,m.TEXTURE_MAG_FILTER,m.LINEAR),m.texParameteri(m.TEXTURE_2D,m.TEXTURE_MIN_FILTER,n?m.LINEAR_MIPMAP_LINEAR:m.LINEAR),r}function xe(){let e=D(m,r,i);if(!e)return!1;P=be(512,512,!0);let t=m.createFramebuffer();return m.bindFramebuffer(m.FRAMEBUFFER,t),m.framebufferTexture2D(m.FRAMEBUFFER,m.COLOR_ATTACHMENT0,m.TEXTURE_2D,P,0),m.checkFramebufferStatus(m.FRAMEBUFFER)===m.FRAMEBUFFER_COMPLETE?(m.viewport(0,0,512,512),m.useProgram(e),m.uniform1f(m.getUniformLocation(e,`u_size`),512),m.bindVertexArray(j),m.drawArrays(m.TRIANGLES,0,3),m.bindFramebuffer(m.FRAMEBUFFER,null),m.deleteFramebuffer(t),m.deleteProgram(e),m.bindTexture(m.TEXTURE_2D,P),m.generateMipmap(m.TEXTURE_2D),!0):(m.bindFramebuffer(m.FRAMEBUFFER,null),m.deleteFramebuffer(t),m.deleteProgram(e),!1)}function Se(e,t){let n=m.createVertexArray(),r=m.createBuffer();return m.bindVertexArray(n),m.bindBuffer(m.ARRAY_BUFFER,r),m.bufferData(m.ARRAY_BUFFER,e,t),m.enableVertexAttribArray(0),m.vertexAttribPointer(0,2,m.FLOAT,!1,24,0),m.enableVertexAttribArray(1),m.vertexAttribPointer(1,4,m.FLOAT,!1,24,8),[n,r]}function Ce(){B=!!m.getExtension(`EXT_color_buffer_float`);let e=m.getParameter(m.ALIASED_POINT_SIZE_RANGE);if(fe=e?e[1]:64,j=m.createVertexArray(),!xe()&&(!B||(B=!1,!xe())))return!1;let t=D(m,r,a),n=D(m,r,o),i=D(m,s,c),f=D(m,l,u),p=D(m,r,d);if(!t||!n||!i||!f||!p)return!1;A={sky:t,blit:n,star:i,line:f,ground:p,skyAt:O(m,t,ne),blitAt:O(m,n,re),starAt:O(m,i,ie),lineAt:O(m,f,ae),groundAt:O(m,p,oe)},[ce]=Se(v,m.STATIC_DRAW),[le,ue]=Se(b,m.DYNAMIC_DRAW),M=m.createVertexArray(),N=m.createBuffer(),m.bindVertexArray(M),m.bindBuffer(m.ARRAY_BUFFER,N),m.bufferData(m.ARRAY_BUFFER,E,m.DYNAMIC_DRAW);for(let e=0;e<3;e++)m.enableVertexAttribArray(e),m.vertexAttribPointer(e,2,m.FLOAT,!1,24,e*8);m.bindVertexArray(null);for(let e=0;e<2;e++)F[e]=be(2,2,!1),I[e]=m.createFramebuffer(),m.bindFramebuffer(m.FRAMEBUFFER,I[e]),m.framebufferTexture2D(m.FRAMEBUFFER,m.COLOR_ATTACHMENT0,m.TEXTURE_2D,F[e],0);return m.bindFramebuffer(m.FRAMEBUFFER,null),G=K=0,!0}function we(){let e=W*S[q].scale,t=Math.max(2,Math.round(f.width*e)),n=Math.max(2,Math.round(f.height*e));if(_.scale=e,_.steps=S[q].steps,t!==G||n!==K){G=t,K=n;for(let e of F)m.bindTexture(m.TEXTURE_2D,e),m.texImage2D(m.TEXTURE_2D,0,B?m.RGBA16F:m.RGBA8,t,n,0,m.RGBA,B?m.HALF_FLOAT:m.UNSIGNED_BYTE,null);R=!0}}function $(e){q=e,we(),ve=12,Y=0,X=0}function Te(){if(!H)return h.width>0;H=!1;let e=f.clientWidth,t=f.clientHeight;if(!e||!t)return h.width=0,!1;U=Math.min(window.devicePixelRatio||1,2),h.width=e,h.height=t,h.unit=Math.min(e,t);let n=Math.round(e*U),r=Math.round(t*U);return(f.width!==n||f.height!==r)&&(f.width=n,f.height=r),W=U>=1.5?.5:.7,J.fill(0),Z=0,$(q),!0}function Ee(e){if(ve>0){ve--,e>=60&&++ye>=5&&q<S.length-1&&(ye=0,$(S.length-1));return}if(_e[Y++]=e,X+=e,Y<C)return;let t=X/Y;_e.sort();let n=_e[Math.floor(C*.2)];Y=0,X=0,(!Q||n<Q*.8)&&(Q=Math.min(w,Math.max(ee,n))),_.frame=t,_.goal=Q,t>Q*1.28?(J[q]=1,Z=0,q<S.length-1?$(q+1):Q<w&&t<w*1.28&&(Q=w,J.fill(0))):t<Q*1.1&&q>0&&!J[q-1]?++Z>=3&&(Z=0,$(q-1)):Z=0}function De(){let e=0;for(let t=0;t<n.length;t++){let r=g.glow[t];if(r<.004)continue;let i=n[t],a=e*6;b[a]=i.x,b[a+1]=i.y,b[a+2]=3+2.2*r,b[a+3]=.5*r,b[a+4]=i.tint,b[a+5]=0,e++}return e}function Oe(){let e=te/h.unit,t=0;for(let n=0;n<g.count;n++){let r=n*5,i=g.segments[r],a=g.segments[r+1],o=g.segments[r+2],s=g.segments[r+3],c=g.segments[r+4],l=Math.hypot(o-i,s-a);if(l<1e-5||c<=0)continue;let u=(o-i)/l,d=(s-a)/l,f=-d*e,p=u*e,m=i-u*e,h=a-d*e,_=o+u*e,v=s+d*e,y=t*36;for(let t=0;t<6;t++){let n=t===2||t===3||t===5,r=t===1||t===4||t===5?-1:1;E[y++]=(n?_:m)+f*r,E[y++]=(n?v:h)+p*r,E[y++]=e*r,E[y++]=n?l+e:-e,E[y++]=l,E[y++]=c}t++}return t}function ke(e){let t=A;if(!t)return;let n=h.width/h.unit/2,r=h.height/h.unit/2,i=Math.max(.72,.92*r),a=r*(2*(h.height>h.width?.15:.18)-1),o=Math.atan(-a/i),s=.55;m.disable(m.BLEND),m.activeTexture(m.TEXTURE1),m.bindTexture(m.TEXTURE_2D,P),m.activeTexture(m.TEXTURE0),m.bindVertexArray(j);let c=F[L];m.activeTexture(m.TEXTURE2),m.bindTexture(m.TEXTURE_2D,F[1-L]),m.activeTexture(m.TEXTURE0),m.bindFramebuffer(m.FRAMEBUFFER,I[L]),m.viewport(0,0,G,K),m.bindTexture(m.TEXTURE_2D,null),m.useProgram(t.sky),m.uniform2f(t.skyAt.res,G,K),m.uniform2f(t.skyAt.half,n,r),m.uniform1f(t.skyAt.focal,i),m.uniform2f(t.skyAt.pitch,Math.cos(o),Math.sin(o)),m.uniform2f(t.skyAt.yaw,Math.cos(s),Math.sin(s)),m.uniform1f(t.skyAt.time,e),m.uniform1i(t.skyAt.steps,S[q].steps),m.uniform1f(t.skyAt.quant,+!B);let l=de%64*5.588238;m.uniform2f(t.skyAt.grain,l,l),m.uniform1i(t.skyAt.noise,1),m.uniform1i(t.skyAt.prev,2),m.uniform1f(t.skyAt.blend,R?1:B?.2:.4),m.drawArrays(m.TRIANGLES,0,3),R=!1,L=1-L,de++,m.bindFramebuffer(m.FRAMEBUFFER,null),m.viewport(0,0,f.width,f.height),m.bindTexture(m.TEXTURE_2D,c),m.useProgram(t.blit),m.uniform1i(t.blitAt.sky,0),m.uniform2f(t.blitAt.size,f.width,f.height),m.uniform2f(t.blitAt.half,n,r),m.uniform1f(t.blitAt.frame,he?0:e*60%1024),m.drawArrays(m.TRIANGLES,0,3),m.enable(m.BLEND),m.blendFunc(m.ONE,m.ONE);let u=2*h.unit/h.width,d=2*h.unit/h.height;if(m.useProgram(t.star),m.uniform2f(t.starAt.scale,u,d),m.uniform1f(t.starAt.dpr,U),m.uniform1f(t.starAt.time,e),m.uniform1f(t.starAt.horizon,a),m.uniform1f(t.starAt.density,Math.min(1,Math.max(.1,(h.unit/1080)**2))),m.uniform1f(t.starAt.maxPoint,fe),m.bindVertexArray(ce),m.drawArrays(m.POINTS,0,y),g.active){let e=De();e&&(m.bindVertexArray(le),m.bindBuffer(m.ARRAY_BUFFER,ue),m.bufferSubData(m.ARRAY_BUFFER,0,b,0,e*6),m.drawArrays(m.POINTS,0,e));let n=Oe();n&&(m.useProgram(t.line),m.uniform2f(t.lineAt.scale,u,d),m.uniform1f(t.lineAt.unit,h.unit),m.bindVertexArray(M),m.bindBuffer(m.ARRAY_BUFFER,N),m.bufferSubData(m.ARRAY_BUFFER,0,E,0,n*36),m.drawArrays(m.TRIANGLES,0,n*6))}m.blendFunc(m.ONE,m.ONE_MINUS_SRC_ALPHA),m.useProgram(t.ground),m.uniform1i(t.groundAt.sky,0),m.uniform1i(t.groundAt.noise,1),m.uniform2f(t.groundAt.size,f.width,f.height),m.uniform2f(t.groundAt.half,n,r),m.uniform1f(t.groundAt.horizon,a),m.uniform1f(t.groundAt.time,e),m.bindVertexArray(j),m.drawArrays(m.TRIANGLES,0,3),pe||(pe=!0,f.dataset.ready=``)}function Ae(e,t){if(Ie.onStep?.(e,t),V||!A)return;let n=H;if(Te()){if(he){let e=g.active||me;if(me=g.active,(!pe||n)&&(z=24),z<=0&&!e)return;z--,ke(T)}else Ee(t),ke(T+(e-ge)/1e3)}}let je=e=>{e.preventDefault(),V=!0},Me=()=>{V=!Ce(),H=!0},Ne=()=>{H=!0};f.addEventListener(`webglcontextlost`,je),f.addEventListener(`webglcontextrestored`,Me),window.addEventListener(`resize`,Ne);let Pe=typeof ResizeObserver==`function`?new ResizeObserver(Ne):null;if(Pe?.observe(f),!Ce())return f.removeEventListener(`webglcontextlost`,je),f.removeEventListener(`webglcontextrestored`,Me),window.removeEventListener(`resize`,Ne),Pe?.disconnect(),null;let Fe=t(Ae),Ie={view:h,overlay:g,stats:_,onStep:null,release(){k===Ie&&(k=null,Fe(),Pe?.disconnect(),window.removeEventListener(`resize`,Ne),f.removeEventListener(`webglcontextlost`,je),f.removeEventListener(`webglcontextrestored`,Me))}};return k=Ie,Ie}var j=1300,ce=900,le=450,ue={mouse:.045,touch:.072},M=22;function N(e,t,r={}){let{view:i,overlay:a}=t,o=[],s=null,c=!1,l=0,u=0,d=-1,f=0,p=null,m=0,h=0,g=0;function _(){let t=e.getBoundingClientRect();h=t.left,g=t.top}function v(e){l=(e.clientX-h-i.width/2)/i.unit,u=-(e.clientY-g-i.height/2)/i.unit}function y(){let e=Math.max(c?ue.touch:ue.mouse,M/i.unit),t=-1,r=e*e;for(let e=0;e<n.length;e++){let i=n[e],a=(i.x-l)**2+(i.y-u)**2;a<r&&(r=a,t=e)}return t}function b(e){e<0||o[o.length-1]===e||o.length>=48||(o.push(e),a.glow[e]=1.6)}function x(){o.length=0,m=0}function S(t){s!==null||t.pointerType===`mouse`&&t.button!==0||(s=t.pointerId,ee(),c=t.pointerType!==`mouse`,e.setPointerCapture?.(t.pointerId),_(),v(t),o.length&&performance.now()-f>j&&x(),m=1,b(y()))}function C(e){if(s===null){if(e.pointerType!==`mouse`)return;c=!1,_(),v(e),d=y();return}if(e.pointerId!==s)return;let t=e.getCoalescedEvents?.()??[];for(let e of t)v(e),b(y());v(e),b(y())}function w(e,t){if(e.pointerId===s&&(s=null,f=performance.now(),d=-1,t&&o.length>=7)){let e=o.slice();p=setTimeout(()=>{p=null,r.onChain?.(e)},le)}}function ee(){p!==null&&clearTimeout(p),p=null}let T=e=>w(e,!0),te=e=>w(e,!1),E=()=>{d=-1},D=e=>{c&&e.preventDefault()};function O(e,t){if(s===null&&o.length){let t=e-f;if(t>2200)x();else if(t>j){let e=(t-j)/ce;m=(1-e)*(1-e)}}let r=1-Math.exp(-t/110),i=o.length>0;for(let e=0;e<n.length;e++){let t=o.includes(e)?m:e===d?.55:0,n=a.glow[e]+(t-a.glow[e])*r;a.glow[e]=n,n>.004&&(i=!0)}let c=0;for(let e=1;e<o.length&&c<64;e++){let t=n[o[e-1]],r=n[o[e]],i=c++*5;a.segments[i]=t.x,a.segments[i+1]=t.y,a.segments[i+2]=r.x,a.segments[i+3]=r.y,a.segments[i+4]=m}if(s!==null&&o.length&&c<64){let e=n[o[o.length-1]],t=c++*5;a.segments[t]=e.x,a.segments[t+1]=e.y,a.segments[t+2]=l,a.segments[t+3]=u,a.segments[t+4]=.7}a.count=c,a.active=i}return e.addEventListener(`pointerdown`,S),e.addEventListener(`pointermove`,C),e.addEventListener(`pointerup`,T),e.addEventListener(`pointercancel`,te),e.addEventListener(`pointerleave`,E),e.addEventListener(`contextmenu`,D),t.onStep=O,{chain:o,release(){ee(),e.removeEventListener(`pointerdown`,S),e.removeEventListener(`pointermove`,C),e.removeEventListener(`pointerup`,T),e.removeEventListener(`pointercancel`,te),e.removeEventListener(`pointerleave`,E),e.removeEventListener(`contextmenu`,D),t.onStep===O&&(t.onStep=null),a.count=0,a.glow.fill(0),a.active=!1}}}function P(e){let t=A(e);if(t)return{sky:t,canvas:e};let n=e.cloneNode(!1);e.replaceWith(n);let r=F(n);return r?{sky:r,canvas:n}:null}function F(e){let r=e.getContext(`2d`,{alpha:!1}),i=document.createElement(`canvas`),a=i.getContext(`2d`,{alpha:!1});if(!r||!a)return null;let o={width:0,height:0,unit:1},s={segments:new Float32Array(320),count:0,glow:new Float32Array(n.length),active:!1},c={frame:0,goal:0,scale:1,steps:0},l=x(),u=1,d=!0,f=!1,p=e=>(o.width/2+e*o.unit)*u,m=e=>(o.height/2-e*o.unit)*u;function h(){if(!a)return;let t=e.width,n=e.height;i.width=t,i.height=n;let r=a.createLinearGradient(0,n,0,0);r.addColorStop(0,`#0b1620`),r.addColorStop(.38,`#050a14`),r.addColorStop(1,`#03060c`),a.fillStyle=r,a.fillRect(0,0,t,n);let s=Math.min(1,Math.max(.1,(o.unit/1080)**2));for(let e=0;e<l.length;e+=6){if(l[e+5]>s)continue;let r=p(l[e]),i=m(l[e+1]);if(r<0||i<0||r>t||i>n)continue;let o=Math.max(.6,l[e+2]*u*.8);a.globalAlpha=Math.min(1,l[e+3]*.7),a.fillStyle=`#ffffff`,a.beginPath(),a.arc(r,i,o,0,Math.PI*2),a.fill()}a.globalAlpha=1}function g(){d=!1;let t=e.clientWidth,n=e.clientHeight;!t||!n?o.width=0:(u=Math.min(window.devicePixelRatio||1,2),o.width=t,o.height=n,o.unit=Math.min(t,n),e.width=Math.round(t*u),e.height=Math.round(n*u),h())}function _(){if(r&&(r.globalCompositeOperation=`source-over`,r.drawImage(i,0,0),s.active)){r.globalCompositeOperation=`lighter`;for(let e=0;e<n.length;e++){let t=s.glow[e];if(t<.004)continue;let i=n[e],a=p(i.x),o=m(i.y),c=(10+8*t)*u,l=r.createRadialGradient(a,o,0,a,o,c);l.addColorStop(0,`rgb(210 232 255 / ${Math.min(1,.55*t)})`),l.addColorStop(1,`rgb(210 232 255 / 0)`),r.fillStyle=l,r.fillRect(a-c,o-c,c*2,c*2)}r.lineCap=`round`,r.lineWidth=1.5*u;for(let e=0;e<s.count;e++){let t=e*5;r.strokeStyle=`rgb(184 227 255 / ${Math.min(1,s.segments[t+4])})`,r.beginPath(),r.moveTo(p(s.segments[t]),m(s.segments[t+1])),r.lineTo(p(s.segments[t+2]),m(s.segments[t+3])),r.stroke()}}}function v(t,n){C.onStep?.(t,n);let r=d;if(r&&g(),!o.width)return;let i=s.active||f;f=s.active,(r||i)&&(_(),e.dataset.ready=``)}let y=()=>{d=!0};window.addEventListener(`resize`,y);let b=typeof ResizeObserver==`function`?new ResizeObserver(y):null;b?.observe(e);let S=t(v),C={view:o,overlay:s,stats:c,onStep:null,release(){S(),b?.disconnect(),window.removeEventListener(`resize`,y)}};return C}var I=Date.UTC(2e3,0,6,18,14),L=2551442876.8992,R=[[.033,`new moon`],[.217,`waxing crescent`],[.283,`first quarter`],[.467,`waxing gibbous`],[.533,`full moon`],[.717,`waning gibbous`],[.783,`last quarter`],[.967,`waning crescent`]];function de(e){let t=((e.getTime()-I)%L+L)%L/L;for(let[e,n]of R)if(t<e)return{share:t,name:n};return{share:t,name:`new moon`}}function z(e){let t=new Intl.DateTimeFormat(`en-GB`,{day:`numeric`,month:`long`}),n=()=>{let n=new Date,r=t.formatToParts(n);e.textContent=``;for(let t of r)if(t.type===`day`){let n=document.createElement(`span`);n.className=`num`,n.textContent=t.value,e.append(n)}else e.append(t.value);e.append(` · ${de(n).name}`)};n();let r=setInterval(n,6e5);return()=>clearInterval(r)}async function B(e){if(e.length<7)return!1;try{let t=await fetch(`/api/unlock`,{method:`POST`,headers:{"Content-Type":`application/json`},body:JSON.stringify({chain:e}),credentials:`same-origin`,cache:`no-store`});return t.ok?(await t.json())?.ok===!0:!1}catch{return!1}}var fe=700;function V(){document.documentElement.dataset.opening=``,window.setTimeout(()=>window.location.reload(),fe)}var H=document.querySelector(`#sky`),U=document.querySelector(`#readout`);U&&z(U);var W=H?P(H):null;if(W){let{sky:e}=W;N(W.canvas,e,{onChain:e=>{B(e).then(e=>{e&&V()})}})}else document.documentElement.dataset.flat=``;