/* Shared shell behaviour: status line, history drawer, toasts, health polling, copy. */

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

/* ------------------------------------------------------- copy code block -- */

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  const block = btn.closest(".codeblock");
  const text = block?.dataset.code ?? "";
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied";
    btn.classList.add("ok");
  } catch {
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

function paintState() {
  if (!bar) return;
  const info = window.AURORA?.info || {};
  bar.dataset.phase = phase?.key || info.status || "cold";
  if (stateEl) stateEl.textContent = phase?.text || STATE_WORDS[info.status] || info.status || "";
}

/**
 * Put the status line into a request phase — "working", "queued", "reading",
 * "writing" — with the words to show, or pass nothing to hand it back to the
 * health poll.
 */
export function setPhase(key, text) {
  phase = key ? { key, text } : null;
  paintState();
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

// Toasts sit just above the line, which is one row or two depending on width.
if (bar && "ResizeObserver" in window) {
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--status-h", `${bar.offsetHeight}px`);
  }).observe(bar);
}

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
    // Resident memory is not measurable on every host. Where it is not, the
    // figure is left off the line rather than shown as a dash.
    const rss = $("#st-rss");
    if (rss) {
      rss.hidden = !info.rss_mb;
      if (info.rss_mb) $("#b-rss").textContent = `${info.rss_mb} MB`;
    }

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
