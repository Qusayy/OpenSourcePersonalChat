/* Chat page controller. */

import { $, $$, toast, copyText, setRail, onHealth, setPhase, holdPhase } from "./app.js";
import { renderMarkdown, splitBlocks, escapeHtml } from "./md.js";
import { sseFetch } from "./stream.js";
import { Meter, fmt, fmtInt, fmtMs } from "./metrics.js";
import { mountCard } from "./cards.js";
import { mountSkills, setupPalette } from "./skills.js";
import { enter, onFrame, REDUCED, EASE } from "./motion.js";
import { aurora } from "./aurora.js";
import { icon, hasIcon } from "./icons.js";

/** Fade a transient element out before removing it. */
function dismiss(el) {
  if (!el) return;
  if (REDUCED) return el.remove();
  el.dataset.leaving = "true";
  setTimeout(() => el.remove(), 160);
}

const state = {
  conversationId: null,
  persona: localStorage.getItem("aurora.persona") || window.AURORA.defaultPersona,
  skill: null,
  streaming: false,
  requestId: null,
};

const els = {
  main: $("#main"),
  thread: $("#thread"),
  wrap: $("#stream-wrap"),
  form: $("#composer"),
  input: $("#input"),
  send: $("#send"),
  sendLabel: $("#send-label"),
  list: $("#convo-list"),
  bar: $("#status"),
  wait: $("#st-wait"),
  waitLead: $("#st-wait-lead"),
  waitS: $("#st-wait-s"),
  tpsFig: $(".st-tps"),
  ttftFig: $(".st-ttft"),
  tps: $("#tps-value"),
  ttft: $("#ttft-value"),
  ctxValue: $("#ctx-value"),
  ctxFill: $("#ctx-fill"),
  spark: $("#spark"),
  skillGrid: $("#skill-grid"),
  skillChip: $("#skill-active"),
  palette: $("#palette"),
  status: $("#sr-status"),
  charCount: $("#char-count"),
};

const MAX_CHARS = 8000; // matches the server's Field(max_length=8000)

/** The single screen-reader announcement channel. */
function announce(message) {
  if (!els.status) return;
  els.status.textContent = message;
}

/* ------------------------------------------------------------- personas -- */

function paintPersonas() {
  $$(".persona").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.persona === state.persona))
  );
  placePersonaInk();
}

// One rule that slides under whichever persona is chosen. It is positioned
// from the chosen button's own box, so it follows a wrap onto a second row and
// a change of font; `moving` is added only after the first placement, so it
// appears in place on load and travels thereafter.
const personaRow = $("#persona-row");
const personaInk = document.createElement("span");
personaInk.className = "persona-ink";
personaInk.setAttribute("aria-hidden", "true");
personaRow?.appendChild(personaInk);

function placePersonaInk() {
  const chosen = personaRow?.querySelector('.persona[aria-pressed="true"]');
  if (!chosen || !chosen.offsetWidth) return;
  const pad = parseFloat(getComputedStyle(chosen).paddingLeft) || 0;
  personaInk.style.setProperty("--x", `${chosen.offsetLeft + pad}px`);
  personaInk.style.setProperty("--y", `${chosen.offsetTop + chosen.offsetHeight - 3}px`);
  personaInk.style.setProperty("--w", String(Math.max(0, chosen.offsetWidth - pad * 2)));
  personaRow.classList.add("inked");
  if (!REDUCED) requestAnimationFrame(() => personaInk.classList.add("moving"));
}
window.addEventListener("resize", placePersonaInk);
document.fonts?.ready.then(placePersonaInk);
$$(".persona").forEach((b) =>
  b.addEventListener("click", () => {
    state.persona = b.dataset.persona;
    localStorage.setItem("aurora.persona", state.persona);
    paintPersonas();
  })
);
paintPersonas();

/* --------------------------------------------------------------- skills -- */

/** What the empty input says, given the server's condition. */
function placeholderFor(info) {
  if (info.status === "ready") return "Ask something…";
  return info.status === "error"
    ? "The model could not be loaded — nothing can be sent right now"
    : "The model is still loading…";
}

