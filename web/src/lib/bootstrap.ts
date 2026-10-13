/* The first values the page shows.
 *
 * The server writes them into index.html as one JSON element, so they are
 * there before any request: the model's name, the machine as last measured,
 * the personas and tools on offer. Under the Vite dev server nothing wrote
 * them, and the page asks /api/bootstrap for the same thing before it draws.
 */

import { api } from "./api";
import type { Bootstrap } from "./types";

let loaded: Bootstrap | null = null;

/** What the server wrote into the page, or null if it wrote nothing. */
export function readEmbedded(doc: Document = document): Bootstrap | null {
  const text = doc.getElementById("bootstrap")?.textContent;
  if (!text) return null;
  try {
    const value: unknown = JSON.parse(text);
    return value && typeof value === "object" ? (value as Bootstrap) : null;
  } catch {
    return null;
  }
}

/** Resolve the bootstrap once, before the first render. */
export async function loadBootstrap(): Promise<Bootstrap> {
  loaded = readEmbedded() ?? (await api.bootstrap());
  return loaded;
}

/** The bootstrap. Only valid after `loadBootstrap()` has resolved, which
 *  main.tsx awaits before it renders anything. */
export function bootstrap(): Bootstrap {
  if (!loaded) throw new Error("bootstrap() was read before loadBootstrap() resolved");
  return loaded;
}

/** For tests: stand in for the server. */
export function setBootstrap(value: Bootstrap | null): void {
  loaded = value;
}
