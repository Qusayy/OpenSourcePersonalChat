/* Chat page controller. */

import { $, $$, toast, setRail, onHealth, canvas } from "./app.js";
import { renderMarkdown, escapeHtml } from "./md.js";
import { sseFetch } from "./stream.js";
import { Meter, sparkPaths, fmt, fmtInt, fmtMs } from "./metrics.js";
import { mountCard } from "./cards.js";
import { mountSkills, setupPalette, getSkill } from "./skills.js";
import { enter } from "./motion.js";
import { icon, hasIcon } from "./icons.js";

const ARROW = "M5 12h13M12 5l7 7-7 7";
const SQUARE = "M8 8h8v8H8z";
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
  glyph: $("#send-glyph"),
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
};

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
els.input.addEventListener("input", autosize);
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
  if (els.hero && !els.hero.hidden) els.hero.hidden = true;
}

function addUser(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="body">${escapeHtml(text).replace(/\n/g, "<br>")}</div>`;
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
      <div class="prose"></div>
      <div class="msg-foot" hidden></div>
    </div>`;
  els.thread.appendChild(el);
  els.wrap.scrollTop = els.wrap.scrollHeight;
  return el;
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

function footer(el, m) {
  const foot = el.querySelector(".msg-foot");
  const bits = [];
  if (m.gen_tps) bits.push(`${fmt(m.gen_tps, 1)} tok/s`);
  if (m.ttft_ms) bits.push(`${fmtMs(m.ttft_ms)} ttft`);
  if (m.completion_tokens) bits.push(`${fmtInt(m.completion_tokens)} tok`);
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
      () => toast("Clipboard blocked", "err")
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
  els.glyph.setAttribute("d", streaming ? SQUARE : ARROW);
  canvas?.setGenerating(streaming);
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
  const prose = bot.querySelector(".prose");
  const timeline = bot.querySelector(".timeline");
  const cardHost = bot.querySelector(".cards");
  let queueCard = null;
  let raw = "";
  let dirty = false;
  let caretOn = true;
  let cardIndex = 0;

  const paint = () => {
    if (!dirty) return;
    dirty = false;
    prose.innerHTML = renderMarkdown(raw) + (caretOn ? '<span class="caret"></span>' : "");
  };
  // Re-parsing markdown per token is the classic way to make streaming feel
  // slower than it is. 100ms is well under the eye's threshold.
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
          queueCard.lastElementChild.textContent =
            data.position > 0
              ? `${data.position} request${data.position > 1 ? "s" : ""} ahead — one generation runs at a time on 2 cores`
              : "Next in line — starting shortly";
        } else if (event === "step") {
          upsertStep(timeline, data);
        } else if (event === "card") {
          mountCard(cardHost, data.card, data.data, cardIndex++);
          if (nearBottom()) els.wrap.scrollTop = els.wrap.scrollHeight;
        } else if (event === "trimmed") {
          notice("", `<span>${data.dropped} earlier message${
            data.dropped > 1 ? "s" : ""
          } trimmed to fit the context window</span>`);
        } else if (event === "start") {
          state.requestId = data.request_id;
          queueCard?.remove();
          queueCard = null;
          setContext(data.prompt_tokens);
        } else if (event === "token") {
          const was = nearBottom();
          raw += data.t;
          dirty = true;
          meter.token(1);
          canvas?.pulse();
          if (was) els.wrap.scrollTop = els.wrap.scrollHeight;
        } else if (event === "done") {
          caretOn = false;
          dirty = true;
          paint();
          bot.dataset.raw = raw;
          footer(bot, data);
          els.ttft.textContent = fmtMs(data.ttft_ms);
          els.out.textContent = fmtInt(data.completion_tokens);
          setContext((data.prompt_tokens || 0) + (data.completion_tokens || 0));
          if (data.gen_tps) els.tps.textContent = fmt(data.gen_tps, 1);
          const strip = document.querySelector('[data-strip="ttft"]');
          if (strip) strip.textContent = Math.round(data.ttft_ms || 0);
          refreshList();
        } else if (event === "error") {
          caretOn = false;
          paint();
          notice("err", `<span>${escapeHtml(data.message || "Generation failed")}</span>`);
        }
      },
    });
  } catch (err) {
    caretOn = false;
    dirty = true;
    paint();
    if (err.name !== "AbortError") {
      notice("err", `<span>${escapeHtml(err.message)}</span>`);
    }
  } finally {
    clearInterval(painter);
    caretOn = false;
    paint();
    queueCard?.remove();
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
      <button class="convo" data-id="${c.id}" ${
        c.id === state.conversationId ? 'aria-current="true"' : ""
      }>
        <span class="t">${escapeHtml(c.title)}</span>
        <span class="m">${c.n} messages</span>
        <span class="x" role="button" tabindex="0" aria-label="Delete conversation" data-del="${c.id}">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </span>
      </button>`
        )
        .join("") ||
      '<p class="muted" style="padding:6px 10px;font-size:var(--t-sm)">Nothing yet.</p>';
  } catch {
    /* the rail is a convenience; a failed refresh is not worth a toast */
  }
}

els.list.addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) {
    e.stopPropagation();
    const id = del.dataset.del;
    await fetch(`/api/conversations/${id}`, { method: "DELETE" });
    if (state.conversationId === id) newChat();
    refreshList();
    return;
  }
  const item = e.target.closest(".convo");
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
        if (m.gen_tps || m.ttft_ms) {
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
    els.input.placeholder = ready
      ? `Ask ${info.model} something…  (press / for tools)`
      : info.status === "error"
      ? "Model unavailable — see the About page"
      : "Warming the model…";
  }
});

setContext(0);
els.send.disabled = window.AURORA.info.status !== "ready";
els.input.focus();