function setSkill(skill) {
  state.skill = skill;
  if (!els.skillChip) return;
  if (!skill) {
    els.skillChip.hidden = true;
    els.input.placeholder = placeholderFor(window.AURORA.info);
    return;
  }
  els.skillChip.hidden = false;
  els.skillChip.innerHTML = `
    <span>Skill</span>
    <b>${escapeHtml(skill.name)}</b>
    <button class="text-btn small quiet x" type="button">Clear</button>`;
  els.skillChip.querySelector(".x").addEventListener("click", () => setSkill(null));
  els.input.placeholder = skill.placeholder || `${skill.name}…`;
  els.input.focus();
}

mountSkills(els.skillGrid, (skill) => {
  setSkill(skill);
  els.input.focus();
});

if (els.palette) {
  setupPalette(els.input, els.palette, { onSkill: setSkill });
}

/* -------------------------------------------------------------- composer - */

function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 208)}px`;
}

/** Only speak up near the ceiling — a permanent counter is noise. */
function updateCharCount() {
  if (!els.charCount) return;
  const left = MAX_CHARS - els.input.value.length;
  const near = left <= 500;
  els.charCount.hidden = !near;
  if (near) {
    els.charCount.textContent = `${left.toLocaleString()} characters left`;
    els.charCount.classList.toggle("warn", left <= 0);
  }
}

els.input.addEventListener("input", () => {
  autosize();
  updateCharCount();
});
els.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && els.palette?.hidden !== false) {
    e.preventDefault();
    els.form.requestSubmit();
  }
});
els.form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.streaming) return stopGeneration();
  const text = els.input.value.trim();
  if (!text) return;
  els.input.value = "";
  autosize();
  updateCharCount();
  send(text);
});

document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    newChat();
  }
});

/* ---------------------------------------------------------------- render - */

function nearBottom() {
  const el = els.wrap;
  return el.scrollHeight - el.scrollTop - el.clientHeight < 140;
}

/**
 * Switch between the empty sheet and the thread. One attribute on <main>
 * carries it, so a view can never be half applied.
 *
 * `animate` is for the one change a visitor causes by sending the first
 * question: the composer travels to the foot of the sheet. Loading a stored
 * conversation or starting a new one just changes.
 */
function setView(view, { animate = false } = {}) {
  if (els.main.dataset.view === view) return;
  const apply = () => {
    els.main.dataset.view = view;
    // The thread was display:none until now, so nothing could scroll it.
    els.wrap.scrollTop = els.wrap.scrollHeight;
  };
  if (animate && !REDUCED && document.startViewTransition) document.startViewTransition(apply);
  else apply();
}

// Past this a message is a pasted document, not a question, and is set as
// text and folded rather than as the heading of the exchange.
const LONG_CHARS = 280;
const LONG_LINES = 4;

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  // Kept verbatim for "Ask again": the rendered body may be folded.
  el.dataset.raw = text;
  // dir="auto" so Arabic or Hebrew input lays out right-to-left on its own.
  el.innerHTML = `<div class="body" dir="auto">${escapeHtml(text).replace(/\n/g, "<br>")}</div>`;
  if (text.length > LONG_CHARS || text.split("\n").length > LONG_LINES) {
    el.classList.add("long");
    el.insertAdjacentHTML(
      "beforeend",
      '<button class="text-btn small quiet msg-more" type="button" aria-expanded="false">Show all</button>'
    );
  }
  els.thread.appendChild(el);
  els.wrap.scrollTop = els.wrap.scrollHeight;
  return el;
}

function addAssistant() {
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `
    <div class="body">
      <div class="timeline" hidden></div>
      <div class="cards"></div>
      <div class="prose" dir="auto"></div>
      <div class="msg-foot" hidden></div>
    </div>`;
  els.thread.appendChild(el);
  els.wrap.scrollTop = els.wrap.scrollHeight;
  return el;
}

/**
 * Incremental renderer for a streaming answer.
 *
 * Completed blocks are appended once and never touched again; only the block
 * still being written is re-rendered each tick. Rewriting the whole answer
 * measured 8.9ms per tick at 1200 tokens and rose with length — the cost was
 * DOM teardown and relayout, not the markdown parse (0.55ms of it).
 */
/**
 * Where the caret goes: inside the last block being written, at the end of its
 * text. Appended after that block instead, an inline caret following a <p>
 * starts a line of its own and sits under the sentence it is meant to end.
 */
const CARET_INSIDE = /^(P|UL|OL|LI|H[1-6]|BLOCKQUOTE|DIV|PRE|CODE|TABLE|TBODY|TR|TD)$/;
function caretHost(root) {
  let el = root;
  while (el.lastElementChild && CARET_INSIDE.test(el.lastElementChild.tagName)) {
    el = el.lastElementChild;
  }
  return el;
}

/* Ink-in.
 *
 * Text that has just arrived fades up from nothing, so an answer is seen being
 * written rather than appearing in slabs ten times a second. The block being
 * written is rebuilt on every tick, which would restart any animation inside
 * it. So the renderer remembers which stretches of that block are new, as
 * offsets into its rendered text with the time each arrived, and wraps them
 * again after every rebuild with the animation already that far along.
 */
const INK_MS = 280; // matches `ink-in` in chat.css

function inkIn(root, ranges, now) {
  if (!ranges.length) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node);
  let offset = 0;
  for (const node of nodes) {
    const start = offset;
    const end = start + node.nodeValue.length;
    offset = end;
    // White space between blocks is counted but never wrapped: a span there
    // would be a stray child of a list or a table row.
    if (!node.nodeValue.trim()) continue;
    // Latest stretch first. Wrapping splits the text node and leaves its
    // beginning in place, so the offsets of earlier stretches stay true.
    for (let i = ranges.length - 1; i >= 0; i--) {
      const from = Math.max(ranges[i].start, start);
      const to = Math.min(ranges[i].end, end);
      if (from >= to) continue;
      const range = document.createRange();
      range.setStart(node, from - start);
      range.setEnd(node, to - start);
      const span = document.createElement("span");
      span.className = "fresh";
      span.style.animationDelay = `${-Math.round(now - ranges[i].at)}ms`;
      range.surroundContents(span);
    }
  }
}

function proseRenderer(host) {
  const stable = document.createElement("div");
  const tail = document.createElement("div");
  const caret = document.createElement("span");
  caret.className = "caret";
  host.append(stable, tail);
  let settled = 0; // blocks already committed to `stable`
  let written = 0; // rendered length of the block being written, at the last tick
  let fresh = []; // {start, end, at}: stretches of that block still fading up

  /** Note what is new in a block `length` long, and return what is still fading. */
  const mark = (length, now) => {
    fresh = fresh.filter((r) => now - r.at < INK_MS);
    // Shorter than it was: a closing mark arrived and markdown re-read what
    // was there ("**bold" became bold). The offsets no longer mean what they
    // did, so nothing is wrapped this time.
    if (length < written) fresh = [];
    else if (length > written) fresh.push({ start: written, end: length, at: now });
    written = length;
    return fresh;
  };

  return {
    update(raw, caretOn) {
      const now = performance.now();
      const blocks = splitBlocks(raw);
      // Commit every block that is now finished, appending rather than
      // rebuilding so existing nodes are left alone.
      while (settled < blocks.length - 1) {
        const html = renderMarkdown(blocks[settled]);
        if (REDUCED) {
          stable.insertAdjacentHTML("beforeend", html);
        } else {
          // Its last words may still be fading up, and it will not be
          // rendered again: wrap them once, here, on the way in.
          const done = document.createElement("div");
          done.innerHTML = html;
          inkIn(done, mark(done.textContent.length, now), now);
          stable.append(...done.childNodes);
          written = 0;
          fresh = [];
        }
        settled++;
      }
      const last = blocks[blocks.length - 1];
      tail.innerHTML = last === undefined ? "" : renderMarkdown(last);
      if (!REDUCED) inkIn(tail, mark(tail.textContent.length, now), now);
      if (caretOn) caretHost(tail).appendChild(caret);
      else caret.remove();
    },
    finish(raw) {
      // One clean pass at the end so the finished answer is a single tree.
      caret.remove();
      stable.innerHTML = "";
      tail.innerHTML = renderMarkdown(raw);
      settled = 0;
      written = 0;
      fresh = [];
    },
  };
}

// How a tool came to be chosen, where that says something. A slash command
// is not listed: the question above the answer already is one. Only "model"
// costs a pass through the model; a pattern match is decided before the model
// is touched.
const ROUTE_WORDS = {
  heuristic: "Pattern match",
  model: "Model's choice",
};

/**
 * Create or update one step of the answer's route line.
 *
 * The line carries only what is said nowhere else. The answer step is the
 * status line's job while it runs and the footer's once it has. A finished
 * tool is named, timed and qualified by its own figure below. So a step is
 * drawn here while it is running, and afterwards only if it was a routing
 * decision worth knowing about.
 */
function upsertStep(host, data) {
  if (data.kind === "model") return;
  const status = data.status || "running";
  const over = status !== "running";
  let row = host.querySelector(`[data-id="${CSS.escape(data.id)}"]`);
  if (over && (data.kind === "tool" || (data.kind === "route" && data.detail === "slash"))) {
    row?.remove();
    host.hidden = !host.children.length;
    return;
  }
  if (!row) {
    row = document.createElement("div");
    row.className = "tl-step";
    row.dataset.id = data.id;
    row.innerHTML =
      '<span class="g"></span><span class="label"></span>' +
      '<span class="st"></span><span class="ms num"></span>';
    host.appendChild(row);
    enter(row, { distance: 6 });
  }
  host.hidden = false;
  row.dataset.status = status;
  let name = data.tool && hasIcon(data.tool) ? data.tool : "tool";
  let label = data.label || "";
  if (data.kind === "route") {
    name = "route";
    if (data.detail === "skill") label = `Skill: ${data.label}`;
    else if (data.tool && ROUTE_WORDS[data.detail]) label = ROUTE_WORDS[data.detail];
  }
  row.querySelector(".g").innerHTML = icon(name, 14);
  row.querySelector(".label").textContent = label;
  // Said in a word as well as a colour.
  row.querySelector(".st").textContent = over ? "" : "running";
  row.querySelector(".ms").textContent = data.ms ? fmtMs(data.ms) : "";
  return row;
}

// What a tool is called on screen, for cards replayed from a stored
// conversation, where only its registry name was kept.
const TOOL_LABELS = Object.fromEntries(
  (window.AURORA.tools || []).map((t) => [t.name, t.label || t.name])
);

function notice(kind, html) {
  const el = document.createElement("div");
  el.className = `notice ${kind}`;
  el.innerHTML = html;
  els.thread.appendChild(el);
  els.wrap.scrollTop = els.wrap.scrollHeight;
  return el;
}

/** An error the user can act on: says what broke and offers the way back. */
function errorNotice(message, retryText) {
  const el = notice("err", `${icon("error", 16)}<span>${escapeHtml(message)}</span>`);
  announce(message);
  if (retryText) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn notice-retry";
    btn.textContent = "Try again";
    btn.addEventListener("click", () => {
      el.remove();
      send(retryText);
    });
    el.appendChild(btn);
  }
  els.wrap.scrollTop = els.wrap.scrollHeight;
  return el;
}

function footer(el, m) {
  const foot = el.querySelector(".msg-foot");
  const bits = [];
  const has = (v) => v !== null && v !== undefined;
  // The numeral is fixed-width so a column of these lines up; the words
  // around it are words and are set as words.
  const fig = (value, words) => `<span><b class="num">${value}</b> ${words}</span>`;
  if (has(m.gen_tps)) bits.push(fig(fmt(m.gen_tps, 1), "tok/s"));
  if (has(m.ttft_ms)) bits.push(fig(fmtMs(m.ttft_ms), "to first token"));
  if (has(m.completion_tokens)) bits.push(fig(fmtInt(m.completion_tokens), "tokens"));
  if (has(m.total_ms)) bits.push(fig(fmtMs(m.total_ms), "to answer"));
  if (m.cancelled) bits.push("<span>stopped</span>");
  // The tools an answer used are the figures above it, so they are not
  // counted again here.
  // Always there, in words: an action that only appears on hover does not
  // exist on a phone, and an unlabelled glyph has to be guessed at.
  foot.innerHTML =
    `<span class="figs">${bits.join("")}</span>` +
    `<span class="act">
       <button class="text-btn small quiet" type="button" data-act="copy">Copy</button>
       <button class="text-btn small quiet" type="button" data-act="again">Ask again</button>
     </span>`;
  foot.hidden = false;
}

els.thread.addEventListener("click", (e) => {
  const more = e.target.closest(".msg-more");
  if (more) {
    const open = more.closest(".msg").classList.toggle("expanded");
    more.setAttribute("aria-expanded", String(open));
    more.textContent = open ? "Show less" : "Show all";
    return;
  }
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const msg = btn.closest(".msg");
  if (btn.dataset.act === "copy") {
    copyText(msg.dataset.raw || "").then((copied) =>
      copied ? toast("Answer copied") : toast("Clipboard blocked by the browser", "err")
    );
  } else if (btn.dataset.act === "again") {
    if (state.streaming) return;
    const prev = msg.previousElementSibling;
    const q = prev?.classList.contains("user") ? prev.dataset.raw : null;
    if (q) send(q);
  }
});

/* --------------------------------------------------------- status line --- */

const meter = new Meter({
  onSample: ({ rate, total, at }) => {
    // The meter ticks from the moment a request is sent. Until a token has
    // arrived there is no rate to report, and "0.0 tok/s" would be a claim.
    if (!total) return;
    traceSample(rate, at);
  },
});

/* The rate, live.
 *
 * The meter measures four times a second. Shown raw, the number flips and the
 * line jerks four times a second. So what is on screen moves every frame
 * toward what was last measured: the figure eases to the new rate and the
 * line is drawn a quarter of a second behind now, which is exactly enough for
 * its head to glide from one sample to the next. Nothing is invented: every
 * point on the line is a measured sample or lies on the straight line between
 * two of them.
 */
const TRACE_SPAN = 12000; // ms of history across the full width
const TRACE_LAG = 260; // drawn this far behind now; just over one sample interval
const trace = { samples: [], shown: 0, target: 0, top: 1, ink: "", off: null, ctx: null, w: 0, h: 0 };

function traceStart() {
  trace.samples = [];
  trace.shown = trace.target = 0;
  trace.top = 1;
  trace.ink = els.spark ? getComputedStyle(els.spark).color : "";
  if (!REDUCED && !trace.off) trace.off = onFrame(traceFrame);
}

function traceStop() {
  trace.off?.();
  trace.off = null;
  trace.samples = [];
  if (trace.ctx) trace.ctx.clearRect(0, 0, trace.w, trace.h);
}

function traceSample(rate, at) {
  trace.samples.push({ t: at, v: rate });
  while (trace.samples.length > 2 && at - trace.samples[0].t > TRACE_SPAN + 1000) trace.samples.shift();
  trace.target = rate;
  aurora.rate(rate);
  if (REDUCED) {
    // No frame loop: the figure and the line step from sample to sample.
    els.tps.textContent = fmt(rate, 1);
    drawTrace(at + TRACE_LAG);
  }
}

function traceFrame(now, dt) {
  trace.shown += (trace.target - trace.shown) * (1 - Math.exp(-dt / 140));
  const text = fmt(trace.shown, 1);
  if (els.tps.textContent !== text) els.tps.textContent = text;
  drawTrace(now);
}

/** Match the canvas's pixels to its box. False while it has no box (hidden). */
function traceFit() {
  const c = els.spark;
  if (!c || !c.clientWidth || !c.clientHeight) return false;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = Math.round(c.clientWidth * dpr);
  const h = Math.round(c.clientHeight * dpr);
  if (c.width !== w || c.height !== h) {
    c.width = w;
    c.height = h;
  }
  trace.ctx = c.getContext("2d");
  trace.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  trace.w = c.clientWidth;
  trace.h = c.clientHeight;
  return true;
}

/** The measured rate at time `t`: a sample, or the line between two. */
function rateAt(t) {
  const s = trace.samples;
  if (t <= s[0].t) return s[0].v;
  for (let i = 1; i < s.length; i++) {
    if (t <= s[i].t) {
      const a = s[i - 1];
      const b = s[i];
      return a.v + (b.v - a.v) * ((t - a.t) / (b.t - a.t || 1));
    }
  }
  return s[s.length - 1].v;
}

function drawTrace(now) {
  if (!traceFit()) return;
  const { ctx, w, h, samples } = trace;
  ctx.clearRect(0, 0, w, h);
  if (samples.length < 2) return;

  const head = now - TRACE_LAG;
  // Same scale as before: against the tallest sample, or 1 tok/s. The scale
  // eases as well, so a new peak does not make the whole line jump.
  const peak = Math.max(1, ...samples.map((s) => s.v));
  trace.top += (peak - trace.top) * (REDUCED ? 1 : 0.1);
  const pad = 3;
  const right = w - 3;
  const x = (t) => right - ((head - t) / TRACE_SPAN) * right;
  const y = (v) => h - pad - (Math.min(v, trace.top) / trace.top) * (h - pad * 2);

  ctx.beginPath();
  let drawing = false;
  for (const s of samples) {
    if (s.t > head) break;
    const px = x(s.t);
    if (px < -6) continue;
    if (drawing) ctx.lineTo(px, y(s.v));
    else ctx.moveTo(px, y(s.v));
    drawing = true;
  }
  const now_v = rateAt(head);
  if (drawing) ctx.lineTo(right, y(now_v));
  else ctx.moveTo(right, y(now_v));
  ctx.strokeStyle = ctx.fillStyle = trace.ink;
  ctx.lineWidth = 1.5;
  ctx.lineJoin = ctx.lineCap = "round";
  ctx.stroke();
  // where the rate is now
  ctx.beginPath();
  ctx.arc(right, y(now_v), 2.5, 0, Math.PI * 2);
  ctx.fill();
}

function setContext(tokens) {
  const max = window.AURORA.info.n_ctx || 4096;
  const pct = Math.max(0, Math.min(1, tokens / max));
  els.ctxValue.textContent = fmtInt(tokens);
  els.ctxFill.style.setProperty("--v", pct.toFixed(4));
  // Past this the next long answer is likely to push old turns out.
  els.bar.dataset.ctx = pct > 0.85 ? "high" : "ok";
}

/**
 * Take the cost figures off the line. They are there while a request is in
 * flight; once it ends they are printed under the answer they belong to, and
 * showing them here as well would say the same thing twice.
 */
function resetFigures() {
  els.tpsFig.hidden = els.ttftFig.hidden = true;
  els.tps.textContent = "—";
  els.ttft.textContent = "—";
  traceStop();
}

/** The first token has landed: the figures have something to report. */
function showFigures(ttftMs) {
  els.tpsFig.hidden = false;
  traceStart();
  if (ttftMs !== null && ttftMs !== undefined) {
    els.ttft.textContent = fmtMs(ttftMs);
    els.ttftFig.hidden = false;
  }
}

// The status line's state while a request is in flight. Every phase but
// "writing" is a wait, so it counts the seconds: on this hardware a wait is
// long enough that a still label looks like a hang. The count sits with the
// figures on the right, where it can change width without moving anything.
const PHASE_WORDS = { working: "working", queued: "queued", reading: "reading", writing: "writing" };
let phaseClock = null;
function livePhase(key, lead = "") {
  clearInterval(phaseClock);
  phaseClock = null;
  els.wait.hidden = true;
  // The one place the aurora is driven from: it follows the same phase the
  // status line shows, so the two cannot disagree.
  aurora.phase(key);
  if (!key) return setPhase(null);
  setPhase(key, PHASE_WORDS[key]);
  if (key === "writing") return;
  const since = performance.now();
  const paint = () => {
    const s = Math.floor((performance.now() - since) / 1000);
    els.waitLead.textContent = lead;
    els.waitS.textContent = s >= 2 ? `${s} s` : "";
    els.wait.hidden = !lead && s < 2;
  };
  paint();
  phaseClock = setInterval(paint, 1000);
}

/* ------------------------------------------------------------ streaming -- */

function setMode(streaming) {
  state.streaming = streaming;
  const label = streaming ? "Stop" : "Send";
  if (els.sendLabel.textContent !== label) {
    els.send.dataset.mode = streaming ? "stop" : "send";
    els.sendLabel.textContent = label;
    // The button changes what it does; the word is seen to change with it.
    if (!REDUCED && els.sendLabel.animate) {
      els.sendLabel.animate(
        [
          { opacity: 0, translate: "0 35%" },
          { opacity: 1, translate: "0 0" },
        ],
        { duration: 200, easing: EASE }
      );
    }
  }
  els.send.setAttribute("aria-label", streaming ? "Stop generating" : "Send message");
  livePhase(streaming ? "working" : null);
}

async function stopGeneration() {
  if (!state.requestId) return;
  try {
    await fetch(`/api/cancel/${state.requestId}`, { method: "POST" });
  } catch {
    /* the stream's own teardown stops it anyway */
  }
}

async function send(text) {
  if (state.streaming) return;
  setRail(false);
  addUser(text);
  setView("thread", { animate: true });

  const skill = state.skill;
  const bot = addAssistant();
  const prose = proseRenderer(bot.querySelector(".prose"));
  const timeline = bot.querySelector(".timeline");
  const cardHost = bot.querySelector(".cards");
  let queueCard = null;
  let queuedAs = null; // the queue wording currently on the status line
  let finishedUnseen = false;
  // What each tool step reported, kept for the figure that follows it.
  const stepMeta = new Map();
  let raw = "";
  let dirty = false;
  let caretOn = true;
  let cardIndex = 0;

  const paint = () => {
    // A backgrounded tab still receives tokens; there is no point laying them
    // out until someone can see them.
    if (!dirty || document.hidden) return;
    dirty = false;
    // Read first, then write: measuring the scroll position after the DOM has
    // grown would force a second synchronous layout.
    const stick = nearBottom();
    prose.update(raw, caretOn);
    if (stick) els.wrap.scrollTop = els.wrap.scrollHeight;
  };
  // 100ms is well under the eye's threshold for text appearing.
  const painter = setInterval(paint, 100);

  setMode(true);
  meter.start();
  resetFigures();

  const controller = new AbortController();

  try {
    await sseFetch("/api/chat", {
      body: {
        message: text,
        conversation_id: state.conversationId,
        persona: state.persona,
        skill: skill ? skill.id : null,
      },
      signal: controller.signal,
      onEvent: (event, data) => {
        if (event === "conversation") {
          state.conversationId = data.id;
          if (data.created) refreshList();
        } else if (event === "queue") {
          if (!queueCard) {
            queueCard = notice("queue", '<span class="spin"></span><span></span>');
          }
          const queueText =
            data.position > 1
              ? `${data.position} requests ahead of yours — this server runs one generation at a time.`
              : data.position === 1
              ? "One request ahead of yours — this server runs one generation at a time."
              : "Next in line — starting shortly.";
          queueCard.lastElementChild.textContent = queueText;
          announce(queueText);
          const ahead = data.position > 0 ? `${data.position} ahead` : "next in line";
          if (queuedAs !== ahead) livePhase("queued", (queuedAs = ahead));
        } else if (event === "step") {
          upsertStep(timeline, data);
          if (data.kind === "tool") stepMeta.set(data.id, { label: data.label, ms: data.ms });
          if (data.status === "failed") {
            announce(`${data.label} failed: ${data.detail || "unknown error"}`);
          }
        } else if (event === "card") {
          mountCard(cardHost, data.card, data.data, cardIndex++, stepMeta.get(data.step_id));
          if (nearBottom()) els.wrap.scrollTop = els.wrap.scrollHeight;
        } else if (event === "trimmed") {
          notice("", `<span>${
            data.dropped > 1
              ? `The ${data.dropped} oldest messages were dropped to fit the context window.`
              : "The oldest message was dropped to fit the context window."
          }</span>`);
        } else if (event === "start") {
          state.requestId = data.request_id;
          livePhase("reading"); // holds until the first token lands
          dismiss(queueCard);
          queueCard = null;
          setContext(data.prompt_tokens);
        } else if (event === "token") {
          // No layout reads here — the tick below batches one read and one
          // write per frame instead of a pair per token.
          // The release: prefill is over, the model is talking.
          if (!raw) {
            livePhase("writing");
            showFigures(data.ttft_ms);
          }
          raw += data.t;
          dirty = true;
          meter.token(1);
          aurora.pulse();
        } else if (event === "done") {
          caretOn = false;
          dirty = false;
          prose.finish(raw);
          bot.dataset.raw = raw;
          // The footer adds height below the last line of the answer. If the
          // reader was at the bottom they stay there; without this the figures
          // and the actions land just out of sight.
          const stick = nearBottom();
          footer(bot, data);
          if (stick) els.wrap.scrollTop = els.wrap.scrollHeight;
          setContext((data.prompt_tokens || 0) + (data.completion_tokens || 0));
          finishedUnseen = document.hidden;
          announce(
            data.cancelled
              ? "Generation stopped."
              : `Answer complete — ${fmtInt(data.completion_tokens)} tokens at ${fmt(
                  data.gen_tps, 1
                )} per second.`
          );
          refreshList();
        } else if (event === "error") {
          caretOn = false;
          prose.finish(raw);
          errorNotice(data.message || "The model could not answer that.", text);
        }
      },
    });
  } catch (err) {
    caretOn = false;
    dirty = false;
    prose.finish(raw);
    if (err.name !== "AbortError") {
      errorNotice(
        navigator.onLine
          ? err.message
          : "You appear to be offline — the answer stopped partway.",
        text
      );
    }
  } finally {
    clearInterval(painter);
    caretOn = false;
    if (raw) prose.finish(raw);
    dismiss(queueCard);
    meter.stop();
    setMode(false);
    resetFigures();
    // Finished while the tab was in the background: say so until it is seen.
    if (finishedUnseen) holdPhase("answer ready");
    state.requestId = null;
    if (!raw.trim() && !cardHost.children.length) bot.remove();
    setSkill(null); // a skill applies to one message, not the whole thread
    els.input.focus();
  }
}

/* -------------------------------------------------------- conversations -- */

/** How long ago, in words short enough for a list row. `ts` is epoch seconds. */
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

async function refreshList() {
  try {
    const res = await fetch("/api/conversations");
    const { conversations } = await res.json();
    els.list.innerHTML =
      conversations
        .map(
          (c) => `
      <li class="convo">
        <button class="convo-open" type="button" data-id="${c.id}" ${
          c.id === state.conversationId ? 'aria-current="true"' : ""
        }>
          <span class="t" dir="auto">${escapeHtml(c.title)}</span>
          <span class="m">${c.n} ${c.n === 1 ? "message" : "messages"} · ${ago(c.updated_at)}</span>
        </button>
        <button class="icon-btn convo-del" type="button" data-del="${c.id}"
                aria-label="Delete conversation: ${escapeHtml(c.title)}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </button>
      </li>`
        )
        .join("") || '<li class="convo-empty muted">No conversations yet.</li>';
  } catch {
    /* the rail is a convenience; a failed refresh is not worth a toast */
  }
}

els.list.addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) {
    const id = del.dataset.del;
    const title = del.closest(".convo")?.querySelector(".t")?.textContent || "";
    try {
      const res = await fetch(`/api/conversations/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      announce(`Deleted conversation ${title}`);
    } catch (err) {
      toast(`Could not delete that conversation — ${err.message}`, "err");
      return;
    }
    if (state.conversationId === id) newChat();
    refreshList();
    return;
  }
  const item = e.target.closest(".convo-open");
  if (item) loadConversation(item.dataset.id);
});

