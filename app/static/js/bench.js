/* Benchmark page: run the suite over SSE, then draw what came back.
 *
 * Charts are hand-rolled — a charting library would be a bigger download than
 * the entire rest of this front-end, on a server whose whole premise is that it
 * fits in 4 GB.
 */

import { $, toast } from "./app.js";
import { escapeHtml } from "./md.js";
import { sseFetch } from "./stream.js";
import { fmt, fmtInt, fmtMs } from "./metrics.js";

const REFERENCE = JSON.parse($("#reference-data").textContent);

const els = {
  run: $("#run-btn"),
  progress: $("#progress"),
  status: $("#run-status"),
  gen: $("#chart-gen"),
  ttft: $("#chart-ttft"),
  ref: $("#chart-ref"),
  refMax: $("#ref-max"),
  history: $("#history-chart"),
  historyEmpty: $("#history-empty"),
  table: $("#case-table").querySelector("tbody"),
};

let latest = null;

/* ------------------------------------------------------------- rendering */

function tiles(s) {
  $("#t-gen").textContent = s ? fmt(s.gen_tps, 1) : "—";
  $("#t-prefill").textContent = s ? fmt(s.prefill_tps, 0) : "—";
  const short = s?.cases?.find((c) => c.id === "short") || s?.cases?.[0];
  $("#t-ttft").textContent = short ? fmtMs(short.ttft_p50) : "—";
  $("#t-rss").textContent = s?.rss_mb ? `${fmtInt(s.rss_mb)} MB` : "—";
}

function barChart(host, rows, { max, unit, digits = 1, alt = false }) {
  if (!rows.length) {
    host.innerHTML = '<div class="empty">Run the suite to fill this in.</div>';
    return;
  }
  const top = max || Math.max(...rows.map((r) => r.value)) * 1.12;
  host.innerHTML = rows
    .map(
      (r) => `
      <div class="bar-row">
        <span class="lab">${escapeHtml(r.label)}${
        r.sub ? `<small>${escapeHtml(r.sub)}</small>` : ""
      }</span>
        <span class="track" title="${escapeHtml(r.label)}: ${fmt(r.value, digits)} ${unit}">
          <i class="${alt ? "alt" : ""}" style="width:${Math.max(1.5, (100 * r.value) / top).toFixed(2)}%"></i>
        </span>
        <span class="val">${fmt(r.value, digits)}<small> ${unit}</small>${
        r.extra ? `<small> · ${escapeHtml(r.extra)}</small>` : ""
      }</span>
      </div>`
    )
    .join("");
}

function referenceChart(measured) {
  // Reference rows are ranges ("6–12"); this machine gets its measured value
  // marked on top of the range it is expected to fall in.
  const parse = (s) => {
    const m = String(s).match(/(\d+(?:\.\d+)?)\s*[–-]?\s*(\d+(?:\.\d+)?)?/);
    if (!m) return [0, 0];
    const lo = parseFloat(m[1]);
    return [lo, m[2] ? parseFloat(m[2]) : lo * 1.25];
  };

  const rows = REFERENCE.map((r) => ({ ...r, range: parse(r.gen) }));
  const max = Math.max(...rows.map((r) => r.range[1]), measured || 0) * 1.08;
  els.refMax.textContent = Math.round(max);

  els.ref.innerHTML = rows
    .map((r) => {
      const [lo, hi] = r.range;
      const left = (100 * lo) / max;
      const width = Math.max(1, (100 * (hi - lo)) / max);
      const dot =
        r.self && measured
          ? `<b style="left:${((100 * measured) / max).toFixed(2)}%" title="measured here: ${fmt(
              measured,
              1
            )} tok/s"></b>`
          : "";
      return `
      <div class="bar-row">
        <span class="lab">${escapeHtml(r.hw)}<small>${r.threads} threads</small></span>
        <span class="strip ${r.self ? "self" : ""}">
          <i style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"
             title="${escapeHtml(r.gen)} tok/s"></i>${dot}
        </span>
        <span class="val">${escapeHtml(r.gen)}${
        r.self && measured ? `<small> · here ${fmt(measured, 1)}</small>` : ""
      }</span>
      </div>`;
    })
    .join("");
}

