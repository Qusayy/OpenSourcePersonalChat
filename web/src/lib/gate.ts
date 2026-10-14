/* What the page does when the server stops recognising it.
 *
 * The site is behind a gate (app/gate.py): a browser with no session is sent
 * the cover in place of every page, and 401 in place of every API answer. A
 * session ends on its own after a while, or when the server is told to end
 * them all, and the first this page hears of it is a 401.
 *
 * There is nothing to show at that point that the server would not refuse to
 * fill in, so the page asks for itself again and is given the cover.
 */

const KEY = "aurora.relocked";
// A reload answered by another 401 within this long would be a loop.
const NOT_AGAIN_FOR = 10_000;

/** Reload, once, so the server can decide what this browser is shown. */
export function relock(): void {
  if (typeof window === "undefined") return;
  const now = Date.now();
  try {
    const last = Number(window.sessionStorage.getItem(KEY) ?? 0);
    if (now - last < NOT_AGAIN_FOR) return;
    window.sessionStorage.setItem(KEY, String(now));
  } catch {
    // No storage to keep the time in: reload anyway. The cover carries none
    // of this code, so it cannot ask again.
  }
  window.location.reload();
}
