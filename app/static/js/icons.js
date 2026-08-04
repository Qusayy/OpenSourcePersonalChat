/* Inline SVG icon set.
 *
 * Unicode symbols like ⧉, ⇹ and ⚖ are missing from plenty of system font
 * stacks and render as tofu boxes — exactly the failure that makes a UI look
 * broken on someone else's machine. These are drawn instead, so they look the
 * same everywhere and inherit currentColor.
 */

const P = (d, extra = "") =>
  `<path d="${d}" fill="none" stroke="currentColor" stroke-width="1.8"
     stroke-linecap="round" stroke-linejoin="round" ${extra}/>`;

const SHAPES = {
  // tools
  calculator:
    P("M6 3h12a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z") +
    P("M8 7h8M8 11h2M12 11h2M16 11h0M8 15h2M12 15h2M16 15v4"),
  clock: P("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z") + P("M12 7v5l3 2"),
  convert: P("M4 8h13l-3-3M20 16H7l3 3"),
  currency: P("M4 9h13l-3-3M20 15H7l3 3") + P("M12 2v3M12 19v3", 'opacity=".5"'),
  textstats: P("M4 5h16M4 10h16M4 15h10M4 20h6"),
  wikipedia: P("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z") +
    P("M3 12h18M12 3c2.5 2.6 2.5 15 0 18-2.5-3-2.5-15.4 0-18z"),
  weather: P("M7 18h10a4 4 0 0 0 .2-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z"),
  url: P("M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1") +
    P("M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"),

  // skills
  summarise_link: P("M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7z") +
    P("M14 3v4h4M9 12h6M9 16h4"),
  fact_check: P("M12 4v16M6 8h12") + P("M6 8l-3 6h6zM18 8l-3 6h6z") +
    P("M9 20h6", 'opacity=".6"'),
  compare: P("M4 5h7v14H4zM13 5h7v14h-7z") + P("M11 12h2", 'opacity=".6"'),
  weather_brief: P("M7 17h10a4 4 0 0 0 .2-8A6 6 0 0 0 6 8a4.5 4.5 0 0 0 1 9z") +
    P("M9 21h2M14 21h2", 'opacity=".7"'),
  do_maths: P("M13 2 4 14h7l-1 8 9-12h-7z"),
  explain_code: P("M9 8l-4 4 4 4M15 8l4 4-4 4"),

  // pipeline / timeline
  route: P("M6 4v6a4 4 0 0 0 4 4h8") + P("M15 11l3 3-3 3"),
  model: P("M12 3l1.9 4.6L19 9.5l-5.1 1.9L12 16l-1.9-4.6L5 9.5l5.1-1.9z") +
    P("M18 16l.8 2 2.2.8-2.2.8-.8 2-.8-2-2.2-.8 2.2-.8z", 'opacity=".65"'),
  tool: P("M14.5 4a4.5 4.5 0 0 0-4 6.6L4 17v3h3l6.4-6.4A4.5 4.5 0 1 0 14.5 4z"),
  error: P("M12 8v5M12 16.5v.5") + P("M10.3 4.3 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z"),
  skill: P("M12 3l2.1 5.1L20 9l-4.4 4 1.2 5.9L12 16l-4.8 2.9L8.4 13 4 9l5.9-.9z"),
};

// Aliases so a card, a tool and a skill can all ask for the same picture.
const ALIAS = {
  convert_units: "convert",
  read_url: "url",
  text_stats: "textstats",
  exchange: "currency",
  answer: "model",
  calc: "calculator",
};

export function icon(name, size = 16) {
  const key = ALIAS[name] || name;
  const shape = SHAPES[key];
  if (!shape) return "";
  return `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24"
    aria-hidden="true" focusable="false">${shape}</svg>`;
}

export function hasIcon(name) {
  return Boolean(SHAPES[ALIAS[name] || name]);
}