function historyChart(runs) {
  const pts = runs
    .filter((r) => r.gen_tps)
    .slice()
    .reverse()
    .slice(-60);

  if (pts.length < 2) {
    els.history.style.display = "none";
    els.historyEmpty.hidden = false;
    return;
  }
  els.history.style.display = "block";
  els.historyEmpty.hidden = true;

  const W = 720;
  const H = 190;
  const pad = { l: 42, r: 16, t: 14, b: 26 };
  const max = Math.max(...pts.map((p) => p.gen_tps)) * 1.15;
  const x = (i) => pad.l + (i * (W - pad.l - pad.r)) / (pts.length - 1);
  const y = (v) => H - pad.b - (v / max) * (H - pad.t - pad.b);

  const ticks = 4;
  let svg = "";
  for (let t = 0; t <= ticks; t++) {
    const v = (max / ticks) * t;
    svg +=
      `<line class="grid" x1="${pad.l}" x2="${W - pad.r}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}"/>` +
      `<text class="lbl" x="${pad.l - 8}" y="${(y(v) + 3).toFixed(1)}" text-anchor="end">${v.toFixed(0)}</text>`;
  }

  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.gen_tps).toFixed(1)}`).join(" ");
  svg += `<path class="area" d="${line} L${x(pts.length - 1).toFixed(1)} ${H - pad.b} L${pad.l} ${H - pad.b} Z"/>`;
  svg += `<path class="series" d="${line}"/>`;

  pts.forEach((p, i) => {
    const when = new Date(p.created_at * 1000).toLocaleString();
    svg +=
      `<circle class="pt" cx="${x(i).toFixed(1)}" cy="${y(p.gen_tps).toFixed(1)}" r="3">` +
      `<title>${escapeHtml(p.label || "run")} — ${fmt(p.gen_tps, 2)} tok/s · ${escapeHtml(when)}</title></circle>`;
  });

  // Direct-label the newest point only; a number on every point is noise.
  const last = pts[pts.length - 1];
  svg +=
    `<text class="lbl" x="${(x(pts.length - 1) - 6).toFixed(1)}" y="${(y(last.gen_tps) - 9).toFixed(1)}" ` +
    `text-anchor="end" style="fill:var(--ink-1)">${fmt(last.gen_tps, 1)} tok/s</text>`;
  svg += `<text class="lbl" x="${pad.l}" y="${H - 6}">oldest</text>`;
  svg += `<text class="lbl" x="${W - pad.r}" y="${H - 6}" text-anchor="end">newest</text>`;

  els.history.innerHTML =
    `<defs><linearGradient id="histfill" x1="0" y1="0" x2="0" y2="1">` +
    `<stop offset="0" stop-color="#6366f1" stop-opacity=".26"/>` +
    `<stop offset="1" stop-color="#6366f1" stop-opacity="0"/></linearGradient></defs>` +
    svg;
}

function caseTable(summary) {
  if (!summary?.cases?.length) return;
  els.table.innerHTML = summary.cases
    .map(
      (c) => `
      <tr>
        <td>${escapeHtml(c.label)}</td>
        <td>${fmtInt(c.prompt_tokens)}</td>
        <td>${fmtInt(c.gen_tokens)}</td>
        <td>${c.reps}</td>
        <td>${fmt(c.prefill_tps, 1)}</td>
        <td>${fmt(c.gen_tps, 2)}</td>
        <td>${fmtMs(c.ttft_p50)}</td>
        <td>${fmtMs(c.ttft_p95)}</td>
      </tr>`
    )
    .join("");
}

function paint(summary, runs) {
  latest = summary;
  tiles(summary);
  caseTable(summary);
  referenceChart(summary?.gen_tps);

  barChart(
    els.gen,
    (summary?.cases || []).map((c) => ({
      label: c.label,
      sub: `${fmtInt(c.prompt_tokens)} prompt tokens`,
      value: c.gen_tps,
      extra: `${fmt(c.gen_tps_min, 1)}–${fmt(c.gen_tps_max, 1)}`,
    })),
    { unit: "tok/s", digits: 2 }
  );

  barChart(
    els.ttft,
    (summary?.cases || []).flatMap((c) => [
      { label: c.label, sub: "p50", value: c.ttft_p50 },
      { label: "", sub: "p95", value: c.ttft_p95 },
    ]),
    { unit: "ms", digits: 0, alt: false }
  );

  // The p95 rows read as the lighter of each pair.
  [...els.ttft.querySelectorAll(".bar-row")].forEach((row, i) => {
    if (i % 2 === 1) row.querySelector(".track i")?.classList.add("alt");
  });

  historyChart(runs || []);
}

/* --------------------------------------------------------------- loading */

async function load() {
  try {
    const res = await fetch("/api/bench/results");
    const data = await res.json();
    paint(data.latest, data.history);
  } catch {
    toast("Could not load benchmark results", "err");
  }
}

/* ------------------------------------------------------------- running -- */

let running = false;

els.run.addEventListener("click", async () => {
  if (running) return;
  running = true;
  els.run.disabled = true;
  els.progress.style.width = "0%";
  els.status.textContent = "Starting…";

  const live = [];
  try {
    await sseFetch("/api/bench/run", {
      onEvent: (event, data) => {
        if (event === "stage") {
          els.status.textContent = data.text;
          if (data.pct != null) els.progress.style.width = `${data.pct}%`;
        } else if (event === "rep") {
          live.push(data);
          els.progress.style.width = `${data.pct}%`;
          els.status.textContent = `${data.case} run ${data.rep}: ${fmt(
            data.gen_tps,
            2
          )} tok/s · ${fmtMs(data.ttft_ms)} to first token`;
        } else if (event === "case") {
          els.status.textContent = `${data.label} done — median ${fmt(data.gen_tps, 2)} tok/s`;
        } else if (event === "summary") {
          els.progress.style.width = "100%";
          els.status.textContent = `Finished in ${fmt(data.elapsed_s, 0)}s — median ${fmt(
            data.gen_tps,
            2
          )} tok/s`;
          load();
        } else if (event === "error") {
          els.status.textContent = data.message;
          toast(data.message, "err");
        }
      },
    });
  } catch (err) {
    els.status.textContent = err.message;
    toast(err.message, "err");
  } finally {
    running = false;
    els.run.disabled = false;
  }
});

load();
