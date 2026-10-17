/* A small, dependency-free markdown renderer.
 *
 * Written rather than vendored on purpose: a 1.5B model emits a narrow slice
 * of markdown (paragraphs, lists, fences, emphasis, the occasional table), and
 * this handles that slice in ~150 lines with no third-party blob to audit.
 * Everything is HTML-escaped before any tag is produced, so model output can
 * never inject markup.
 */

import { FILE_MARK, foldFiles } from "./files";

const ESC: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** The line `foldFiles` (./files) folds a file into, drawn as a chip that
 *  opens the file in the project panel. */
function fileChip(path: string, lines: number, writing: boolean): string {
  const count = `${lines} line${lines === 1 ? "" : "s"}`;
  return (
    `<button type="button" class="file-chip" data-file="${escapeHtml(path)}"` +
    ` data-state="${writing ? "writing" : "done"}">` +
    `<span class="file-chip-name">${escapeHtml(path)}</span>` +
    `<span class="file-chip-meta num">${writing ? `writing · ${count}` : count}</span></button>`
  );
}

export function escapeHtml(s: unknown): string {
  return String(s).replace(/[&<>"']/g, (c) => ESC[c] ?? c);
}

// A code span is lifted out of the text while the other inline rules run and
// put back afterwards. What stands in for it is its number between two NUL
// characters, which escaped text cannot otherwise be made to say.
const HOLD = "\u0000";
// biome-ignore lint/suspicious/noControlCharactersInRegex: the placeholder is NUL on purpose
const HELD = /\u0000(\d+)\u0000/g;

function inline(src: string): string {
  let text = escapeHtml(src);

  // Protect code spans from every other inline rule.
  const spans: string[] = [];
  text = text.replace(/`([^`\n]+)`/g, (_, code: string) => {
    spans.push(code);
    return `${HOLD}${spans.length - 1}${HOLD}`;
  });

  text = text
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    )
    .replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, "<strong>$2</strong>")
    .replace(/(^|[\s(])[*_](?=\S)([^*_\n]*?\S)[*_](?=[\s).,!?:;]|$)/g, "$1<em>$2</em>")
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");

  return text.replace(HELD, (_, i: string) => `<code>${spans[+i]}</code>`);
}

function codeBlock(lang: string | undefined, body: string): string {
  const label = lang ? escapeHtml(lang) : "text";
  return (
    `<div class="codeblock" data-code="${escapeHtml(body)}">` +
    `<header><span>${label}</span>` +
    `<button class="copy-btn" type="button" data-copy>Copy</button></header>` +
    `<pre><code>${escapeHtml(body)}</code></pre></div>`
  );
}

function tableBlock(rows: string[]): string {
  const cells = (line: string) =>
    line
      .replace(/^\s*\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map((c) => c.trim());
  const head = cells(rows[0] ?? "");
  const body = rows.slice(2).map(cells);
  let html = "<table><thead><tr>";
  for (const h of head) html += `<th>${inline(h)}</th>`;
  html += "</tr></thead><tbody>";
  for (const r of body) {
    html += "<tr>";
    head.forEach((_, i) => {
      html += `<td>${inline(r[i] ?? "")}</td>`;
    });
    html += "</tr>";
  }
  return `${html}</tbody></table>`;
}

/**
 * Split markdown into top-level blocks at blank lines, without splitting
 * inside a fenced code block.
 *
 * This exists so a streaming answer can re-render only its last block instead
 * of rebuilding the whole subtree on every tick. Measured on a 1200-token
 * answer, the whole-subtree rewrite costs ~8.9ms per tick and grows with
 * length; only the tail actually changes.
 */
export function splitBlocks(src: string | null | undefined): string[] {
  const lines = String(src ?? "")
    .replace(/\r\n?/g, "\n")
    .split("\n");
  const blocks: string[] = [];
  let current: string[] = [];
  let fenced = false;

  for (const line of lines) {
    if (/^\s*```/.test(line)) fenced = !fenced;
    if (!fenced && !line.trim()) {
      if (current.length) {
        blocks.push(current.join("\n"));
        current = [];
      }
      continue;
    }
    current.push(line);
  }
  if (current.length) blocks.push(current.join("\n"));
  return blocks;
}

/** An answer as the chat shows it: markdown, with each file it writes as a
 *  chip. The text copied from it stays the answer as it was written. */
export function renderAnswer(src: string | null | undefined): string {
  return renderMarkdown(foldFiles(String(src ?? "")));
}

export function renderMarkdown(src: string | null | undefined): string {
  const lines = String(src ?? "")
    .replace(/\r\n?/g, "\n")
    .split("\n");
  let out = "";
  let i = 0;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      out += `<p>${inline(para.join("\n")).replace(/\n/g, "<br>")}</p>`;
      para = [];
    }
  };

  while (i < lines.length) {
    const line = lines[i] as string;

    // a file of a project, folded into one line
    if (line.startsWith(FILE_MARK)) {
      const [, path, count, state] = line.split(FILE_MARK);
      if (path && count !== undefined) {
        flushPara();
        out += fileChip(path, Number(count) || 0, state === "writing");
        i++;
        continue;
      }
    }

    // fenced code — also renders while still streaming (unterminated fence)
    const fence = line.match(/^\s*```+\s*([\w+-]*)\s*$/);
    if (fence) {
      flushPara();
      const lang = fence[1];
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```+\s*$/.test(lines[i] as string)) {
        buf.push(lines[i++] as string);
      }
      i++; // closing fence (or EOF mid-stream)
      out += codeBlock(lang, buf.join("\n"));
      continue;
    }

    if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) {
      flushPara();
      out += "<hr>";
      i++;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushPara();
      const lvl = Math.min((heading[1] as string).length + 1, 4); // h1 belongs to the page
      out += `<h${lvl}>${inline(heading[2] as string)}</h${lvl}>`;
      i++;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      flushPara();
      const buf: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i] as string)) {
        buf.push((lines[i] as string).replace(/^\s*>\s?/, ""));
        i++;
      }
      out += `<blockquote>${renderMarkdown(buf.join("\n"))}</blockquote>`;
      continue;
    }

    // table: header row + separator row
    if (/\|/.test(line) && /^\s*\|?[\s:-]*-[-\s:|]*$/.test(lines[i + 1] || "")) {
      flushPara();
      const buf: string[] = [];
      while (i < lines.length && /\|/.test(lines[i] as string)) buf.push(lines[i++] as string);
      out += tableBlock(buf);
      continue;
    }

    const bullet = line.match(/^\s*([-*+]|\d+[.)])\s+/);
    if (bullet) {
      flushPara();
      const ordered = /\d/.test(bullet[1] as string);
      const items: string[] = [];
      while (i < lines.length) {
        const current = lines[i] as string;
        const m = current.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
        if (!m) {
          // continuation line of the current item
          if (items.length && /^\s{2,}\S/.test(current)) {
            items[items.length - 1] += `\n${current.trim()}`;
            i++;
            continue;
          }
          break;
        }
        items.push(m[2] as string);
        i++;
      }
      const tag = ordered ? "ol" : "ul";
      out += `<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${tag}>`;
      continue;
    }

    if (!line.trim()) {
      flushPara();
      i++;
      continue;
    }

    para.push(line);
    i++;
  }

  flushPara();
  return out;
}
