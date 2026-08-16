/* The pass plate.
 *
 * Two SVG layers behind the whole page: an engraved graticule, and a ground
 * track drawn across it.
 *
 * The track is not decoration. Its axes carry real data:
 *
 *   x — seconds elapsed since the request was sent
 *   y — instantaneous tokens per second
 *
 * So the background of the page IS the throughput plot, at plate scale. This
 * is the whole reason the direction survives its own recorded risk: a field of
 * curves that meant nothing would read as a decorative world map and claim
 * location data this product does not have. Plotting the one quantity the
 * product actually measures makes it an instrument instead.
 *
 * A request is a contact window, and it runs through phases:
 *
 *   idle       graticule, horizon, and a dashed PREDICTED arc. No axis numbers,
 *              because nothing has been measured yet.
 *   acquiring  prefill. A contact band opens at the current time; its edge
 *              travels (marching ants) so it reads as live without glowing.
 *   live       first token is AOS: the mark drops with the real TTFT, axis
 *              numbers appear, and the track extends as samples arrive.
 *   closed     LOS mark drops, the band closes onto exactly [AOS, LOS], and the
 *              track settles from signal ink to a spent tone.
 *   failed     the band edge and the closing mark turn fault red.
 *
 * Under prefers-reduced-motion the data still plots — that is measurement, not
 * animation — but nothing travels, marches, or eases.
 */

const NS = "http://www.w3.org/2000/svg";

const TOP_PAD = 0.12; // apex ceiling, as a fraction of viewport height
const HORIZON = 0.62; // the horizon rule; below it is the "below horizon" field
// Left margin the y axis labels live in. Wide enough on desktop for the full
// "75 tok/s"; on a phone the unit is dropped and the margin shrinks with it,
// because 72px of a 390px plate is a quarter of the field.
const padFor = (w) => (w < 700 ? 44 : 72);
const MIN_SPAN = 6; // shortest x axis, in seconds — most answers are short, and
// a 20s axis crushed a two-second pass into an unreadable sliver at the origin
const MIN_RATE = 8; // tok/s ceiling floor, so an early slow sample is not a spike

/** Small helper: create an SVG element with attributes in one call. */
function svg(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  return node;
}

/** Nice round axis ceiling, so the y labels are readable numbers. */
function niceCeil(value) {
  const steps = [10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200];
  for (const s of steps) if (value <= s) return s;
  return Math.ceil(value / 100) * 100;
}

