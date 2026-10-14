/* The line of text under the sky: today's date and the moon's phase.
 *
 * Both are worked out here, from the clock, with no request. The phase is the
 * mean one: the moon's age since a known new moon, as a share of the mean
 * month. The true moon runs up to half a day ahead of or behind that, which
 * matters to an almanac and not to a caption.
 */

/** A new moon: 6 January 2000, 18:14 UTC. */
const EPOCH = Date.UTC(2000, 0, 6, 18, 14);
const MONTH = 29.530588853 * 86_400_000;

const PHASES = [
  [0.033, "new moon"],
  [0.217, "waxing crescent"],
  [0.283, "first quarter"],
  [0.467, "waxing gibbous"],
  [0.533, "full moon"],
  [0.717, "waning gibbous"],
  [0.783, "last quarter"],
  [0.967, "waning crescent"],
] as const;

/** The moon's age as a share of its month, 0 at new and 0.5 at full, and its name. */
export function moonPhase(date: Date): { share: number; name: string } {
  const since = date.getTime() - EPOCH;
  const share = (((since % MONTH) + MONTH) % MONTH) / MONTH;
  for (const [limit, name] of PHASES) {
    if (share < limit) return { share, name };
  }
  return { share, name: "new moon" };
}

/** Write the line into `el`, and keep it true past midnight. Returns the way to stop. */
export function mountReadout(el: HTMLElement): () => void {
  const day = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long" });
  const write = (): void => {
    const now = new Date();
    const parts = day.formatToParts(now);
    el.textContent = "";
    for (const part of parts) {
      if (part.type === "day") {
        // Figures are set in the typeface's fixed-width cut, as everywhere else.
        const figure = document.createElement("span");
        figure.className = "num";
        figure.textContent = part.value;
        el.append(figure);
      } else el.append(part.value);
    }
    el.append(` · ${moonPhase(now).name}`);
  };
  write();
  const timer = setInterval(write, 10 * 60_000);
  return () => clearInterval(timer);
}