async function loadConversation(cid) {
  if (state.streaming) return;
  try {
    const res = await fetch(`/api/conversations/${cid}`);
    if (!res.ok) throw new Error("Conversation not found");
    const convo = await res.json();
    state.conversationId = cid;
    state.persona = convo.persona || state.persona;
    paintPersonas();
    els.thread.innerHTML = "";

    let ctx = 0;
    for (const m of convo.messages) {
      if (m.role === "user") {
        addUser(m.content);
      } else {
        const bot = addAssistant();
        bot.dataset.raw = m.content;
        // Replay stored tool cards, so reloading a conversation is not lossy.
        const cardHost = bot.querySelector(".cards");
        (m.tool_calls || []).forEach((call, i) => {
          const meta = { label: TOOL_LABELS[call.tool] || call.tool, ms: call.ms };
          if (call.ok && call.card) mountCard(cardHost, call.card, call.data, i, meta);
          else mountCard(cardHost, "error", { message: call.summary }, i, meta);
        });
        bot.querySelector(".prose").innerHTML = renderMarkdown(m.content);
        if (m.gen_tps != null || m.ttft_ms != null) footer(bot, m);
        ctx = (m.prompt_tokens || 0) + (m.completion_tokens || 0) || ctx;
      }
    }
    setContext(ctx);
    setRail(false);
    setView("thread");
    els.wrap.scrollTop = els.wrap.scrollHeight;
    refreshList();
  } catch (err) {
    toast(err.message, "err");
  }
}