export function startPlate(gratEl, trackEl) {
  if (!gratEl || !trackEl) return null;

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let W = 0;
  let H = 0;
  let horizonY = 0;
  let topY = 0;
  let padL = padFor(0);

  // A pass in progress.
  let phase = "idle";
  let samples = []; // {t, r} — seconds since send, tokens per second
  let t0 = 0;
  let aosT = null; // seconds at which the first token landed
  let losT = null;
  let ttft = null;
  let peak = MIN_RATE;
  let raf = null;
  let lastPaint = 0;

  /* ------------------------------------------------------------ layers -- */

  function measure() {
    const w = Math.max(1, gratEl.clientWidth || window.innerWidth);
    const h = Math.max(1, gratEl.clientHeight || window.innerHeight);
    if (w === W && h === H) return false;
    W = w;
    H = h;
    horizonY = Math.round(H * HORIZON);
    topY = Math.round(H * TOP_PAD);
    padL = padFor(W);
    for (const node of [gratEl, trackEl]) {
      node.setAttribute("viewBox", `0 0 ${W} ${H}`);
    }
    return true;
  }

  /**
   * The graticule is drawn at a density you could measure against. A faint
   * decorative grid would make this a dark theme with a texture; this is meant
   * to be a plate, so the cells are real and every fourth line is heavier.
   */
  function drawGraticule() {
    const cell = W < 700 ? 30 : 44;
    const minor = [];
    const major = [];

    // Columns are laid out from the horizon's left edge; rows from the horizon
    // itself, so the heavy lines land on the axis rather than near it.
    for (let x = 0, i = 0; x <= W; x += cell, i++) {
      (i % 4 === 0 ? major : minor).push(`M${x} 0V${H}`);
    }
    for (let y = horizonY, i = 0; y >= 0; y -= cell, i++) {
      (i % 4 === 0 ? major : minor).push(`M0 ${y}H${W}`);
    }
    for (let y = horizonY + cell, i = 1; y <= H; y += cell, i++) {
      (i % 4 === 0 ? major : minor).push(`M0 ${y}H${W}`);
    }

    gratEl.replaceChildren(
      svg("path", { class: "g-minor", d: minor.join("") }),
      svg("path", { class: "g-major", d: major.join("") }),
      svg("path", { class: "g-horizon", d: `M0 ${horizonY}H${W}` })
    );
  }

  /* ------------------------------------------------------- the geometry -- */

  /** The predicted pass: shape only, no numbers, because nothing is measured. */
  function predictedPath() {
    const amp = horizonY - topY;
    const pts = [];
    for (let i = 0; i <= 96; i++) {
      const x = (i / 96) * W;
      const y = horizonY - amp * Math.pow(Math.sin((Math.PI * i) / 96), 1.5);
      pts.push(`${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`);
    }
    return pts.join("");
  }

  /** Seconds of x axis currently shown. Grows with the pass; never shrinks. */
  function span() {
    const end = losT ?? (samples.length ? samples[samples.length - 1].t : 0);
    if (end <= 0) return MIN_SPAN;
    // Coarser steps as the pass runs long, so the axis re-ranges a few times
    // rather than continuously — an instrument, not a treadmill.
    const step = end < 20 ? 5 : end < 60 ? 15 : 30;
    return Math.max(MIN_SPAN, Math.ceil(end / step) * step);
  }

  const xOf = (t) => padL + (t / span()) * (W - padL);
  const yOf = (r) => horizonY - (Math.min(r, peak) / peak) * (horizonY - topY);

  function trackPath() {
    if (!samples.length) return "";
    const pts = samples.slice();
    // The meter samples a few times a second, so a track drawn only through
    // its readings trails the open band's edge by a visible gap. Carry the
    // last known rate forward to the present instant: the head then sits where
    // the band is, and it still asserts nothing that was not measured.
    if (phase === "live") pts.push({ t: now(), r: pts[pts.length - 1].r });
    return pts
      .map((s, i) => `${i ? "L" : "M"}${xOf(s.t).toFixed(1)} ${yOf(s.r).toFixed(1)}`)
      .join("");
  }

  /* ---------------------------------------------------------- rendering -- */

  function label(x, y, text, cls = "") {
    const node = svg("text", { class: `t-label ${cls}`.trim(), x, y });
    node.textContent = text;
    return node;
  }

  /** One full repaint of the track layer. Cheap: a handful of nodes. */
  function paint() {
    const kids = [];

    if (phase === "idle") {
      kids.push(svg("path", { class: "t-pred", d: predictedPath() }));
      const amp = horizonY - topY;
      const at = (frac) => ({
        x: frac * W,
        y: horizonY - amp * Math.pow(Math.sin(Math.PI * frac), 1.5),
      });
      // Nodes at all three, but only the apex is labelled: the AOS and LOS
      // ends of the arc sit low on the plate, exactly where the content column
      // is, and their labels landed on top of the page's own text.
      for (const frac of [0.16, 0.5, 0.84]) {
        const p = at(frac);
        kids.push(svg("circle", { class: "t-node", cx: p.x, cy: p.y, r: 2.5 }));
      }
      const apex = at(0.5);
      kids.push(label(apex.x + 8, apex.y - 7, "APEX"));
      kids.push(label(10, horizonY - 8, "HORIZON", "t-axis"));
      kids.push(label(10, horizonY + 16, "PREDICTED PASS · NO CONTACT", "t-axis"));
    } else {
      // Axis numbers only exist once something has actually been measured.
      const secs = span();
      for (let s = 5; s < secs; s += 5) {
        const x = xOf(s);
        kids.push(svg("path", { class: "t-tickline", d: `M${x} ${horizonY}v6` }));
        kids.push(label(x + 4, horizonY + 15, `${s}s`, "t-axis"));
      }
      for (const frac of [0.5, 1]) {
        const r = Math.round(peak * frac);
        const text = W < 700 ? `${r} t/s` : `${r} tok/s`;
        kids.push(label(6, yOf(r) - 4, text, "t-axis"));
        kids.push(svg("path", { class: "t-tickline", d: `M${padL - 8} ${yOf(r)}h8` }));
      }

      // The contact band: open-ended while acquiring, closed once the pass ends.
      const bandStart = aosT === null ? xOf(now()) - 2 : xOf(aosT);
      const bandEnd =
        losT !== null ? xOf(losT) : aosT === null ? xOf(now()) + 2 : xOf(now());
      kids.push(
        svg("rect", {
          class: `t-window ${phase === "acquiring" ? "acquiring" : ""} ${
            phase === "failed" ? "failed" : ""
          }`.trim(),
          x: Math.min(bandStart, bandEnd),
          y: topY - 10,
          width: Math.max(2, Math.abs(bandEnd - bandStart)),
          height: horizonY - topY + 10,
        })
      );

      if (samples.length) {
        kids.push(
          svg("path", {
            class: phase === "closed" || phase === "failed" ? "t-done" : "t-live",
            d: trackPath(),
          })
        );
      }

      if (aosT !== null) {
        const x = xOf(aosT);
        kids.push(svg("path", { class: "t-mark", d: `M${x} ${topY - 10}V${horizonY}` }));
        kids.push(svg("circle", { class: "t-aos", cx: x, cy: yOf(samples[0]?.r ?? 0), r: 3.5 }));
        kids.push(label(x + 7, topY + 4, `AOS +${Math.round(ttft ?? 0)} MS`));
      }
      if (losT !== null) {
        const x = xOf(losT);
        kids.push(
          svg("path", {
            class: `t-mark ${phase === "failed" ? "failed" : "closed"}`,
            d: `M${x} ${topY - 10}V${horizonY}`,
          })
        );
        kids.push(
          label(
            Math.max(4, x - 96),
            topY + 4,
            phase === "failed" ? "PASS FAILED" : "LOS",
            phase === "failed" ? "failed" : ""
          )
        );
      }
      if (phase === "acquiring") {
        kids.push(label(bandEnd + 8, topY + 4, "ACQUIRING…"));
      }
    }

    trackEl.replaceChildren(...kids);
  }

  const now = () => (performance.now() - t0) / 1000;

  /* ---------------------------------------------------------- the clock -- */

  function tick(ts) {
    raf = requestAnimationFrame(tick);
    // ~20fps. This is a background plot; a 60fps path rebuild would spend the
    // visitor's battery to show them nothing extra.
    if (ts - lastPaint < 50) return;
    lastPaint = ts;
    if (document.hidden) return;
    paint();
  }

  function run(on) {
    // Under reduced motion there is no clock loop at all: the band never
    // creeps and the track never sweeps. Data still lands, because `sample`
    // repaints directly — a plot that refuses to show measurements would be
    // removing information, not motion.
    if (on && !reduced) {
      if (raf === null) raf = requestAnimationFrame(tick);
    } else if (raf !== null) {
      cancelAnimationFrame(raf);
      raf = null;
    }
  }

  /* -------------------------------------------------------------- setup -- */

  measure();
  drawGraticule();
  paint();

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (!measure()) return;
      drawGraticule();
      paint();
    }, 120);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && (phase === "acquiring" || phase === "live")) paint();
  });

  return {
    /** The request went out: open a contact window and wait for acquisition. */
    open() {
      phase = "acquiring";
      samples = [];
      t0 = performance.now();
      aosT = null;
      losT = null;
      ttft = null;
      peak = MIN_RATE;
      paint();
      run(true);
    },

    /** First token. This is AOS — the moment contact is actually established. */
    acquire(ttftMs) {
      if (phase !== "acquiring") return;
      phase = "live";
      ttft = ttftMs ?? now() * 1000;
      aosT = now();
      samples = [{ t: aosT, r: 0 }];
      paint();
    },

    /** One throughput reading, straight from the meter. */
    sample(rate) {
      if (phase !== "live") return;
      if (!Number.isFinite(rate)) return;
      peak = niceCeil(Math.max(peak, rate * 1.15, MIN_RATE));
      samples.push({ t: now(), r: rate });
      // The plot is the only place this data lives at plate scale; keep it
      // bounded so a very long pass cannot grow the path without limit.
      if (samples.length > 900) samples = samples.filter((_, i) => i % 2 === 0);
      if (reduced) paint(); // no rAF loop under reduced motion — repaint on data
    },

    /**
     * The pass ends. The band closes onto the measured window.
     *
     * Guarded on the live phases because the stream's teardown calls this after
     * `done` has already closed the pass; without the guard the LOS mark would
     * jump forward by the teardown's own duration and stop being a measurement.
     */
    close() {
      if (phase !== "acquiring" && phase !== "live") return;
      losT = now();
      phase = "closed";
      run(false);
      paint();
    },

    fail() {
      if (phase !== "acquiring" && phase !== "live") return;
      losT = now();
      phase = "failed";
      run(false);
      paint();
    },

    /** New conversation: back to a clean plate showing only the prediction. */
    reset() {
      phase = "idle";
      samples = [];
      aosT = losT = ttft = null;
      peak = MIN_RATE;
      run(false);
      paint();
    },

    stop() {
      run(false);
    },
  };
}
