/* Reading a card's payload.
 *
 * The data comes from a tool, and half the tools read the open web: any field
 * may be missing, null, or not the type its name suggests. Each card reads
 * what it needs through these, so a field that is not there is an empty
 * string or an empty list and the rest of the figure is still drawn.
 */

import type { CardData } from "@/lib/types";

/** A field as text; empty when it is missing. */
export function text(data: CardData, key: string): string {
  const value = data[key];
  return value === null || value === undefined ? "" : String(value);
}

/** A field as a list; empty when it is missing or is not one. */
export function list(data: CardData, key: string): unknown[] {
  const value = data[key];
  return Array.isArray(value) ? value : [];
}

/** One entry of a list as a record, whatever it actually was. */
export function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
}
