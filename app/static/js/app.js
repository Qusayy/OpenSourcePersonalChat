/* Shared shell behaviour: status line, history drawer, toasts, health polling, copy. */

import { REDUCED, EASE } from "./motion.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* -------------------------------------------------------------- toasts -- */

export function toast(message, kind = "") {
  const host = $("#toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity 200ms, translate 200ms";
    el.style.opacity = "0";
    el.style.translate = "0 8px";
    setTimeout(() => el.remove(), 220);
  }, 4200);
}

/* ------------------------------------------------------ history drawer -- */

const rail = $("#rail");
const menuBtn = $("#menu-btn");

/** Open or close the history drawer. A native dialog, so the focus trap,
 *  Escape and the return of focus to the opener are the browser's job. */
export function setRail(open) {
  if (!rail) return;
  if (open && !rail.open) rail.showModal();
  else if (!open && rail.open) rail.close();
  menuBtn?.setAttribute("aria-expanded", String(open));
}

menuBtn?.addEventListener("click", () => setRail(true));
$("#rail-close")?.addEventListener("click", () => setRail(false));
// Escape closes the dialog without going through setRail.
rail?.addEventListener("close", () => menuBtn?.setAttribute("aria-expanded", "false"));
// The dialog has no padding of its own, so a click on it is a click on the backdrop.
rail?.addEventListener("click", (e) => {
  if (e.target === rail) setRail(false);
});

/* ------------------------------------------------------------- copying -- */

/**
 * Put text on the clipboard; resolves to whether it got there.
 *
 * `navigator.clipboard` exists only in a secure context. Reached at a bare IP
 * over plain HTTP, which is how a small self-hosted box is often first opened,
 * it is undefined and every Copy button would fail. There, and wherever the
 * API refuses, the text is selected in an off-screen field and the document
 * is asked to copy it, which needs no secure context.
 */
export async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* refused; try the older route */
    }
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.readOnly = true; // no on-screen keyboard on touch devices
  field.setAttribute("aria-hidden", "true");
  // 16px so iOS does not zoom to it; fixed so selecting it cannot scroll the page.
  field.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;font-size:16px;";
  const returnTo = document.activeElement;
  // Inside an open modal dialog everything outside it is inert, a field in
  // <body> included, so the field goes where focus is allowed to be.
  (document.querySelector("dialog[open]") || document.body).appendChild(field);
  field.focus({ preventScroll: true });
  field.select();
  field.setSelectionRange(0, text.length);
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  field.remove();
  returnTo?.focus?.({ preventScroll: true });
  return copied;
}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  const block = btn.closest(".codeblock");
  const text = block?.dataset.code ?? "";
  if (await copyText(text)) {
    btn.textContent = "Copied";
    btn.classList.add("ok");
  } else {
    toast("Clipboard blocked by the browser", "err");
  }
  setTimeout(() => {
    btn.textContent = "Copy";
    btn.classList.remove("ok");
  }, 1600);
});

/* --------------------------------------------------------- status line --- */

const bar = $("#status");
const stateEl = $("#b-state");

// What the server's own states are called on screen. base.html renders the
// same words so the first paint agrees with the first poll.
const STATE_WORDS = {
  cold: "starting",
  loading: "loading",
  ready: "ready",
  error: "model unavailable",
};

// Set by the chat page while a request is in flight; null means "show what
// the health poll says".
let phase = null;
// The engine is answering somebody else. Said on the line, so cores at full
// load beside the word "ready" are explained rather than left to puzzle over.
let otherBusy = false;

function paintState() {
  if (!bar) return;
  const info = window.AURORA?.info || {};
  let key = phase?.key || info.status || "cold";
  let text = phase?.text || STATE_WORDS[info.status] || info.status || "";
  if (!phase && info.status === "ready" && otherBusy) {
    key = "busy";
    text = "busy";
  }
  bar.dataset.phase = key;
  if (stateEl && text !== shownWord) setStateWord(text);
}

// What the line currently says. Compared against rather than read back from
// the element, which briefly holds two words while one replaces the other.
let shownWord = stateEl?.textContent.trim() ?? "";

/**
 * Change the state word. Only called when the word is actually different: the
 * outgoing one leaves upward as the new one comes up from below, so a change
 * of state is seen to happen rather than found to have happened.
 */
function setStateWord(text) {
  shownWord = text;
  const next = document.createElement("span");
  next.className = "st-word-in";
  next.textContent = text;
  const old = stateEl.querySelector(".st-word-in:not(.out)");
  if (REDUCED || !old || !old.animate || document.hidden) {
    stateEl.replaceChildren(next);
    return;
  }
  old.classList.add("out");
  stateEl.appendChild(next);
  old.animate(
    [
      { opacity: 1, translate: "0 0" },
      { opacity: 0, translate: "0 -70%" },
    ],
    { duration: 140, easing: "cubic-bezier(0.7, 0, 0.84, 0)", fill: "forwards" }
  ).onfinish = () => old.remove();
  next.animate(
    [
      { opacity: 0, translate: "0 70%" },
      { opacity: 1, translate: "0 0" },
    ],
    { duration: 220, easing: EASE }
  );
}

