/* Setting the pattern.
 *
 * The cover's sky, with a panel over it that asks for a pattern, asks for it
 * again, and prints the line the server is to be given. The pattern is drawn
 * here exactly as it will be drawn on the cover, on the same stars.
 *
 * Nothing drawn on this page is sent anywhere or written down anywhere. The
 * line it ends with is a hash made in this browser; it is shown on the screen
 * and put on the clipboard if asked, and that is all.
 */

import "../cover/cover.css";
import "./setup.css";
import { mountDraw } from "../cover/draw";
import { mountAnySky } from "../cover/flat";
import { makeVerifier, strength } from "./verifier";

const need = <T extends HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`setup.html has no #${id}`);
  return el as T;
};

const say = need("say");
const line = need("line");
const then = need("then");
const copy = need<HTMLButtonElement>("copy");
const again = need<HTMLButtonElement>("again");
const canvas = need<HTMLCanvasElement>("sky");

const START =
  "Join stars in the sky above, by dragging from one to the next or clicking each in turn.";

/** The pattern drawn the first time, once it has been accepted. */
let first: readonly number[] | null = null;
/** True once the line is on screen: the sky is then only a sky. */
let done = false;

const same = (a: readonly number[], b: readonly number[]): boolean =>
  a.length === b.length && a.every((star, i) => star === b[i]);

function reset(): void {
  first = null;
  done = false;
  line.hidden = then.hidden = copy.hidden = true;
  line.textContent = "";
  copy.textContent = "Copy the line";
  say.textContent = START;
}

async function drawn(chain: readonly number[]): Promise<void> {
  if (done) return;
  if (!first) {
    const found = strength(chain);
    if (!found.enough) {
      say.textContent = `${found.advice} Draw another.`;
      return;
    }
    first = chain;
    say.textContent =
      `${found.stars} stars, and ${found.leaps} of its steps cross the sky. ` +
      "Draw it once more, the same way, to be sure of it.";
    return;
  }
  if (!same(chain, first)) {
    say.textContent =
      "That was not the same pattern. Draw the first one again, or start over with another.";
    return;
  }
  say.textContent = "Working it out.";
  const verifier = await makeVerifier(first);
  done = true;
  say.textContent = "That is the pattern. This is the line the server keeps for it:";
  line.textContent = `AURORA_GATE_VERIFIER=${verifier}`;
  line.hidden = then.hidden = copy.hidden = false;
}

again.addEventListener("click", reset);
copy.addEventListener("click", () => {
  void navigator.clipboard.writeText(line.textContent ?? "").then(() => {
    copy.textContent = "Copied";
  });
});

if (!globalThis.crypto?.subtle) {
  // A page served from another machine over plain HTTP: the browser will not
  // hash for it.
  say.textContent = `Open this page on the machine that is serving it, at http://localhost:${location.port}/setup.html. A browser only makes the hash for a page it trusts.`;
  again.hidden = true;
} else {
  reset();
  const mounted = mountAnySky(canvas);
  if (mounted) {
    mountDraw(mounted.canvas, mounted.sky, {
      onChain: (chain) => void drawn(chain),
    });
  } else {
    say.textContent = "This browser has nothing to draw the sky with. Use another.";
    again.hidden = true;
  }
}
