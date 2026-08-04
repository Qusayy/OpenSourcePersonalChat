/* Skill gallery and the "/" command palette. */

import { escapeHtml } from "./md.js";
import { stagger } from "./motion.js";
import { icon, hasIcon } from "./icons.js";

const glyphFor = (name, fallback, size = 15) =>
  hasIcon(name) ? icon(name, size) : escapeHtml(fallback || "◆");

const SKILLS = window.AURORA.skills || [];
const TOOLS = window.AURORA.tools || [];

/* -------------------------------------------------------------- gallery -- */

function pipeGraph(pipeline) {
  return pipeline
    .map((node, i) => {
      const label = node === "model" ? "answer" : node.replace(/_/g, " ");
      const link = i ? '<span class="link"></span>' : "";
      return `${link}<span class="node">${escapeHtml(label)}</span>`;
    })
    .join("");
}

export function mountSkills(host, onSelect) {
  if (!host) return;
  if (!SKILLS.length) {
    host.remove();
    return;
  }
  host.innerHTML = SKILLS.map(
    (s) => `
    <button class="skill" type="button" data-skill="${escapeHtml(s.id)}">
      <span class="top">
        <span class="g" aria-hidden="true">${glyphFor(s.id, s.glyph)}</span>
        <span class="name">${escapeHtml(s.name)}</span>
      </span>
      <span class="blurb">${escapeHtml(s.blurb)}</span>
      <span class="pipe">${pipeGraph(s.pipeline)}</span>
    </button>`
  ).join("");

  host.querySelectorAll(".skill").forEach((btn) =>
    btn.addEventListener("click", () => {
      const skill = SKILLS.find((s) => s.id === btn.dataset.skill);
      if (skill) onSelect(skill);
    })
  );
  stagger(host.querySelectorAll(".skill"), 40);
}

export function getSkill(id) {
  return SKILLS.find((s) => s.id === id) || null;
}

/* ------------------------------------------------------------- palette --- */

/**
 * The "/" palette. Typing a slash lists tools and skills; picking a tool
 * completes the command, picking a skill arms it. Either way the routing pass
 * is skipped entirely, which on 2 vCPU is worth several seconds.
 */
export function setupPalette(input, host, { onSkill, onSubmit }) {
  let rows = [];
  let index = 0;

  const close = () => {
    host.hidden = true;
    rows = [];
    index = 0;
  };

  const build = (query) => {
    const q = query.toLowerCase();
    const tools = TOOLS.filter(
      (t) => t.name.includes(q) || (t.example || "").toLowerCase().includes(q)
    ).map((t) => ({
      kind: "tool",
      id: t.name,
      glyph: t.glyph,
      cmd: t.example || `/${t.name}`,
      desc: t.description,
    }));
    const skills = SKILLS.filter(
      (s) => s.id.includes(q) || s.name.toLowerCase().includes(q)
    ).map((s) => ({
      kind: "skill",
      id: s.id,
      glyph: s.glyph,
      cmd: s.name,
      desc: s.blurb,
    }));
    return [...skills, ...tools];
  };

  const paint = () => {
    let html = "";
    let lastKind = null;
    rows.forEach((row, i) => {
      if (row.kind !== lastKind) {
        html += `<div class="group">${row.kind === "skill" ? "Skills" : "Tools"}</div>`;
        lastKind = row.kind;
      }
      html += `
        <button class="row" type="button" data-i="${i}" aria-selected="${i === index}">
          <span class="g">${glyphFor(row.id, row.glyph, 14)}</span>
          <span class="cmd">${escapeHtml(row.cmd)}</span>
          <span class="desc">${escapeHtml(row.desc)}</span>
        </button>`;
    });
    host.innerHTML = html;
    host.hidden = rows.length === 0;
    host.querySelectorAll(".row").forEach((btn) =>
      btn.addEventListener("mousedown", (e) => {
        e.preventDefault(); // keep focus in the textarea
        choose(Number(btn.dataset.i));
      })
    );
  };

  const choose = (i) => {
    const row = rows[i];
    if (!row) return;
    if (row.kind === "skill") {
      onSkill(getSkill(row.id));
      input.value = "";
    } else {
      // Leave the example's arguments selected so typing replaces them.
      const [cmd, ...rest] = row.cmd.split(" ");
      input.value = `${cmd} `;
      if (rest.length) {
        const start = input.value.length;
        input.value += rest.join(" ");
        input.setSelectionRange(start, input.value.length);
      }
    }
    close();
    input.focus();
    input.dispatchEvent(new Event("input"));
  };

  input.addEventListener("input", () => {
    const value = input.value;
    if (!value.startsWith("/") || value.includes("\n")) return close();
    const query = value.slice(1).split(" ")[0];
    if (value.slice(1).includes(" ")) return close(); // arguments being typed
    rows = build(query);
    index = 0;
    paint();
  });

  input.addEventListener("keydown", (e) => {
    if (host.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      index = (index + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
      paint();
    } else if (e.key === "Tab" || (e.key === "Enter" && rows.length)) {
      e.preventDefault();
      choose(index);
    } else if (e.key === "Escape") {
      close();
    }
  });

  input.addEventListener("blur", () => setTimeout(close, 120));

  return { close, submit: onSubmit };
}
