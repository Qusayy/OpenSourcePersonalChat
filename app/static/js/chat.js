/* Chat page controller. */

import { $, $$, toast, setRail, onHealth, plate } from "./app.js";
import { renderMarkdown, splitBlocks, escapeHtml } from "./md.js";
import { sseFetch } from "./stream.js";
import { Meter, sparkPaths, fmt, fmtInt, fmtMs } from "./metrics.js";
import { mountCard } from "./cards.js";
import { mountSkills, setupPalette, getSkill } from "./skills.js";
import { enter, REDUCED } from "./motion.js";

/** Fade a transient element out before removing it. */
function dismiss(el) {
  if (!el) return;
  if (REDUCED) return el.remove();
  el.dataset.leaving = "true";
  setTimeout(() => el.remove(), 160);
}
import { icon, hasIcon } from "./icons.js";

const RING_C = 2 * Math.PI * 22;

const state = {
  conversationId: null,
  persona: localStorage.getItem("aurora.persona") || window.AURORA.defaultPersona,
  skill: null,
  streaming: false,
  requestId: null,
};

const els = {
  thread: $("#thread"),
  hero: $("#hero"),
  wrap: $("#stream-wrap"),
  form: $("#composer"),
  input: $("#input"),
  send: $("#send"),
  list: $("#convo-list"),
  ctxHint: $("#ctx-hint"),
  tps: $("#tps-value"),
  ttft: $("#ttft-value"),
  out: $("#out-value"),
  ctxValue: $("#ctx-value"),
  ctxRing: $("#ctx-ring"),
  sparkLine: $("#spark-line"),
  sparkFill: $("#spark-fill"),
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
}
$$(".persona").forEach((b) =>
  b.addEventListener("click", () => {
    state.persona = b.dataset.persona;
    localStorage.setItem("aurora.persona", state.persona);
    paintPersonas();
  })
);
paintPersonas();

/* --------------------------------------------------------------- skills -- */

function setSkill(skill) {
  state.skill = skill;
  if (!els.skillChip) return;
  if (!skill) {
    els.skillChip.hidden = true;
    els.input.placeholder = `Ask ${window.AURORA.info.model} something…`;
    return;
  }
  els.skillChip.hidden = false;
  els.skillChip.innerHTML = `
    <span class="g">${escapeHtml(skill.glyph)}</span>
    <span>${escapeHtml(skill.name)}</span>
    <button class="x" type="button" aria-label="Clear skill">✕</button>`;
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

function hideHero() {
  const hero = els.hero;
  if (!hero || hero.hidden || hero.dataset.leaving === "true") return;
  hero.dataset.leaving = "true";
  const done = () => {
    hero.hidden = true;
    delete hero.dataset.leaving;
  };
  if (REDUCED) done();
  else setTimeout(done, 200);
}

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  // dir="auto" so Arabic or Hebrew input lays out right-to-left on its own.
  el.innerHTML = `<div class="body" dir="auto">${escapeHtml(text).replace(/\n/g, "<br>")}</div>`;
  els.thread.appendChild(el);
  els.wrap.scrollTop = els.wrap.scrollHeight;
  return el;
}

