import { useLayoutEffect, useRef } from "react";
import { Link } from "react-router";
import { cx } from "@/lib/cx";
import { ago } from "@/lib/format";
import { Button } from "@/shell/Button";
import { useConversations } from "@/state/queries";
import { deleteConversation, dom, loadConversation, setRail, useRail } from "./controller";
import { useChat } from "./run";

/**
 * The stored conversations, in a drawer from the right edge.
 *
 * A real <dialog>: the focus trap, Escape, the inert page behind it and the
 * return of focus to the button that opened it all come from the platform.
 * It is always in the page and opened with showModal() (see `setRail`), never
 * rendered on demand: an element that is not there cannot play its exit.
 * Its box, slide and backdrop are in chat.css.
 */
export function HistoryDialog() {
  const rail = useRef<HTMLDialogElement>(null);
  const conversations = useConversations();
  const current = useChat((s) => s.conversationId);
  const pending = useRail((s) => s.pending);
  const stagger = useRail((s) => s.stagger);

  useLayoutEffect(() => {
    dom.rail = rail.current;
    return () => {
      dom.rail = null;
      useRail.setState({ open: false, pending: false, stagger: false });
    };
  }, []);

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: the click is on the backdrop; the key that closes a dialog is Escape, and the platform handles it
    <dialog
      className="rail"
      id="rail"
      aria-labelledby="history-label"
      ref={rail}
      // Escape closes the dialog without going through setRail.
      onClose={() => useRail.setState({ open: false })}
      // The dialog has no padding of its own, so a click on it is a click on
      // the backdrop.
      onClick={(e) => {
        if (e.target === rail.current) setRail(false);
      }}
    >
      <div
        className={cx(
          "flex flex-col h-full pt-4 pl-5",
          "pr-[max(var(--edge),env(safe-area-inset-right))]",
          "pb-[max(var(--s-4),env(safe-area-inset-bottom))]",
        )}
      >
        <div className="flex items-center justify-between gap-4 pb-3">
          <h2 id="history-label" className="text-4 font-semibold tracking-[-0.01em]">
            History
          </h2>
          <Button variant="text" quiet id="rail-close" onClick={() => setRail(false)}>
            Close
          </Button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto -mr-2 pr-2">
          <ul
            id="convo-list"
            aria-labelledby="history-label"
            className={cx(
              "convo-list list-none m-0 p-0 border-t border-rule",
              pending && "pending",
              stagger && "stagger",
            )}
          >
            {conversations.length === 0 && (
              <li className="py-4 text-2 text-ink-2">No conversations yet.</li>
            )}
            {/* Two sibling buttons, not one nested inside the other: a control
                inside a control is invalid and leaves the inner one
                unreachable by keyboard. */}
            {conversations.map((c) => (
              <li key={c.id} className="flex items-center gap-2 border-b border-rule">
                <button
                  type="button"
                  data-id={c.id}
                  aria-current={c.id === current ? "true" : undefined}
                  onClick={() => void loadConversation(c.id)}
                  className={cx(
                    "group/open flex-1 min-w-0 py-3 pr-2 pl-0 border-0 bg-transparent",
                    "text-start cursor-pointer coarse:min-h-[52px]",
                  )}
                >
                  <span
                    dir="auto"
                    className={cx(
                      "block text-2 whitespace-nowrap overflow-hidden text-ellipsis",
                      "group-hover/open:underline group-hover/open:decoration-ink-2",
                      "group-hover/open:underline-offset-[3px]",
                      "group-aria-[current=true]/open:font-semibold",
                    )}
                  >
                    {c.title}
                  </span>
                  <span className="block text-1 text-ink-2">
                    {`${c.n} ${c.n === 1 ? "message" : "messages"} · ${ago(c.updated_at)}`}
                  </span>
                </button>
                <button
                  type="button"
                  data-del={c.id}
                  aria-label={`Delete conversation: ${c.title}`}
                  onClick={() => void deleteConversation(c.id, c.title)}
                  // The shell's icon button, written out: this one turns the
                  // fault colour under the pointer, not ink.
                  className={cx(
                    "appearance-none grid place-items-center size-[32px] p-0",
                    "border-0 rounded-1 bg-transparent text-ink-2 cursor-pointer",
                    "transition-[background,color] duration-(--dur-1)",
                    "hover:bg-sheet-2 hover:text-fault coarse:size-[44px]",
                  )}
                >
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    aria-hidden="true"
                  >
                    <path d="M6 6l12 12M18 6L6 18" />
                  </svg>
                </button>
              </li>
            ))}
          </ul>
        </div>

        {/* The page links join the drawer only where the top bar has no room
            for them. */}
        <nav aria-label="Pages" className="hidden le-620:flex gap-5 pt-4 text-2">
          <Link to="/bench" viewTransition>
            Benchmark
          </Link>
          <Link to="/about" viewTransition>
            About
          </Link>
        </nav>
      </div>
    </dialog>
  );
}
