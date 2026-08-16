/* Shared shell behaviour: canvas, mobile rail, toasts, health polling, copy. */

import { startCanvas } from "./canvas.js";

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* ------------------------------------------------------------- canvas --- */

/**
 * Null when WebGL2 is unavailable — every caller uses `canvas?.pulse()`, and
 * the CSS gradient layers stay visible as the fallback.
 */
export const canvas = (() => {
  const el = $("#gl");
  if (!el) return null;
  try {
    return startCanvas(el);
  } catch (err) {
    console.warn("[aurora] canvas unavailable:", err);
    return null;
  }
})();

/* -------------------------------------------------------------- toasts -- */

export function toast(message, kind = "") {
  const host = $("#toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => {
    el.style.transition = "opacity 300ms, transform 300ms";
    el.style.opacity = "0";
    el.style.transform = "translateY(8px)";
    setTimeout(() => el.remove(), 320);
  }, 4200);
}

/* ---------------------------------------------------------- mobile rail -- */

const rail = $("#rail");
const scrim = $("#rail-scrim");

const menuBtn = $("#menu-btn");

export function setRail(open) {
  if (!rail) return;
  const was = rail.dataset.open === "true";
  rail.dataset.open = String(open);
  rail.setAttribute("aria-hidden", String(!open && isDrawer()));
  menuBtn?.setAttribute("aria-expanded", String(open));
  if (scrim) {
    scrim.hidden = !open;
    scrim.dataset.open = String(open);
  }
  // A drawer that opens without moving focus leaves a keyboard or screen-reader
  // user still outside it, and closing without returning focus strands them at
  // the top of the page.
  if (!isDrawer() || open === was) return;
  if (open) rail.querySelector("button, a")?.focus();
  else menuBtn?.focus();
}

/** True only while the rail is actually behaving as an overlay drawer. */
function isDrawer() {
  return window.matchMedia("(max-width: 860px)").matches;
}

menuBtn?.addEventListener("click", () => setRail(rail?.dataset.open !== "true"));
scrim?.addEventListener("click", () => setRail(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setRail(false);
});

// Leaving drawer width must not leave the rail marked hidden.
window.matchMedia("(max-width: 860px)").addEventListener("change", (e) => {
  if (!e.matches) {
    rail?.removeAttribute("aria-hidden");
    rail?.removeAttribute("data-open");
    if (scrim) scrim.hidden = true;
  }
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

    const dot = $("#status-dot");
    if (dot) {
      dot.className = `dot ${info.status}`;
      dot.title = info.error || info.status;
    }
    const state = $("#b-state");
    if (state) state.textContent = info.mock ? "mock" : info.status;
    const rss = $("#b-rss");
    if (rss) rss.textContent = info.rss_mb ? `${info.rss_mb} MB` : "—";
    const strip = document.querySelector('[data-strip="rss"]');
    if (strip) strip.textContent = info.rss_mb ?? "—";

    if (info.status !== lastStatus) {
      if (info.status === "ready" && lastStatus === "loading") toast("Model warm — ready to go");
      if (info.status === "error") toast(info.error || "Model failed to load", "err");
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
