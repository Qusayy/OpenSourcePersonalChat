/* Turning a pattern into the line the server keeps, and saying whether a
 * pattern is worth keeping.
 *
 * The line is a slow hash of the pattern with a salt, in the form app/gate.py
 * reads: gate1.<iterations>.<salt>.<hash>, letters, digits and dots only. The
 * server never sees the pattern when it is set, only this; and whoever sees
 * this can work the pattern out by trying them all, so it goes in the
 * server's own .env and nowhere else.
 *
 * Nothing here touches the page. It needs the browser's own cryptography,
 * which exists only in a secure context: localhost is one, and that is where
 * the setup page is served.
 */

import { ANCHORS, canonical, MIN_STARS } from "../cover/pattern";

/** Enough that the hash is not instant, few enough that opening the site is.
 *  The server checks one of these each time a pattern is drawn, on two cores
 *  that have a language model on them. The count is not what keeps the
 *  pattern safe: there are too few patterns for any count to. The limit on
 *  guesses is. */
export const ITERATIONS = 300_000;

const hex = (bytes: Uint8Array): string =>
  Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");

/** The line for a pattern. The salt is sixteen bytes, random unless given. */
export async function makeVerifier(
  chain: readonly number[],
  salt: Uint8Array<ArrayBuffer> = crypto.getRandomValues(new Uint8Array(16)),
  iterations: number = ITERATIONS,
): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(canonical(chain)),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    key,
    256,
  );
  return `gate1.${iterations}.${hex(salt)}.${hex(new Uint8Array(bits))}`;
}

/** What makes a pattern hard to guess, counted. */
export interface Strength {
  stars: number;
  /** How many different stars it uses. */
  different: number;
  /** Steps that go past a star's nearest neighbours. */
  leaps: number;
  /** Whether the setup page will take it. */
  enough: boolean;
  /** If not, what to change, as a sentence. */
  advice: string;
}

/** A step to one of this many nearest stars is the obvious one to take. */
const NEIGHBOURS = 4;
const MIN_DIFFERENT = 6;
const MIN_LEAPS = 2;

function neighbours(of: number): Set<number> {
  const from = ANCHORS[of]!;
  return new Set(
    ANCHORS.map((a, i) => ({ i, d: Math.hypot(a.x - from.x, a.y - from.y) }))
      .filter(({ i }) => i !== of)
      .sort((a, b) => a.d - b.d)
      .slice(0, NEIGHBOURS)
      .map(({ i }) => i),
  );
}

/**
 * How a pattern stands. The server allows about a hundred wrong guesses a
 * day. Seven stars chosen freely would take thousands of years to find at
 * that rate; seven stars that each step to the star next door, under one.
 * So a pattern has to be long enough, use enough of the sky, and leap.
 */
export function strength(chain: readonly number[]): Strength {
  const stars = chain.length;
  const different = new Set(chain).size;
  let leaps = 0;
  for (let i = 1; i < chain.length; i++) {
    if (!neighbours(chain[i - 1]!).has(chain[i]!)) leaps++;
  }
  let advice = "";
  if (stars < MIN_STARS) advice = `Join at least ${MIN_STARS} stars. This has ${stars}.`;
  else if (different < MIN_DIFFERENT)
    advice = `Use at least ${MIN_DIFFERENT} different stars. This goes back and forth between ${different}.`;
  else if (leaps < MIN_LEAPS)
    advice =
      "Every step goes to a star next door, which is the first thing anyone would try. Cross the sky at least twice.";
  return { stars, different, leaps, enough: advice === "", advice };
}
