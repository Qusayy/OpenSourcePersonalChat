import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router";
import { curtains } from "@/lib/curtains";
import { cx } from "@/lib/cx";
import { Button } from "@/shell/Button";
import { Main } from "@/shell/Main";
import { TopBarActions } from "@/shell/TopBar";
import { useInfo } from "@/state/queries";
import { Composer } from "./Composer";
import { dom, leave, newChat, openHistory, useRail } from "./controller";
import { HistoryDialog } from "./HistoryDialog";
import { COL } from "./layout";
import { useChat } from "./run";
import { Thread } from "./Thread";

/* The chat page: the empty sheet, the thread, and the composer.

   Two views, carried by data-view on <main>:

     empty   headline, paragraph, and the composer directly under them.
             Nothing is pinned; the page scrolls as one sheet.
     thread  the composer sits above the status line and the thread scrolls
             above it.

   DOM order is the visual order in both, so nothing is reordered with CSS and
   the tab order never disagrees with what is on screen. */

export function ChatPage() {
  const view = useChat((s) => s.view);
  const railOpen = useRail((s) => s.open);
  const info = useInfo();
  const wrap = useRef<HTMLDivElement>(null);
  const empty = view === "empty";

  // The scroller is the controller's to read and write while an answer
  // streams. Coming back to a conversation, it opens at the latest exchange.
  useLayoutEffect(() => {
    dom.wrap = wrap.current;
    if (wrap.current) wrap.current.scrollTop = wrap.current.scrollHeight;
    return () => {
      dom.wrap = null;
    };
  }, []);

  // The field takes focus when the page is come to, so typing can start at
  // once. Not after a tap on a touch screen: there, focus raises the keyboard
  // over the page that was just asked for. (A loaded page, and Back, arrive
  // as "POP"; a link arrives as "PUSH".)
  const arrivedByLink = useNavigationType() !== "POP";
  // biome-ignore lint/correctness/useExhaustiveDependencies: decided once, as the page arrives
  useEffect(() => {
    const touch = window.matchMedia("(pointer: coarse)").matches;
    if (!(touch && arrivedByLink)) dom.input?.focus();
    // Leaving the page with an answer in flight stops it, as Stop does.
    return () => leave();
  }, []);

  useNewChatOnChatLink();

  // The curtains hang over the empty sheet and go when a conversation starts;
  // the light at the status line takes over from them.
  useEffect(() => curtains.rest(empty), [empty]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        newChat();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <TopBarActions>
        <Button
          variant="text"
          id="menu-btn"
          aria-haspopup="dialog"
          aria-expanded={railOpen}
          onClick={() => void openHistory()}
        >
          History
        </Button>
        <Button variant="text" id="new-chat" onClick={newChat}>
          New chat
        </Button>
      </TopBarActions>

      <HistoryDialog />

      <Main
        data-view={view}
        // A scrollbar that takes room on one side only would pull a centred
        // column off the axis the top bar and status line keep. Room is
        // reserved on both sides, whether or not anything scrolls.
        className={empty ? "overflow-y-auto [scrollbar-gutter:stable_both-edges]" : undefined}
      >
        {/* <main> is a flex column so the thread can take the spare height. On
            the empty sheet nothing should give: a flex item may shrink below
            its content, and here that stacked the headline and the input on
            top of one another on any screen too short to hold both. */}
        <div
          id="stream-wrap"
          ref={wrap}
          className={cx(
            "min-h-0",
            empty
              ? "shrink-0"
              : "flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable_both-edges]",
          )}
        >
          <section
            id="hero"
            className={cx(COL, "pt-[clamp(var(--s-6),7vh,var(--s-9))] pb-5 le-620:pt-6")}
            hidden={!empty}
          >
            <h1 className="arrive mb-4 text-7 font-bold leading-[1.04] tracking-[-0.03em] text-balance">
              Ask it something small.
            </h1>
            {/* One string, so one text node: two side by side are shaped apart,
                and the space between the model's name and the next word is
                then a hair off. */}
            <p className="arrive text-3 text-pretty" style={{ "--i": 1 } as React.CSSProperties}>
              {`Sums, conversions, the weather, the news, a link boiled down, a quick draft. The writing comes from ${info.model} on this server, and it searches the web by itself when a question needs it.`}
            </p>
          </section>

          {/* Not a live region: milestones are announced through the shell's
              one channel, and the finished answer is read by browsing. */}
          <div id="thread" className={cx(COL, "py-6")} hidden={empty}>
            <Thread />
          </div>
        </div>

        <Composer />
      </Main>
    </>
  );
}

/**
 * Clicking Chat (or the name) while already on the chat page starts a new
 * conversation: it used to reload to an empty sheet, and should still land
 * on one. The router gives such a click a new location key. Arriving on the
 * page is not that, and neither is the browser's own back or forward or a
 * link to a part of this page.
 */
function useNewChatOnChatLink(): void {
  const { key } = useLocation();
  const how = useNavigationType();
  const seen = useRef(key);
  useEffect(() => {
    if (seen.current === key) return;
    seen.current = key;
    // After this effect has returned: starting over redraws the page at once,
    // which React will not do from inside an effect.
    if (how !== "POP") queueMicrotask(newChat);
  }, [key, how]);
}
