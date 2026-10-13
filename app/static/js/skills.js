/* Skill gallery and the "/" command palette. */

import { escapeHtml } from "./md.js";
import { icon, hasIcon } from "./icons.js";
import { REDUCED, EASE } from "./motion.js";

// Every row gets a drawn icon. A tool or skill the set has no picture for
// takes the generic one rather than a Unicode glyph, which is tofu on half
// the machines that will ever load this.
const glyphFor = (name, size = 15) => icon(hasIcon(name) ? name : "tool", size);

const SKILLS = window.AURORA.skills || [];
const TOOLS = window.AURORA.tools || [];

/* -------------------------------------------------------------- gallery -- */

/** The tools a skill runs through, in order, as plain words. */
function pipeGraph(pipeline) {
  return pipeline
    .map((node) => escapeHtml(node === "model" ? "answer" : node.replace(/_/g, " ")))
    .join(icon("arrow", 12));
}

export function mountSkills(host, onSelect) {
  if (!host) return;
  if (!SKILLS.length) {
    // The heading goes with the list it introduces.
    (host.closest(".skills") || host).remove();
    return;
  }
  host.innerHTML = SKILLS.map(
    (s) => `
    <li>
      <button class="skill" type="button" data-skill="${escapeHtml(s.id)}">
        <span class="name">${escapeHtml(s.name)}</span>
        <span class="blurb">${escapeHtml(s.blurb)}</span>
        <span class="pipe">${pipeGraph(s.pipeline)}</span>
      </button>
    </li>`
  ).join("");

  host.querySelectorAll(".skill").forEach((btn) =>
    btn.addEventListener("click", () => {
      const skill = SKILLS.find((s) => s.id === btn.dataset.skill);
      if (skill) onSelect(skill);
    })
  );
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
export function setupPalette(input, host, { onSkill }) {
  let rows = [];
  let index = 0;

  const close = () => {
    host.hidden = true;
    rows = [];
    index = 0;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  };

  const build = (query) => {
    const q = query.toLowerCase();
    const tools = TOOLS.filter(
      (t) => t.name.includes(q) || (t.example || "").toLowerCase().includes(q)
    ).map((t) => ({
      kind: "tool",
      id: t.name,
      cmd: t.example || `/${t.name}`,
      desc: t.description,
    }));
    const skills = SKILLS.filter(
      (s) => s.id.includes(q) || s.name.toLowerCase().includes(q)
    ).map((s) => ({
      kind: "skill",
      id: s.id,
      cmd: s.name,
      desc: s.blurb,
    }));
    // On the empty sheet the skills are already listed, as rows, directly
    // under the input this palette opens over. Listing them again would put
    // the same six names on screen twice, so there it offers the tools.
    const emptySheet = document.querySelector("#main")?.dataset.view === "empty";
    return emptySheet ? tools : [...skills, ...tools];
  };

  // The list holds commands; what the highlighted one does is written out in
  // full beneath it. Each option also carries its description for a screen
  // reader, which reads options, not the panel under them.
  const paint = () => {
    let html = "";
    let lastKind = null;
    rows.forEach((row, i) => {
      if (row.kind !== lastKind) {
        html += `<div class="group">${row.kind === "skill" ? "Skills" : "Tools"}</div>`;
        lastKind = row.kind;
      }
      html += `
        <div class="row" role="option" id="pal-opt-${i}" data-i="${i}"
             aria-selected="${i === index}">
          <span class="g">${glyphFor(row.id, 14)}</span>
          <span class="cmd${row.kind === "tool" ? " num" : ""}">${escapeHtml(row.cmd)}</span>
          <span class="sr-only">. ${escapeHtml(row.desc)}</span>
        </div>`;
    });
    const wasHidden = host.hidden;
    host.innerHTML = `<div class="pal-list">${html}</div><div class="pal-desc" aria-hidden="true"></div>`;
    host.hidden = rows.length === 0;
    // The panel arrives; its rows do not. They are rebuilt on every keystroke,
    // and a list that re-entered each time a letter was typed would flicker.
    if (wasHidden && !host.hidden && !REDUCED && host.animate) {
      const from = getComputedStyle(host).bottom === "auto" ? "-6px" : "6px";
      host.animate(
        [
          { opacity: 0, translate: `0 ${from}` },
          { opacity: 1, translate: "0 0" },
        ],
        { duration: 160, easing: EASE }
      );
    }
    const desc = host.querySelector(".pal-desc");
    const describe = (i) => {
      desc.textContent = rows[i]?.desc || "";
    };
    describe(index);
    // A listbox needs its options announced through the input that owns it.
    input.setAttribute("aria-expanded", String(!host.hidden));
    if (host.hidden) input.removeAttribute("aria-activedescendant");
    else input.setAttribute("aria-activedescendant", `pal-opt-${index}`);
    host.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    host.querySelectorAll(".row").forEach((btn) => {
      btn.addEventListener("mousedown", (e) => {
        e.preventDefault(); // keep focus in the textarea
        choose(Number(btn.dataset.i));
      });
      // Pointing at a command describes it; leaving goes back to the one the
      // keyboard has selected.
      btn.addEventListener("mouseenter", () => describe(Number(btn.dataset.i)));
      btn.addEventListener("mouseleave", () => describe(index));
    });
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

  return { close };
}
