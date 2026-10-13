/* The pieces a long-form page is set from. Benchmark and About are both made
   of these: a sheet that scrolls, a heading with a contents list, ruled
   sections, short lists of facts, and prose.

   Preflight has taken every default margin away, so each piece states the
   space it keeps; nothing here leans on what a browser would have given a
   paragraph or a heading. */

import type { ComponentProps, ReactNode } from "react";
import { cx } from "@/lib/cx";
import { Main } from "@/shell/Main";

/** The page: the sheet scrolls, and the column inside it is the same column
 *  the chat page uses, so the left edge of the text does not move when the
 *  page changes. Text, fact lists and bar charts all run its full width. */
export function LongPage({ children }: { children: ReactNode }) {
  return (
    <Main>
      <div className="flex-1 min-h-0 overflow-y-auto [scrollbar-gutter:stable_both-edges]">
        <div
          className={cx(
            "max-w-[calc(var(--col)+var(--edge)*2)] mx-auto pt-7 pb-9",
            "pr-[max(var(--edge),env(safe-area-inset-right))]",
            "pl-[max(var(--edge),env(safe-area-inset-left))]",
            "le-640:pt-6 le-640:pb-8",
          )}
        >
          {children}
        </div>
      </div>
    </Main>
  );
}

const TOC_LINK = cx(
  "py-1 text-ink-2 hover:text-ink",
  "coarse:inline-flex coarse:items-center coarse:min-h-[44px]",
);

/** The head of a page: what it is, in a sentence or two, and where its parts are. */
export function PageHead({
  title,
  children,
  contents,
}: {
  title: string;
  children: ReactNode;
  /** In-page links: `[id, label]`. */
  contents: ReadonlyArray<readonly [string, string]>;
}) {
  return (
    <header>
      <h1 className="mb-4 max-w-[20ch] text-7 font-bold leading-[1.04] tracking-[-0.03em] text-balance intro:arrive-0">
        {title}
      </h1>
      <p className="text-pretty intro:arrive-1">{children}</p>
      <nav
        className="flex flex-wrap gap-y-1 gap-x-5 mt-5 text-2 coarse:gap-y-0 intro:arrive-2"
        aria-label="On this page"
      >
        {contents.map(([id, label]) => (
          <a key={id} className={TOC_LINK} href={`#${id}`}>
            {label}
          </a>
        ))}
      </nav>
    </header>
  );
}

/** One part of a page, under its own heading. `first` is the section that
 *  arrives straight after the head; the rest arrive together, a beat later. */
export function Section({
  title,
  sub,
  first,
  className,
  children,
  ...rest
}: {
  title: string;
  /** A line under the heading, in the second ink. */
  sub?: ReactNode;
  first?: boolean;
} & Omit<ComponentProps<"section">, "title">) {
  return (
    <section
      className={cx(
        "mt-8 scroll-mt-5 le-640:mt-7",
        first ? "intro:arrive-3" : "intro:arrive-4",
        className,
      )}
      {...rest}
    >
      <h2 className="mb-2 text-5 font-semibold leading-[1.2] tracking-[-0.015em]">{title}</h2>
      {sub ? <p className="mb-4 text-ink-2 text-2">{sub}</p> : null}
      {children}
    </section>
  );
}

/** A panel is a ruled-off block, not a box. */
export function Panel({ className, ...rest }: ComponentProps<"div">) {
  return <div className={cx("pt-4 border-t border-rule", className)} {...rest} />;
}

/* ------------------------------------------------------------------ facts */

/** A short list of settings: term on the left, value on the right. */
export function Facts({ className, ...rest }: ComponentProps<"dl">) {
  return <dl className={cx("mt-3 border-t border-rule text-2", className)} {...rest} />;
}

export const FACT_ROW = "flex justify-between gap-4 py-2 border-b border-rule";
export const FACT_TERM = "text-ink-2";
export const FACT_VALUE = "text-right";

/** One row of a list of facts. `figure` sets the value fixed-width. */
export function Fact({
  term,
  figure,
  children,
  ...rest
}: { term: ReactNode; figure?: boolean } & ComponentProps<"div">) {
  return (
    <div className={FACT_ROW} {...rest}>
      <dt className={FACT_TERM}>{term}</dt>
      <dd className={figure ? `${FACT_VALUE} num` : FACT_VALUE}>{children}</dd>
    </div>
  );
}

/* ------------------------------------------------------------------ prose */

/** A paragraph of explanation. The last of a run keeps no space under it. */
export function P({ children }: { children: ReactNode }) {
  return <p className="mb-[0.9em] text-pretty last:mb-0">{children}</p>;
}

/** A word of code inside a sentence. */
export function Code({ children }: { children: ReactNode }) {
  return <code className="py-px px-[4px] rounded-1 bg-sheet-2 text-[0.94em]">{children}</code>;
}

/** A sentence or two that belongs to the chart above it. */
export const AFTER_CHART = "mt-4 text-2";

/* ------------------------------------------------------------------ notes */

/**
 * A warning about the data, set where it applies: a ruled note, not a box.
 * The mark carries the colour; the words stay in ink so they are read. The
 * chart's own top rule closes it underneath.
 */
export function Caveat({ className, children, ...rest }: ComponentProps<"div">) {
  return (
    <div className={cx("flex gap-3 py-3 border-t border-rule text-2", className)} {...rest}>
      <svg
        className="block flex-none mt-[2px] text-caution"
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M12 8v5M12 16.5v.5" />
        <path d="M10.3 4.3 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z" />
      </svg>
      <span>{children}</span>
    </div>
  );
}

/** What a chart says before there is anything to draw. */
export function Empty({ className, ...rest }: ComponentProps<"div">) {
  return <div className={cx("py-2 text-ink-2 text-2", className)} {...rest} />;
}
