/* The streaming answer.
 *
 * Framework-free on purpose. While an answer is being written its markup is
 * rebuilt ten times a second, and the fade on its newest words is carried
 * across each rebuild by hand; that only works if nothing else writes to the
 * same nodes. The page gives `proseRenderer` an empty element and leaves
 * everything inside it alone.
 */

import { reduced } from "./frame";
import { renderMarkdown, splitBlocks } from "./markdown";

/**
 * Where the caret goes: inside the last block being written, at the end of its
 * text. Appended after that block instead, an inline caret following a <p>
 * starts a line of its own and sits under the sentence it is meant to end.
 */
const CARET_INSIDE = /^(P|UL|OL|LI|H[1-6]|BLOCKQUOTE|DIV|PRE|CODE|TABLE|TBODY|TR|TD)$/;
export function caretHost(root: Element): Element {
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
export const INK_MS = 280; // matches the `ink-in` keyframes in the stylesheet

/** A stretch of rendered text that is still fading up: offsets into the text
 *  of the element it is in, and when it arrived on the `performance.now()` clock. */
export interface InkRange {
  start: number;
  end: number;
  at: number;
}

export function inkIn(root: Node, ranges: readonly InkRange[], now: number): void {
  if (!ranges.length) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node as Text);
  let offset = 0;
  for (const node of nodes) {
    const value = node.nodeValue ?? "";
    const start = offset;
    const end = start + value.length;
    offset = end;
    // White space between blocks is counted but never wrapped: a span there
    // would be a stray child of a list or a table row.
    if (!value.trim()) continue;
    // Latest stretch first. Wrapping splits the text node and leaves its
    // beginning in place, so the offsets of earlier stretches stay true.
    for (let i = ranges.length - 1; i >= 0; i--) {
      const stretch = ranges[i] as InkRange;
      const from = Math.max(stretch.start, start);
      const to = Math.min(stretch.end, end);
      if (from >= to) continue;
      const range = document.createRange();
      range.setStart(node, from - start);
      range.setEnd(node, to - start);
      const span = document.createElement("span");
      span.className = "fresh";
      span.style.animationDelay = `${-Math.round(now - stretch.at)}ms`;
      range.surroundContents(span);
    }
  }
}

/** Take the wrappers out again, leaving the text exactly where it was. */
export function inkDry(root: Element): void {
  const spans = root.querySelectorAll(".fresh");
  if (!spans.length) return;
  for (const span of spans) span.replaceWith(...span.childNodes);
  root.normalize();
}

export interface ProseRenderer {
  /** Paint the answer as it stands. Called on a timer while tokens arrive. */
  update(raw: string, caretOn: boolean): void;
  /** The answer has ended: one clean render of the whole of it. Safe to call
   *  again with the same text, which changes nothing. */
  finish(raw: string): void;
  /** Cancel the tidying-up `finish` left pending. For when the element is
   *  being thrown away; it changes nothing that is on screen. */
  dispose(): void;
  /** Take everything off: the answer so far is being written again. */
  reset(): void;
}

/**
 * Incremental renderer for a streaming answer.
 *
 * Completed blocks are appended once and never touched again; only the block
 * still being written is re-rendered each tick. Rewriting the whole answer
 * measured 8.9ms per tick at 1200 tokens and rose with length — the cost was
 * DOM teardown and relayout, not the markdown parse (0.55ms of it).
 */
export function proseRenderer(host: Element): ProseRenderer {
  const stable = document.createElement("div");
  const tail = document.createElement("div");
  const caret = document.createElement("span");
  caret.className = "caret";
  host.append(stable, tail);
  let settled = 0; // blocks already committed to `stable`
  let written = 0; // rendered length of the block being written, at the last tick
  let fresh: InkRange[] = []; // stretches of that block still fading up
  let finished: string | null = null; // the text the finished answer was rendered from
  const pending = new Set<ReturnType<typeof setTimeout>>(); // wrappers waiting to come out

  /** Note what is new in a block `length` long, and return what is still fading. */
  const mark = (length: number, now: number): InkRange[] => {
    fresh = fresh.filter((r) => now - r.at < INK_MS);
    // Shorter than it was: a closing mark arrived and markdown re-read what
    // was there ("**bold" became bold). The offsets no longer mean what they
    // did, so nothing is wrapped this time.
    if (length < written) fresh = [];
    else if (length > written) fresh.push({ start: written, end: length, at: now });
    written = length;
    return fresh;
  };

  const length = (el: Element): number => (el.textContent ?? "").length;

  return {
    update(raw, caretOn) {
      finished = null;
      const now = performance.now();
      const blocks = splitBlocks(raw);
      // Commit every block that is now finished, appending rather than
      // rebuilding so existing nodes are left alone.
      while (settled < blocks.length - 1) {
        const html = renderMarkdown(blocks[settled]);
        if (reduced()) {
          stable.insertAdjacentHTML("beforeend", html);
        } else {
          // Its last words may still be fading up, and it will not be
          // rendered again: wrap them once, here, on the way in.
          const done = document.createElement("div");
          done.innerHTML = html;
          inkIn(done, mark(length(done), now), now);
          stable.append(...done.childNodes);
          written = 0;
          fresh = [];
        }
        settled++;
      }
      const last = blocks[blocks.length - 1];
      tail.innerHTML = last === undefined ? "" : renderMarkdown(last);
      if (!reduced()) inkIn(tail, mark(length(tail), now), now);
      if (caretOn) caretHost(tail).appendChild(caret);
      else caret.remove();
    },

    finish(raw) {
      caret.remove();
      // Called when the answer ends and again as the request is torn down. The
      // second call has nothing new to render, and rendering anyway would cut
      // short the fade the first one left running.
      if (finished === raw) return;
      finished = raw;
      const now = performance.now();
      // What was on screen at the last tick, as offsets into the whole answer,
      // and which of it was still fading up.
      const before = length(stable);
      const shown = before + written;
      const drying = fresh
        .filter((r) => now - r.at < INK_MS)
        .map((r) => ({ start: before + r.start, end: before + r.end, at: r.at }));
      // One clean pass at the end so the finished answer is a single tree.
      stable.innerHTML = "";
      tail.innerHTML = renderMarkdown(raw);
      settled = 0;
      written = 0;
      fresh = [];
      if (reduced()) return;
      // The last words. Those that arrived since the last tick fade up like
      // any others, and those already fading carry on from where they were,
      // instead of the clean render snapping all of them to full.
      const whole = length(tail);
      if (whole < shown) return; // the whole-answer render read it differently; leave it be
      if (whole > shown) drying.push({ start: shown, end: whole, at: now });
      if (!drying.length) return;
      inkIn(tail, drying, now);
      // Then the wrappers come out, so the finished answer is clean markup.
      const timer = setTimeout(() => {
        pending.delete(timer);
        inkDry(tail);
      }, INK_MS + 80);
      pending.add(timer);
    },

    dispose() {
      for (const timer of pending) clearTimeout(timer);
      pending.clear();
    },

    reset() {
      this.dispose();
      stable.innerHTML = "";
      tail.innerHTML = "";
      caret.remove();
      settled = 0;
      written = 0;
      fresh = [];
      finished = null;
    },
  };
}
