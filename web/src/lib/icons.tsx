/* Inline SVG icon set.
 *
 * Unicode symbols like ⧉, ⇹ and ⚖ are missing from plenty of system font
 * stacks and render as tofu boxes — exactly the failure that makes a UI look
 * broken on someone else's machine. These are drawn instead, so they look the
 * same everywhere and inherit currentColor.
 *
 * Each picture is a list of stroked paths, kept as data so the two forms it is
 * needed in come from one place: `Icon`, a component, and `iconSvg`, the same
 * drawing as markup for the places that build HTML as a string.
 */

/** One stroke of a picture: its path, and its opacity where it is a quieter part. */
interface Stroke {
  d: string;
  opacity?: string;
}

const P = (d: string, opacity?: string): Stroke => (opacity ? { d, opacity } : { d });

const SHAPES = {
  // tools
  calculator: [
    P("M6 3h12a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"),
    P("M8 7h8M8 11h2M12 11h2M16 11h0M8 15h2M12 15h2M16 15v4"),
  ],
  clock: [P("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z"), P("M12 7v5l3 2")],
  convert: [P("M4 8h13l-3-3M20 16H7l3 3")],
  currency: [P("M4 9h13l-3-3M20 15H7l3 3"), P("M12 2v3M12 19v3", ".5")],
  textstats: [P("M4 5h16M4 10h16M4 15h10M4 20h6")],
  note: [P("M5 4h11l3 3v13H5z"), P("M9 10h6M9 14h6M9 18h3", ".6")],
  wikipedia: [
    P("M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z"),
    P("M3 12h18M12 3c2.5 2.6 2.5 15 0 18-2.5-3-2.5-15.4 0-18z"),
  ],
  weather: [P("M7 18h10a4 4 0 0 0 .2-8A6 6 0 0 0 6 9a4.5 4.5 0 0 0 1 9z")],
  url: [
    P("M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"),
    P("M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"),
  ],

  search: [P("M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15z"), P("M16 16l5 5")],
  file: [P("M6 3h8l4 4v14H6z"), P("M14 3v4h4", ".6")],
  python: [P("M8 7l-5 5 5 5M16 7l5 5-5 5"), P("M13.5 5l-3 14", ".6")],
  news: [
    P("M4 5h13v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"),
    P("M17 9h3v10a1 1 0 0 1-2 0"),
    P("M7 9h7M7 13h7M7 17h4", ".6"),
  ],

  // skills
  compare: [P("M4 5h7v14H4zM13 5h7v14h-7z"), P("M11 12h2", ".6")],

  // pipeline / timeline
  route: [P("M6 4v6a4 4 0 0 0 4 4h8"), P("M15 11l3 3-3 3")],
  // The model's step is drawn as what it produces: lines of text and the
  // caret still writing the last one.
  model: [P("M5 7h14M5 12h14M5 17h7"), P("M16 14.5v5")],

  // interface
  arrow: [P("M5 12h14M13 6l6 6-6 6")],
  external: [P("M9 6h9v9M18 6 7 17")],
  tool: [P("M14.5 4a4.5 4.5 0 0 0-4 6.6L4 17v3h3l6.4-6.4A4.5 4.5 0 1 0 14.5 4z")],
  error: [
    P("M12 8v5M12 16.5v.5"),
    P("M10.3 4.3 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z"),
  ],
  skill: [P("M12 3l2.1 5.1L20 9l-4.4 4 1.2 5.9L12 16l-4.8 2.9L8.4 13 4 9l5.9-.9z")],
} satisfies Record<string, Stroke[]>;

// Aliases so a card, a tool and a skill can all ask for the same picture.
const ALIAS = {
  convert_units: "convert",
  read_url: "url",
  open_result: "url",
  web_search: "search",
  code: "python",
  text_stats: "textstats",
  remember: "note",
  recall: "note",
  exchange: "currency",
  answer: "model",
  calc: "calculator",
} satisfies Record<string, keyof typeof SHAPES>;

/** A picture that exists, by its own name or by one it also answers to. */
export type IconName = keyof typeof SHAPES | keyof typeof ALIAS;

/** Every name an icon can be asked for by. */
export const ICON_NAMES = [...Object.keys(SHAPES), ...Object.keys(ALIAS)] as IconName[];

function strokes(name: string): Stroke[] | undefined {
  const alias: Record<string, string> = ALIAS;
  const shapes: Record<string, Stroke[]> = SHAPES;
  const key = Object.hasOwn(alias, name) ? (alias[name] as string) : name;
  return Object.hasOwn(shapes, key) ? shapes[key] : undefined;
}

/** Whether there is a picture by this name. Names come from the server, so any string may be asked. */
export function hasIcon(name: string | null | undefined): name is IconName {
  return Boolean(name && strokes(name));
}

/** The picture as markup, for HTML that is assembled as a string. Empty for a name with no picture. */
export function iconSvg(name: string | null | undefined, size = 16): string {
  const shape = name ? strokes(name) : undefined;
  if (!shape) return "";
  const paths = shape
    .map(
      ({ d, opacity }) =>
        `<path d="${d}" fill="none" stroke="currentColor" stroke-width="1.8" ` +
        `stroke-linecap="round" stroke-linejoin="round"${opacity ? ` opacity="${opacity}"` : ""}/>`,
    )
    .join("");
  return (
    `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" ` +
    `aria-hidden="true" focusable="false">${paths}</svg>`
  );
}

export interface IconProps {
  /** Which picture. Names come from the server, so any string is accepted; one with no picture draws nothing. */
  name: string | null | undefined;
  /** Width and height in pixels. */
  size?: number;
}

/** The picture as an element. Draws nothing for a name with no picture. */
export function Icon({ name, size = 16 }: IconProps) {
  const shape = name ? strokes(name) : undefined;
  if (!shape) return null;
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      {shape.map(({ d, opacity }) => (
        <path
          key={d}
          d={d}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={opacity}
        />
      ))}
    </svg>
  );
}
