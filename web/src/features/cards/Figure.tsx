import { createContext, type ReactNode, useContext, useLayoutEffect, useRef } from "react";
import { cx } from "@/lib/cx";
import { took } from "@/lib/format";
import { enter } from "@/lib/frame";
import { Icon } from "@/lib/icons";
import type { ToolCardMeta } from "./ToolCard";

/* Classes more than one card uses. */

/** The figure that is the answer: one size, wherever it appears. */
export const ANSWER = "text-6 font-semibold leading-[1.1] tracking-[-0.02em]";

/** Page titles and domains arrive from the open web at any length. */
export const TITLE =
  "text-4 font-semibold leading-[1.3] tracking-[-0.01em] [overflow-wrap:anywhere]";

/** A link to where the data came from. */
export const SOURCE = "inline-flex items-center gap-1 text-1";

/** Where a card stands among the cards of one answer. Each comes in a beat
 *  after the one before it. */
export const Position = createContext(0);

export interface FigureProps {
  /** Which icon heads the caption: the tool's own name. */
  tool: string;
  /** What the caption calls the tool. */
  name: string;
  /** A source or a qualifier, where there is one: "exact", a place, a domain. */
  note?: string;
  /** What the tool's step reported; its duration ends the caption. */
  meta?: ToolCardMeta | null;
  /** A failed tool is shown failing: the mark and the name take the fault colour. */
  failed?: boolean;
  /** `data-card`, the kind the server named. */
  card?: string;
  children: ReactNode;
}

/**
 * The shell every card shares: a figure with a caption that names the tool,
 * gives its source or a qualifier where there is one, and says how long it
 * took. This caption is the only place a finished tool is named and timed.
 *
 * A tool result is set as a figure between two hairlines. No box, no fill, no
 * border colour: the answer below is the model's, the figure above it is the
 * tool's, and the rules are what keep the two apart.
 */
export function Figure({ tool, name, note, meta, failed, card, children }: FigureProps) {
  const ms = meta?.ms;
  const position = useContext(Position);
  const root = useRef<HTMLElement>(null);
  const arrived = useRef(false);
  // The shared entrance: from just below, staggered by position. Before the
  // first paint, so the card is never seen at rest and then seen arriving;
  // and once, because a development build runs effects twice.
  useLayoutEffect(() => {
    if (arrived.current || !root.current) return;
    arrived.current = true;
    enter(root.current, { delay: position * 40 });
  }, [position]);

  return (
    <figure
      className="card-tool m-0 pt-3 pb-4 border-t border-rule text-2"
      data-card={card}
      ref={root}
    >
      <figcaption className="flex items-center gap-2 mb-3 text-ink-2 text-1">
        <span className={failed ? "text-fault" : undefined}>
          <Icon name={tool} size={14} />
        </span>
        <span className={cx("font-semibold", failed ? "text-fault" : "text-ink")}>{name}</span>
        {note ? <span className="min-w-0 [overflow-wrap:anywhere]">{note}</span> : null}
        {/* how long the tool took, at the end of its own caption */}
        {ms !== null && ms !== undefined ? (
          <span className="ml-auto pl-3 num">{took(ms)}</span>
        ) : null}
      </figcaption>
      {children}
    </figure>
  );
}
