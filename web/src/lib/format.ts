/* Formatters.
 *
 * Every place the interface turns a number or a time into text, gathered so
 * the same figure reads the same wherever it is printed. All pure: a value in,
 * a string out.
 */

type Maybe<T> = T | null | undefined;

/** A figure to a fixed number of places; a dash when there is none. */
export function fmt(n: Maybe<number>, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toFixed(digits);
}

export function fmtInt(n: Maybe<number>): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  // A fixed locale: these sit beside figures the server formatted, and a
  // visitor's own grouping would put "4.096" next to "4,096" on one line.
  return Math.round(n).toLocaleString("en-US");
}

export function fmtMs(ms: Maybe<number>): string {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`;
}

/** Megabytes as gigabytes, to one place: the status line's memory figures. */
export const gb = (mb: number): string => (mb / 1024).toFixed(1);

/** A CPU count. A cgroup quota can make it fractional; a whole one has no ".0". */
export const cpuText = (n: number): string => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/** The noun that goes with a CPU count. */
export const coreNoun = (n: number): string => (n === 1 ? "core" : "cores");

/** A share of a hundred, rounded, with its sign: "37%". */
export const pct = (v: number): string => `${Math.round(v)}%`;

/** Megabytes as gigabytes to two places, for the figure that sits beside the
 *  memory estimate, which is printed to two places. */
export const gbExact = (mb: number): string => (mb / 1024).toFixed(2);

/** The three load averages, each to two places. */
export const loadText = (load: readonly number[]): string =>
  load.map((v) => v.toFixed(2)).join("  ");

/** A card's figure: grouped in a fixed locale, to exactly `digits` places. */
export const num = (v: unknown, digits = 0): string =>
  v === null || v === undefined || Number.isNaN(Number(v))
    ? "—"
    : Number(v).toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });

// An offline tool finishes inside the timer's resolution. That is "under a
// millisecond", not "0ms": nothing takes no time.
export const took = (ms: number): string => (ms < 1 ? "<1ms" : fmtMs(ms));

/** How long ago, in words short enough for a list row. `ts` is epoch seconds. */
export function ago(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** The day of the week, short, in the visitor's own language. */
export const weekdayShort = (date: string | number | Date): string =>
  new Date(date).toLocaleDateString(undefined, { weekday: "short" });
