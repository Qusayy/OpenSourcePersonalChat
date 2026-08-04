/* Motion primitives — one shared feel instead of ad-hoc transitions.
 *
 * All of it honours prefers-reduced-motion by jumping straight to the end
 * state rather than by animating faster.
 */

export const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Critically-damped spring. Calls onFrame(value) until it settles. */
export function spring(from, to, onFrame, { stiffness = 170, damping = 26, mass = 1 } = {}) {
  if (REDUCED) {
    onFrame(to);
    return () => {};
  }
  let value = from;
  let velocity = 0;
  let raf = null;
  let last = performance.now();

  const step = (now) => {
    const dt = Math.min((now - last) / 1000, 0.064);
    last = now;
    const force = -stiffness * (value - to) - damping * velocity;
    velocity += (force / mass) * dt;
    value += velocity * dt;

    if (Math.abs(value - to) < 0.001 && Math.abs(velocity) < 0.01) {
      onFrame(to);
      return;
    }
    onFrame(value);
    raf = requestAnimationFrame(step);
  };
  raf = requestAnimationFrame(step);
  return () => raf !== null && cancelAnimationFrame(raf);
}

/** Spring an element in from below with a slight scale. */
export function enter(el, { delay = 0, distance = 14 } = {}) {
  if (REDUCED) {
    el.style.opacity = "1";
    return;
  }
  el.style.opacity = "0";
  el.style.transform = `translateY(${distance}px) scale(.985)`;
  setTimeout(() => {
    el.style.transition = "opacity 260ms cubic-bezier(.16,1,.3,1)";
    el.style.opacity = "1";
    spring(0, 1, (t) => {
      el.style.transform = `translateY(${(1 - t) * distance}px) scale(${0.985 + t * 0.015})`;
      if (t === 1) el.style.transform = "";
    });
  }, delay);
}

/** Stagger a NodeList/array of elements. */
export function stagger(elements, gap = 40) {
  [...elements].forEach((el, i) => enter(el, { delay: i * gap }));
}

/** Animate a number readout. Keeps thousands separators and decimals. */
export function countUp(el, to, { decimals = 0, duration = 700 } = {}) {
  const target = Number(to);
  if (!Number.isFinite(target)) {
    el.textContent = String(to);
    return;
  }
  const format = (v) =>
    v.toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  if (REDUCED) {
    el.textContent = format(target);
    return;
  }
  const from = 0;
  const start = performance.now();
  const tick = (now) => {
    const t = Math.min((now - start) / duration, 1);
    // easeOutExpo — fast then settling, matches the spring elsewhere
    const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
    el.textContent = format(from + (target - from) * eased);
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/**
 * FLIP: capture positions, mutate the DOM, then animate the delta so elements
 * slide to their new places instead of jumping.
 */
export function flip(elements) {
  const first = new Map();
  [...elements].forEach((el) => first.set(el, el.getBoundingClientRect()));

  return function play() {
    if (REDUCED) return;
    first.forEach((box, el) => {
      const now = el.getBoundingClientRect();
      const dx = box.left - now.left;
      const dy = box.top - now.top;
      if (!dx && !dy) return;
      el.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
        { duration: 320, easing: "cubic-bezier(.16,1,.3,1)" }
      );
    });
  };
}

/** A one-shot attention pulse, used when a tool result lands. */
export function flash(el) {
  if (REDUCED) return;
  el.animate(
    [
      { boxShadow: "0 0 0 0 rgba(34,211,238,.55)" },
      { boxShadow: "0 0 0 14px rgba(34,211,238,0)" },
    ],
    { duration: 700, easing: "cubic-bezier(.16,1,.3,1)" }
  );
}
