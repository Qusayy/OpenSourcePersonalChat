import { type ReactNode, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router";
import { cx } from "@/lib/cx";
import type { PageName } from "./page";

const ACTIONS_ID = "topbar-actions";

const NAV_LINK = cx(
  "py-1 text-ink-2 no-underline transition-[color] duration-(--dur-1) hover:text-ink",
  // Where you are is said with ink and a rule, not a filled tab.
  "aria-[current=page]:text-ink aria-[current=page]:underline",
  "aria-[current=page]:decoration-2 aria-[current=page]:decoration-ink",
  "aria-[current=page]:underline-offset-[7px]",
  "coarse:inline-flex coarse:items-center coarse:min-h-[44px]",
);

const PAGES: Array<{ page: PageName; to: string; label: string }> = [
  { page: "chat", to: "/", label: "Chat" },
  { page: "bench", to: "/bench", label: "Benchmark" },
  { page: "about", to: "/about", label: "About" },
];

/** The top bar: the name, the three pages, and whatever the page in view adds
 *  at the right end (see `TopBarActions`). */
export function TopBar({ page }: { page: PageName }) {
  return (
    <header
      className={cx(
        "flex items-center gap-5 min-w-0 border-b border-rule [view-transition-name:topbar]",
        "pl-[max(var(--inset),env(safe-area-inset-left))]",
        "pr-[max(var(--inset),env(safe-area-inset-right))]",
        "le-620:gap-4",
      )}
    >
      <Link className="text-3 font-bold tracking-[-0.01em] no-underline" to="/" viewTransition>
        Aurora
      </Link>
      <nav
        aria-label="Primary"
        // On the chat page History and New take the room at phone width; the
        // page links move into the history drawer. The other pages have
        // nothing else in the bar and keep them.
        className={cx("flex gap-4 text-2", page === "chat" && "le-620:hidden")}
      >
        {PAGES.map((p) => (
          <Link
            key={p.page}
            to={p.to}
            viewTransition
            className={NAV_LINK}
            aria-current={p.page === page ? "page" : undefined}
          >
            {p.label}
          </Link>
        ))}
      </nav>
      <span className="flex-1" />
      <div id={ACTIONS_ID} className="contents" />
    </header>
  );
}

/**
 * Put controls at the right end of the top bar from inside a page. The bar is
 * drawn by the shell and outlives the page; the page still owns its own
 * buttons and their state.
 */
export function TopBarActions({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  useEffect(() => setSlot(document.getElementById(ACTIONS_ID)), []);
  return slot ? createPortal(children, slot) : null;
}