/**
 * Put the status line into a request phase — "working", "queued", "reading",
 * "writing" — with the words to show, or pass nothing to hand it back to the
 * health poll.
 */
export function setPhase(key, text) {
  const was = Boolean(phase);
  phase = key ? { key, text } : null;
  paintState();
  // A request starting or ending changes how often the machine is sampled;
  // do not wait out the slower interval to notice.
  if (was !== Boolean(phase)) scheduleTelemetry(250);
}

/** The phase a request is in right now, or null. */
export function currentPhase() {
  return phase?.key || null;
}

/**
 * A change that landed while nobody was looking stays marked until the tab is
 * seen, then clears itself. Does nothing if the tab is already visible.
 */
export function holdPhase(text) {
  if (!document.hidden) return;
  setPhase("held", text);
  const seen = () => {
    if (document.hidden) return;
    document.removeEventListener("visibilitychange", seen);
    setTimeout(() => {
      if (phase?.key === "held") setPhase(null);
    }, 4000);
  };
  document.addEventListener("visibilitychange", seen);
}

// On a narrow screen the line shows one figure and keeps the rest behind this.
const more = $("#status-more");
more?.addEventListener("click", () => {
  const open = bar.dataset.open !== "true";
  bar.dataset.open = String(open);
  more.setAttribute("aria-expanded", String(open));
  more.textContent = open ? "Hide" : "Details";
});

// How wide a classic scrollbar is here; zero where scrollbars overlay. The
// thread's scroller reserves this much on both sides so its column stays
// centred, and the composer beside it needs the same amount to match.
{
  const probe = document.createElement("div");
  probe.style.cssText = "position:absolute;top:-999px;width:80px;height:80px;overflow:scroll";
  document.body.appendChild(probe);
  document.documentElement.style.setProperty("--sbw", `${probe.offsetWidth - probe.clientWidth}px`);
  probe.remove();
}

// Toasts sit just above the line, which is one row or two depending on width.
if (bar && "ResizeObserver" in window) {
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--status-h", `${bar.offsetHeight}px`);
  }).observe(bar);
}

/* ------------------------------------------------------------- motion --- */

// The arrival plays once. The class that starts it is set in the head before
// first paint; taking it off afterwards is what stops it replaying whenever a
// section comes back on screen (New chat brings the headline back).
if (document.documentElement.classList.contains("intro")) {
  setTimeout(() => document.documentElement.classList.remove("intro"), 1100);
}

// Charts draw themselves when scrolled to. Elements start drawn; this marks
// the page as able to do better than that, and each chart as arrived.
const revealIO =
  "IntersectionObserver" in window && !REDUCED
    ? new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            entry.target.classList.add("in-view");
            revealIO.unobserve(entry.target);
          }
        },
        { threshold: 0.2 }
      )
    : null;

/** Have `el` draw itself the next time it is on screen (now, if it already is). */
export function reveal(el) {
  if (!el) return;
  if (!revealIO) {
    el.classList.add("in-view");
    return;
  }
  el.classList.remove("in-view");
  void el.offsetWidth; // commit the undrawn state before it can be drawn
  revealIO.observe(el);
}

document.documentElement.classList.add("reveal-ready");
$$(".stack").forEach(reveal);

/* ---------------------------------------------------------- telemetry --- */

// What the machine is doing, from /api/telemetry: the CPUs this process can
// use and how loaded each is, the machine's memory, this app's share of it.
// These are measurements taken on the server, not estimates made here.

const sys = {
  cpu: $("#st-cpu"),
  count: $("#cpu-count"),
  noun: $("#cpu-noun"),
  bars: $("#cpu-bars"),
  pct: $("#cpu-pct"),
  ram: $("#st-ram"),
  used: $("#ram-used"),
  total: $("#ram-total"),
  fill: $("#ram-fill"),
};
const MAX_CORE_BARS = 8; // past this, columns would be a comb; one bar for all
const gb = (mb) => (mb / 1024).toFixed(1);
const cpuText = (n) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/** Fill a named slot on the line or on a page, hiding its row when there is nothing to say. */
function fact(name, value) {
  for (const el of $$(`[data-sys="${name}"]`)) {
    el.textContent = value ?? "";
    const row = el.closest("[data-sys-row]");
    if (row) row.hidden = value === null || value === undefined;
  }
}

