/* Asking the server whether a chain of stars is the pattern.
 *
 * The cover does not know the pattern and cannot find it out: it sends the
 * chain, and is told yes or no. No is said the same way whatever the reason,
 * after the same wait, and with a 200, so a wrong drawing leaves nothing in
 * the console and tells the visitor nothing, not even that it was asked.
 *
 * Yes comes with a session cookie. The cover then asks for the address it is
 * already at, and this time the server sends what is behind it.
 */

import { MIN_STARS } from "./pattern";

/** True if the server let the chain in. Any failure at all is a no. */
export async function tryChain(chain: readonly number[]): Promise<boolean> {
  if (chain.length < MIN_STARS) return false;
  try {
    const res = await fetch("/api/unlock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chain }),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!res.ok) return false;
    const answer = (await res.json()) as { ok?: unknown } | null;
    return answer?.ok === true;
  } catch {
    return false;
  }
}

/** How long the sky takes to give way, in step with the stylesheet. */
const GIVE_WAY = 700;

/**
 * Let the sky go and bring in what is behind it. The page is asked for again
 * at the same address, by a reload, which keeps where the visitor came from:
 * the application greets an arrival from outside, and this is one.
 */
export function open(): void {
  document.documentElement.dataset.opening = "";
  window.setTimeout(() => window.location.reload(), GIVE_WAY);
}
