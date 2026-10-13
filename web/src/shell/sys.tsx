/* The machine's figures, as text, wherever a page names them: the status
 * line, the About page, the memory estimate on Benchmark. They come from one
 * place (`useSystem`, fed by the telemetry poll), so every mention agrees and
 * updates together. A figure the platform could not give is null, and whatever
 * would have shown it is absent rather than a dash.
 */

import type { ComponentProps, ElementType, ReactNode } from "react";
import { coreNoun, cpuText, gb, gbExact, loadText, pct } from "@/lib/format";
import type { SystemSnapshot } from "@/lib/types";
import { useSystem } from "@/state/queries";

export type SysFactName = "cpus" | "cpu" | "ram" | "rss" | "rss-exact" | "limit" | "load";

/** Every named fact for one snapshot; null where there is nothing to say. */
export function sysFacts(s: SystemSnapshot): Record<SysFactName, string | null> {
  // The ceiling that actually bites is the lower of the two the service has.
  const limit = [s.limit_high_mb, s.limit_mb].filter((v): v is number => v !== null);
  return {
    cpus: s.cpus === null ? null : `${cpuText(s.cpus)} ${coreNoun(s.cpus)}`,
    cpu: s.cpu_pct === null ? null : pct(s.cpu_pct),
    ram:
      s.mem_total_mb === null || s.mem_used_mb === null
        ? null
        : `${gb(s.mem_used_mb)} / ${gb(s.mem_total_mb)} GB`,
    rss: s.rss_mb === null ? null : `${gb(s.rss_mb)} GB`,
    // Beside the memory estimate, which is printed to two places.
    "rss-exact": s.rss_mb === null ? null : `${gbExact(s.rss_mb)} GB`,
    limit: limit.length ? `${gb(Math.min(...limit))} GB` : null,
    load: s.load ? loadText(s.load) : null,
  };
}

/** One fact as live text, or null when the machine could not give it. */
export function useSysFact(name: SysFactName): string | null {
  return sysFacts(useSystem())[name];
}

type SysRowProps<T extends ElementType> = {
  /** The fact this row is about. With nothing to say, the row is not drawn. */
  fact: SysFactName;
  as?: T;
  /** Receives the fact's text. */
  children: (value: string) => ReactNode;
} & Omit<ComponentProps<T>, "children">;

/**
 * A row that exists only while its fact does. `children` is given the text:
 *
 *   <SysRow fact="rss" as="div">{(v) => <><dt>Memory held</dt><dd>{v}</dd></>}</SysRow>
 */
export function SysRow<T extends ElementType = "span">({
  fact,
  as,
  children,
  ...rest
}: SysRowProps<T>) {
  const value = useSysFact(fact);
  if (value === null) return null;
  const Tag: ElementType = as ?? "span";
  return <Tag {...rest}>{children(value)}</Tag>;
}