function addAssistant() {
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `
    <div class="avatar" aria-hidden="true">✦</div>
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
function proseRenderer(host) {
  const stable = document.createElement("div");
  const tail = document.createElement("div");
  const caret = document.createElement("span");
  caret.className = "caret";
  host.append(stable, tail);
  let settled = 0; // blocks already committed to `stable`

  return {
    update(raw, caretOn) {
      const blocks = splitBlocks(raw);
      // Commit every block that is now finished, appending rather than
      // rebuilding so existing nodes are left alone.
      while (settled < blocks.length - 1) {
        stable.insertAdjacentHTML("beforeend", renderMarkdown(blocks[settled]));
        settled++;
      }
      const last = blocks[blocks.length - 1];
      tail.innerHTML = last === undefined ? "" : renderMarkdown(last);
      if (caretOn) {
        // Inside the last rendered block, not after it. Appended to the tail
        // container it lands below a finished <p> as a lone block of signal
        // ink on its own line, which reads as a rendering fault rather than as
        // a write head sitting after the newest word.
        (tail.lastElementChild || tail).appendChild(caret);
      } else {
        caret.remove();
      }
    },
    finish(raw) {
      // One clean pass at the end so the finished answer is a single tree.
      caret.remove();
      stable.innerHTML = "";
      tail.innerHTML = renderMarkdown(raw);
      settled = 0;
    },
  };
}

/** Create or update one row of the answer timeline. */
function upsertStep(host, data) {
  host.hidden = false;
  let row = host.querySelector(`[data-id="${CSS.escape(data.id)}"]`);
  if (!row) {
    row = document.createElement("div");
    row.className = "tl-step";
    row.dataset.id = data.id;
    row.innerHTML =
      '<span class="g"></span><span class="label"></span>' +
      '<span class="detail"></span><span class="ms"></span>';
    host.appendChild(row);
    enter(row, { distance: 8 });
  }
  row.dataset.status = data.status || "running";
  const art = data.tool && hasIcon(data.tool) ? icon(data.tool, 13) : icon(data.kind, 13);
  row.querySelector(".g").innerHTML = art || escapeHtml(data.glyph || "◇");
  row.querySelector(".label").textContent = data.label || "";
  row.querySelector(".detail").textContent = data.detail || "";
  row.querySelector(".ms").textContent = data.ms ? fmtMs(data.ms) : "";
  return row;
}

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
  const el = notice("err", `<span>${escapeHtml(message)}</span>`);
  announce(message);
  if (retryText) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "notice-retry";
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
  if (has(m.gen_tps)) bits.push(`${fmt(m.gen_tps, 1)} tok/s`);
  if (has(m.ttft_ms)) bits.push(`${fmtMs(m.ttft_ms)} to first token`);
  if (has(m.completion_tokens)) bits.push(`${fmtInt(m.completion_tokens)} tok`);
  if (m.tool_calls) bits.push(`${m.tool_calls} tool${m.tool_calls > 1 ? "s" : ""}`);
  if (m.cancelled) bits.push("stopped");
  foot.innerHTML =
    bits.map((b) => `<span>${b}</span>`).join('<span class="sep">·</span>') +
    `<span class="act">
       <button class="icon-btn" data-act="copy" title="Copy answer" aria-label="Copy answer">
         <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
       </button>
       <button class="icon-btn" data-act="again" title="Ask this again" aria-label="Ask this again">
         <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/></svg>
       </button>
     </span>`;
  foot.hidden = false;
}

els.thread.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const msg = btn.closest(".msg");
  if (btn.dataset.act === "copy") {
    navigator.clipboard.writeText(msg.dataset.raw || "").then(
      () => toast("Answer copied"),
      () => toast("Clipboard blocked by the browser", "err")
    );
  } else if (btn.dataset.act === "again") {
    if (state.streaming) return;
    const prev = msg.previousElementSibling;
    const q = prev?.classList.contains("user") ? prev.querySelector(".body").innerText : null;
    if (q) send(q);
  }
});

/* ----------------------------------------------------------------- HUD --- */

const meter = new Meter({
  onSample: ({ rate, total, history }) => {
    els.tps.dataset.empty = "false";
    els.tps.textContent = fmt(rate, 1);
    els.out.textContent = fmtInt(total);
    const [line, fill] = sparkPaths(history);
    els.sparkLine.setAttribute("d", line);
    els.sparkFill.setAttribute("d", fill);
    const strip = document.querySelector('[data-strip="tps"]');
    if (strip) strip.textContent = fmt(rate, 1);
    // The same reading the HUD shows, plotted on the plate behind the page.
    plate?.sample(rate);
  },
});

function setContext(tokens) {
  const max = window.AURORA.info.n_ctx || 4096;
  const pct = Math.max(0, Math.min(1, tokens / max));
  els.ctxValue.textContent = fmtInt(tokens);
  els.ctxRing.setAttribute("stroke-dashoffset", String(RING_C * (1 - pct)));
  els.ctxHint.textContent = `${fmtInt(tokens)} / ${fmtInt(max)} ctx`;
  const strip = document.querySelector('[data-strip="ctx"]');
  if (strip) strip.textContent = fmtInt(tokens);
}

/* ------------------------------------------------------------ streaming -- */

function setMode(streaming) {
  state.streaming = streaming;
  els.send.dataset.mode = streaming ? "stop" : "send";
  els.send.setAttribute("aria-label", streaming ? "Stop generating" : "Send message");
  document.documentElement.dataset.pass = streaming ? "open" : "closed";
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
  hideHero();
  setRail(false);
  addUser(text);

  const skill = state.skill;
  const bot = addAssistant();
  const prose = proseRenderer(bot.querySelector(".prose"));
  const timeline = bot.querySelector(".timeline");
  const cardHost = bot.querySelector(".cards");
  let queueCard = null;
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
  els.tps.textContent = "0.0";
  els.ttft.textContent = "—";
  els.out.textContent = "0";

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
        } else if (event === "step") {
          upsertStep(timeline, data);
          if (data.status === "failed") {
            announce(`${data.label} failed: ${data.detail || "unknown error"}`);
          }
        } else if (event === "card") {
          mountCard(cardHost, data.card, data.data, cardIndex++);
          if (nearBottom()) els.wrap.scrollTop = els.wrap.scrollHeight;
        } else if (event === "trimmed") {
          notice("", `<span>${
            data.dropped > 1
              ? `The ${data.dropped} oldest messages were dropped to fit the context window.`
              : "The oldest message was dropped to fit the context window."
          }</span>`);
        } else if (event === "start") {
          state.requestId = data.request_id;
          // The contact window opens here, not at submit: the plate's x axis is
          // time since generation began, so folding queue wait into it would
          // make every axis reading a lie about the model's speed.
          plate?.open();
          dismiss(queueCard);
          queueCard = null;
          setContext(data.prompt_tokens);
        } else if (event === "token") {
          // No layout reads here — the tick below batches one read and one
          // write per frame instead of a pair per token.
          // Acquisition of signal: prefill is over, the model is talking.
          if (!raw) plate?.acquire();
          raw += data.t;
          dirty = true;
          meter.token(1);
        } else if (event === "done") {
          caretOn = false;
          dirty = false;
          plate?.close(); // loss of signal — the band closes on the real window
          prose.finish(raw);
          bot.dataset.raw = raw;
          footer(bot, data);
          els.ttft.textContent = fmtMs(data.ttft_ms);
          els.out.textContent = fmtInt(data.completion_tokens);
          setContext((data.prompt_tokens || 0) + (data.completion_tokens || 0));
          if (data.gen_tps) els.tps.textContent = fmt(data.gen_tps, 1);
          const strip = document.querySelector('[data-strip="ttft"]');
          if (strip) strip.textContent = Math.round(data.ttft_ms || 0);
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
          plate?.fail();
          prose.finish(raw);
          errorNotice(data.message || "The model could not answer that.", text);
        }
      },
    });
  } catch (err) {
    caretOn = false;
    dirty = false;
    plate?.fail();
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
    plate?.close(); // a cancel ends the pass too; a no-op if `done` already did
    if (raw) prose.finish(raw);
    dismiss(queueCard);
    meter.stop();
    setMode(false);
    state.requestId = null;
    if (!raw.trim() && !cardHost.children.length) bot.remove();
    setSkill(null); // a skill applies to one message, not the whole thread
    els.input.focus();
  }
}

/* -------------------------------------------------------- conversations -- */

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
          <span class="m">${c.n} messages</span>
        </button>
        <button class="convo-del" type="button" data-del="${c.id}"
                aria-label="Delete conversation: ${escapeHtml(c.title)}">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>
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
    hideHero();
    els.thread.innerHTML = "";
    // The plate shows the pass that is happening, not one that happened in a
    // conversation you just switched away from.
    plate?.reset();

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
          if (call.ok && call.card) mountCard(cardHost, call.card, call.data, i);
          else mountCard(cardHost, "error", { message: call.summary }, i);
        });
        bot.querySelector(".prose").innerHTML = renderMarkdown(m.content);
        if (m.gen_tps != null || m.ttft_ms != null) {
          footer(bot, { ...m, tool_calls: (m.tool_calls || []).length });
        }
        ctx = (m.prompt_tokens || 0) + (m.completion_tokens || 0) || ctx;
      }
    }
    setContext(ctx);
    setRail(false);
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
  if (els.hero) els.hero.hidden = false;
  setSkill(null);
  setContext(0);
  els.tps.dataset.empty = "true";
  els.tps.textContent = "—";
  els.ttft.textContent = "—";
  els.out.textContent = "—";
  els.sparkLine.setAttribute("d", "");
  els.sparkFill.setAttribute("d", "");
  plate?.reset(); // clean plate: prediction only, nothing measured yet
  setRail(false);
  refreshList();
  els.input.focus();
}
$("#new-chat").addEventListener("click", newChat);

/* ------------------------------------------------------------- startup -- */

onHealth((info) => {
  const ready = info.status === "ready";
  els.send.disabled = !ready;
  if (!state.skill) {
    // No tools hint here: at 390px the two-clause placeholder wrapped to a
    // second line the one-row textarea then clipped. The hint lives in the
    // composer foot, which has room for it at every width.
    els.input.placeholder = ready
      ? `Ask ${info.model} something…`
      : info.status === "error"
      ? "The model could not be loaded — nothing can be sent right now"
      : "The model is still loading…";
  }
});

setContext(0);
els.send.disabled = window.AURORA.info.status !== "ready";
els.input.focus();
