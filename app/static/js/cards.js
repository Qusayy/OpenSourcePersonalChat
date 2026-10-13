/* Visual answers.
 *
 * A tool returns structured data; this turns it into something worth looking
 * at. Every glyph here is drawn inline as SVG — nothing is fetched from a
 * provider's CDN, so the page keeps working with no outbound requests from the
 * browser at all.
 */

import { escapeHtml } from "./md.js";
import { icon } from "./icons.js";
import { enter } from "./motion.js";
import { sparkPaths, fmtMs } from "./metrics.js";

const el = (html) => {
  const wrap = document.createElement("div");
  wrap.innerHTML = html.trim();
  return wrap.firstElementChild;
};

const num = (v, digits = 0) =>
  v === null || v === undefined || Number.isNaN(Number(v))
    ? "—"
    : Number(v).toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });

/* ------------------------------------------------------------ weather art */

const WEATHER_ART = {
  sun: `<circle cx="24" cy="24" r="9" class="w-sun"/>
        <g class="w-rays">${Array.from({ length: 8 }, (_, i) => {
          const a = (i * Math.PI) / 4;
          const x1 = 24 + Math.cos(a) * 13, y1 = 24 + Math.sin(a) * 13;
          const x2 = 24 + Math.cos(a) * 18, y2 = 24 + Math.sin(a) * 18;
          return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`;
        }).join("")}</g>`,
  "cloud-sun": `<circle cx="18" cy="18" r="7" class="w-sun"/>
        <path class="w-cloud" d="M16 34h16a6 6 0 0 0 0-12 8 8 0 0 0-15-2 5 5 0 0 0-1 14z"/>`,
  cloud: `<path class="w-cloud" d="M14 34h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z"/>`,
  rain: `<path class="w-cloud" d="M14 28h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z"/>
        <g class="w-drop"><line x1="17" y1="32" x2="15" y2="39"/><line x1="24" y1="32" x2="22" y2="41"/><line x1="31" y1="32" x2="29" y2="39"/></g>`,
  drizzle: `<path class="w-cloud" d="M14 28h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z"/>
        <g class="w-drop"><line x1="18" y1="32" x2="17" y2="36"/><line x1="25" y1="32" x2="24" y2="36"/><line x1="32" y1="32" x2="31" y2="36"/></g>`,
  snow: `<path class="w-cloud" d="M14 28h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z"/>
        <g class="w-drop"><circle cx="17" cy="35" r="1.6"/><circle cx="25" cy="38" r="1.6"/><circle cx="32" cy="35" r="1.6"/></g>`,
  sleet: `<path class="w-cloud" d="M14 28h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z"/>
        <g class="w-drop"><line x1="18" y1="32" x2="16" y2="38"/><circle cx="27" cy="37" r="1.6"/></g>`,
  storm: `<path class="w-cloud" d="M14 26h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z"/>
        <path class="w-bolt" d="M25 28l-6 9h5l-2 8 8-11h-5l3-6z"/>`,
  fog: `<path class="w-cloud" d="M14 24h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z"/>
        <g class="w-drop"><line x1="12" y1="30" x2="36" y2="30"/><line x1="15" y1="35" x2="33" y2="35"/><line x1="12" y1="40" x2="30" y2="40"/></g>`,
};

// Parameter deliberately not called `icon` — that name is the imported SVG
// helper, and shadowing it here would be a quiet trap for the next edit.
const weatherArt = (condition) =>
  `<svg class="w-art" viewBox="0 0 48 48" aria-hidden="true">${
    WEATHER_ART[condition] || WEATHER_ART.cloud
  }</svg>`;

/* ----------------------------------------------------------------- cards */

/**
 * The shell every card shares: a figure with a caption that names the tool,
 * gives its source or a qualifier where there is one ("exact", a place, a
 * domain), and says how long it took. This caption is the only place a
 * finished tool is named and timed.
 *
 * `meta` is what the tool's step reported: { label, ms }. It is absent when a
 * card is rendered with no step behind it.
 */
// An offline tool finishes inside the timer's resolution. That is "under a
// millisecond", not "0ms": nothing takes no time.
const took = (ms) => (ms < 1 ? "<1ms" : fmtMs(ms));

const figure = (kind, name, note, body, tool = kind, meta = null) =>
  el(`
    <figure class="card-tool card-${kind}">
      <figcaption class="tool-head">
        <span class="g">${icon(tool, 14)}</span>
        <span class="tool-name">${name}</span>
        ${note ? `<span class="tool-note">${note}</span>` : ""}
        ${
          meta && meta.ms !== null && meta.ms !== undefined
            ? `<span class="tool-ms num">${took(meta.ms)}</span>`
            : ""
        }
      </figcaption>
      ${body}
    </figure>`);

const RENDERERS = {
  calculator(d, meta) {
    // The result is printed exactly as the tool returned it. It used to count
    // up from zero, which showed several wrong numbers on the way to an
    // answer whose whole point is that it is exact.
    return figure(
      "calc",
      "Calculator",
      d.exact ? "exact" : "rounded",
      `<div class="tape">
         <div class="expr num">${escapeHtml(d.expression)}</div>
         <div class="result"><span class="eq num">=</span><span class="val num">${escapeHtml(
           String(d.result)
         )}</span></div>
         ${d.fraction ? `<div class="frac num">${escapeHtml(d.fraction)}</div>` : ""}
       </div>`,
      "calculator",
      meta
    );
  },

  weather(d, meta) {
    const hourly = (d.hourly || []).filter((v) => typeof v === "number");
    const [line, fill] = sparkPaths(hourly.length > 2 ? hourly : [0, 0], 120, 38, {
      autoMin: true,
    });
    const days = (d.days || [])
      .map(
        (day) => `
        <div class="day">
          <span class="dow">${new Date(day.date).toLocaleDateString(undefined, {
            weekday: "short",
          })}</span>
          ${weatherArt(day.icon)}
          <span class="hi num">${num(day.max)}°</span>
          <span class="lo num">${num(day.min)}°</span>
        </div>`
      )
      .join("");

    return figure(
      "weather",
      "Weather",
      `${escapeHtml(d.place || "")}${d.country ? ", " + escapeHtml(d.country) : ""}`,
      `<div class="now">
         ${weatherArt(d.icon)}
         <div class="temp"><span class="val num">${num(d.temperature, 1)}</span><span class="unit">°C</span></div>
         <div class="meta">
           <div class="cond">${escapeHtml(d.condition || "")}</div>
           <div class="sub">feels <span class="num">${num(d.feels_like, 1)}°</span> · wind <span class="num">${num(
        d.wind
      )} km/h</span> · humidity <span class="num">${num(d.humidity)}%</span></div>
         </div>
       </div>
       ${
         hourly.length > 2
           ? `<svg class="w-spark" viewBox="0 0 120 38" preserveAspectRatio="none" aria-hidden="true">
                <path class="w-area" d="${fill}"></path>
                <path class="w-line" d="${line}"></path>
              </svg>
              <div class="axis-mini"><span>now</span><span>next 24h</span></div>`
           : ""
       }
       <div class="days">${days}</div>`,
      "weather",
      meta
    );
  },

  wikipedia(d, meta) {
    return figure(
      "wiki",
      "Wikipedia",
      "",
      `<div class="title">${escapeHtml(d.title || "")}</div>
       <div class="desc">${escapeHtml(d.description || "")}</div>
       <p class="extract">${escapeHtml(d.extract || "")}</p>
       <a class="src" href="${escapeHtml(d.url || "#")}" target="_blank"
          rel="noopener noreferrer">en.wikipedia.org${icon("external", 12)}</a>`,
      "wikipedia",
      meta
    );
  },

  url(d, meta) {
    return figure(
      "url",
      "Page read",
      escapeHtml(d.domain || ""),
      `<div class="title">${escapeHtml(d.title || "")}</div>
       <p class="lede">${escapeHtml(d.lede || "")}</p>
       <div class="url-foot">
         <span><span class="num">${num(d.words)}</span> words · about <span class="num">${num(
        d.reading_minutes,
        1
      )}</span> min read${d.truncated ? " · truncated" : ""}</span>
         <a class="src" href="${escapeHtml(d.url || "#")}" target="_blank"
            rel="noopener noreferrer">Open the page${icon("external", 12)}</a>
       </div>`,
      "url",
      meta
    );
  },

  currency(d, meta) {
    return figure(
      "exchange",
      "Currency",
      `ECB ${escapeHtml(String(d.date || ""))}`,
      `<div class="exchange">
         <div class="side">
           <span class="amount num">${num(d.amount, 2)}</span>
           <span class="code">${escapeHtml(d.from || "")}</span>
         </div>
         <div class="arrow">${icon("arrow", 16)}<span class="rate num">×${num(d.rate, 4)}</span></div>
         <div class="side to">
           <span class="amount num">${num(d.result, 2)}</span>
           <span class="code">${escapeHtml(d.to || "")}</span>
         </div>
       </div>`,
      "currency",
      meta
    );
  },

  convert(d, meta) {
    return figure(
      "exchange",
      "Convert",
      escapeHtml(d.dimension || ""),
      `<div class="exchange">
         <div class="side">
           <span class="amount num">${num(d.value, 4).replace(/\.?0+$/, "")}</span>
           <span class="code">${escapeHtml(d.from || "")}</span>
         </div>
         <div class="arrow">${icon("arrow", 16)}</div>
         <div class="side to">
           <span class="amount num">${escapeHtml(d.result_text || "")}</span>
           <span class="code">${escapeHtml(d.to || "")}</span>
         </div>
       </div>`,
      "convert",
      meta
    );
  },

  clock(d, meta) {
    const [hh, mm] = String(d.time || "00:00").split(":").map(Number);
    const minuteAngle = mm * 6;
    const hourAngle = ((hh % 12) + mm / 60) * 30;
    const ticks = Array.from({ length: 12 }, (_, i) => {
      const a = (i * Math.PI) / 6;
      const x1 = 40 + Math.sin(a) * 30, y1 = 40 - Math.cos(a) * 30;
      const x2 = 40 + Math.sin(a) * 34, y2 = 40 - Math.cos(a) * 34;
      return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`;
    }).join("");
    const others = (d.others || [])
      .map((o) => `<div class="zrow"><span>${escapeHtml(o.zone.split("/").pop().replace(/_/g, " "))}</span><b class="num">${escapeHtml(o.time)}</b></div>`)
      .join("");

    return figure(
      "clock",
      "Clock",
      escapeHtml(d.zone || "UTC"),
      `<div class="clock-body">
         <svg class="face" viewBox="0 0 80 80" aria-hidden="true">
           <circle class="rim" cx="40" cy="40" r="36"/>
           <g class="ticks">${ticks}</g>
           <line class="hand hour" x1="40" y1="40" x2="40" y2="22"
                 transform="rotate(${hourAngle.toFixed(1)} 40 40)"/>
           <line class="hand minute" x1="40" y1="40" x2="40" y2="14"
                 transform="rotate(${minuteAngle.toFixed(1)} 40 40)"/>
           <circle class="pin" cx="40" cy="40" r="2.4"/>
         </svg>
         <div>
           <div class="big num">${escapeHtml(d.time || "")}</div>
           <div class="date">${escapeHtml(d.date || "")}</div>
           <div class="zones">${others}</div>
           ${d.note ? `<div class="note">${escapeHtml(d.note)}</div>` : ""}
         </div>
       </div>`,
      "clock",
      meta
    );
  },

  textstats(d, meta) {
    const max = Math.max(...(d.top_terms || []).map((t) => t.count), 1);
    const terms = (d.top_terms || [])
      .map(
        (t) => `
        <div class="term">
          <span class="t">${escapeHtml(t.term)}</span>
          <span class="bar"><i style="width:${((t.count / max) * 100).toFixed(1)}%"></i></span>
          <span class="c num">${t.count}</span>
        </div>`
      )
      .join("");
    return figure(
      "stats",
      "Text stats",
      "",
      `<div class="stat-row">
         <div><b class="num">${num(d.words)}</b><span>words</span></div>
         <div><b class="num">${num(d.sentences)}</b><span>sentences</span></div>
         <div><b class="num">${num(d.reading_minutes, 1)}</b><span>min read</span></div>
         <div><b class="num">${num(d.avg_sentence_words, 1)}</b><span>words per sentence</span></div>
       </div>
       <div class="terms">${terms}</div>`,
      "textstats",
      meta
    );
  },

  error(d, meta) {
    // Named for the tool that failed when that is known, so the line reads
    // "Weather failed" rather than leaving the reader to work out which.
    return figure(
      "fail",
      meta?.label ? `${escapeHtml(meta.label)} failed` : "Tool failed",
      "",
      `<p>${escapeHtml(d.message || "Something went wrong.")}</p>`,
      "error",
      meta
    );
  },
};

/** Build a card element, or null if the type is unknown. */
export function renderCard(type, data, meta = null) {
  const make = RENDERERS[type];
  if (!make) return null;
  try {
    const node = make(data || {}, meta);
    node.dataset.card = type;
    return node;
  } catch (err) {
    console.warn("[cards] failed to render", type, err);
    return RENDERERS.error({ message: `Could not render the ${type} card.` });
  }
}

/** Insert a card with the shared entrance motion. */
export function mountCard(host, type, data, index = 0, meta = null) {
  const node = renderCard(type, data, meta);
  if (!node) return null;
  host.appendChild(node);
  enter(node, { delay: index * 40 });
  return node;
}