function paintSystem(s) {
  if (!s) return;

  if (sys.cpu) {
    sys.cpu.hidden = s.cpus === null;
    if (s.cpus !== null) {
      sys.count.textContent = cpuText(s.cpus);
      sys.noun.textContent = s.cpus === 1 ? "core" : "cores";
    }
    const cores = s.cores_pct;
    if (cores && s.cpu_pct !== null) {
      const one = cores.length > MAX_CORE_BARS;
      const want = one ? 1 : cores.length;
      sys.bars.classList.toggle("agg", one);
      while (sys.bars.children.length < want) sys.bars.appendChild(document.createElement("i"));
      while (sys.bars.children.length > want) sys.bars.lastElementChild.remove();
      [...sys.bars.children].forEach((el, i) =>
        el.style.setProperty("--v", ((one ? s.cpu_pct : cores[i]) / 100).toFixed(3))
      );
      sys.pct.textContent = `${Math.round(s.cpu_pct)}%`;
    }
    sys.cpu.title =
      "The whole machine, measured now" + (s.load ? ` · load average ${s.load.join(" ")}` : "");
  }

  if (sys.ram) {
    const known = s.mem_total_mb !== null && s.mem_used_mb !== null;
    sys.ram.hidden = !known;
    if (known) {
      const share = s.mem_used_mb / s.mem_total_mb;
      sys.used.textContent = gb(s.mem_used_mb);
      sys.total.textContent = `${gb(s.mem_total_mb)} GB`;
      sys.fill.style.setProperty("--v", share.toFixed(3));
      bar.dataset.ram = share > 0.9 ? "high" : "ok";
      sys.ram.title =
        "The whole machine's memory, measured now" +
        (s.rss_mb !== null ? ` · this app holds ${gb(s.rss_mb)} GB` : "");
    }
  }

  // The same figures wherever a page names them (About, Benchmark, and the
  // opened status line on a narrow screen).
  fact("cpus", s.cpus === null ? null : `${cpuText(s.cpus)} ${s.cpus === 1 ? "core" : "cores"}`);
  fact("cpu", s.cpu_pct === null ? null : `${Math.round(s.cpu_pct)}%`);
  fact(
    "ram",
    s.mem_total_mb === null || s.mem_used_mb === null
      ? null
      : `${gb(s.mem_used_mb)} / ${gb(s.mem_total_mb)} GB`
  );
  fact("rss", s.rss_mb === null ? null : `${gb(s.rss_mb)} GB`);
  // The ceiling that actually bites is the lower of the two the service has.
  const limit = [s.limit_high_mb, s.limit_mb].filter((v) => v !== null);
  fact("limit", limit.length ? `${gb(Math.min(...limit))} GB` : null);
  fact("load", s.load ? s.load.map((v) => v.toFixed(2)).join("  ") : null);
}

let teleTimer = null;
// Once a second while a request is running, when the figures are moving; every
// three seconds otherwise. The server has two cores and other work to do.
const teleDelay = () => (phase && phase.key !== "held" ? 1000 : 3000);

async function pollTelemetry() {
  teleTimer = null;
  if (document.hidden) return; // picked up again when the tab is seen
  try {
    const res = await fetch("/api/telemetry");
    const data = await res.json();
    paintSystem(data.system);
    const busy = Boolean(data.engine?.busy);
    if (busy !== otherBusy) {
      otherBusy = busy;
      paintState();
    }
  } catch {
    /* a missed sample is not worth a message; the next is seconds away */
  }
  scheduleTelemetry();
}

function scheduleTelemetry(delay = teleDelay()) {
  clearTimeout(teleTimer);
  teleTimer = setTimeout(pollTelemetry, delay);
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) scheduleTelemetry(0);
});

paintSystem(window.AURORA?.system);
scheduleTelemetry(1000);

/* ------------------------------------------------------------- health --- */

const listeners = new Set();
export function onHealth(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

let lastStatus = window.AURORA?.info?.status;

export async function pollHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    const info = data.engine;
    window.AURORA.info = info;

    paintState();
    const mock = $("#b-mock");
    if (mock) mock.hidden = !info.mock;

    if (info.status !== lastStatus) {
      if (info.status === "ready" && lastStatus === "loading") toast("Model warm — ready to go");
      // info.error carries the server's filesystem path; the visitor needs to
      // know it failed, not where the weights were expected.
      if (info.status === "error") toast("The model could not be loaded", "err");
      lastStatus = info.status;
    }
    listeners.forEach((fn) => fn(info));
    return info;
  } catch {
    return null;
  }
}

// Poll briskly while the model is loading, then settle down — this box has no
// cycles to spare for a chatty front-end.
function scheduleHealth() {
  const info = window.AURORA?.info;
  const delay = info?.status === "ready" ? 15000 : 1500;
  setTimeout(async () => {
    await pollHealth();
    scheduleHealth();
  }, delay);
}

pollHealth();
scheduleHealth();