function newChat() {
  if (state.streaming) return;
  state.conversationId = null;
  els.thread.innerHTML = "";
  setView("empty");
  setSkill(null);
  setContext(0);
  setRail(false);
  refreshList();
  els.input.focus();
}
$("#new-chat").addEventListener("click", newChat);
// Fetched as the drawer opens, so the times are current and a conversation
// started in another tab is listed. The rows are held unseen for the moment
// that takes (the drawer is still sliding in) and then arrive as a list, once:
// showing the old rows, replacing them and animating the new ones would be the
// same list entering twice.
$("#menu-btn").addEventListener("click", async () => {
  els.list.classList.remove("stagger");
  els.list.classList.add("pending");
  await Promise.race([refreshList(), new Promise((done) => setTimeout(done, 300))]);
  els.list.classList.remove("pending");
  if (REDUCED) return;
  els.list.classList.add("stagger");
  setTimeout(() => els.list.classList.remove("stagger"), 700);
});

/* ------------------------------------------------------------- startup -- */

onHealth((info) => {
  const ready = info.status === "ready";
  els.send.disabled = !ready;
  if (!state.skill) els.input.placeholder = placeholderFor(info);
});

setContext(0);
els.send.disabled = window.AURORA.info.status !== "ready";
els.input.focus();
