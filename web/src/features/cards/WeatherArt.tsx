import type { ReactNode } from "react";
import { cx } from "@/lib/cx";

/* Weather, drawn. Every glyph is inline SVG: nothing is fetched from a weather
   provider's CDN, so the page keeps working with no outbound requests from the
   browser at all. The shapes take their colours from cards.css. */

const CLOUD_HIGH = "M14 28h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z";

const RAYS = Array.from({ length: 8 }, (_, i) => {
  const a = (i * Math.PI) / 4;
  return {
    x1: (24 + Math.cos(a) * 13).toFixed(1),
    y1: (24 + Math.sin(a) * 13).toFixed(1),
    x2: (24 + Math.cos(a) * 18).toFixed(1),
    y2: (24 + Math.sin(a) * 18).toFixed(1),
  };
});

const ART: Record<string, ReactNode> = {
  sun: (
    <>
      <circle cx="24" cy="24" r="9" className="w-sun" />
      <g className="w-rays">
        {RAYS.map((r) => (
          <line key={`${r.x1} ${r.y1}`} x1={r.x1} y1={r.y1} x2={r.x2} y2={r.y2} />
        ))}
      </g>
    </>
  ),
  "cloud-sun": (
    <>
      <circle cx="18" cy="18" r="7" className="w-sun" />
      <path className="w-cloud" d="M16 34h16a6 6 0 0 0 0-12 8 8 0 0 0-15-2 5 5 0 0 0-1 14z" />
    </>
  ),
  cloud: <path className="w-cloud" d="M14 34h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z" />,
  rain: (
    <>
      <path className="w-cloud" d={CLOUD_HIGH} />
      <g className="w-drop">
        <line x1="17" y1="32" x2="15" y2="39" />
        <line x1="24" y1="32" x2="22" y2="41" />
        <line x1="31" y1="32" x2="29" y2="39" />
      </g>
    </>
  ),
  drizzle: (
    <>
      <path className="w-cloud" d={CLOUD_HIGH} />
      <g className="w-drop">
        <line x1="18" y1="32" x2="17" y2="36" />
        <line x1="25" y1="32" x2="24" y2="36" />
        <line x1="32" y1="32" x2="31" y2="36" />
      </g>
    </>
  ),
  snow: (
    <>
      <path className="w-cloud" d={CLOUD_HIGH} />
      <g className="w-drop">
        <circle cx="17" cy="35" r="1.6" />
        <circle cx="25" cy="38" r="1.6" />
        <circle cx="32" cy="35" r="1.6" />
      </g>
    </>
  ),
  sleet: (
    <>
      <path className="w-cloud" d={CLOUD_HIGH} />
      <g className="w-drop">
        <line x1="18" y1="32" x2="16" y2="38" />
        <circle cx="27" cy="37" r="1.6" />
      </g>
    </>
  ),
  storm: (
    <>
      <path className="w-cloud" d="M14 26h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z" />
      <path className="w-bolt" d="M25 28l-6 9h5l-2 8 8-11h-5l3-6z" />
    </>
  ),
  fog: (
    <>
      <path className="w-cloud" d="M14 24h20a7 7 0 0 0 0-14 9 9 0 0 0-17-2 6 6 0 0 0-3 16z" />
      <g className="w-drop">
        <line x1="12" y1="30" x2="36" y2="30" />
        <line x1="15" y1="35" x2="33" y2="35" />
        <line x1="12" y1="40" x2="30" y2="40" />
      </g>
    </>
  ),
};

/** The drawing for a condition the server named; a cloud for one it did not. */
export function WeatherArt({ condition, small }: { condition: string; small?: boolean }) {
  const art = Object.hasOwn(ART, condition) ? ART[condition] : ART.cloud;
  return (
    <svg
      className={cx("w-art flex-none", small ? "size-[28px]" : "size-[44px]")}
      viewBox="0 0 48 48"
      aria-hidden="true"
    >
      {art}
    </svg>
  );
}
