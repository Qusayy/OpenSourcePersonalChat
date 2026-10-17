import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { cx } from "@/lib/cx";
import { EASE, reduced } from "@/lib/frame";
import { buttonClass } from "@/shell/Button";
import { useInfo } from "@/state/queries";
import { dom, send, stopGeneration } from "./controller";
import { COL } from "./layout";
import { type CharCount, charCount, placeholderFor } from "./rules";
import { useChat } from "./run";

/* The composer: the input on its rule, Send, and under them the hint.

   There is nothing to choose before sending. The agent decides for itself
   whether a message needs a tool, which one, and how to write the answer.

   The textarea is not a controlled input. Its text is read when it is needed,
   on Send; that is the DOM's own job and React is not asked to mirror it on
   every key. */

export function Composer() {
  const view = useChat((s) => s.view);
  const streaming = useChat((s) => s.streaming);
  const info = useInfo();
  const empty = view === "empty";

  const input = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const [count, setCount] = useState<CharCount | null>(null);

  useLayoutEffect(() => {
    dom.input = input.current;
    return () => {
      dom.input = null;
    };
  }, []);

  /** Everything that follows from the input's text having changed. */
  const changed = useCallback(() => {
    const el = input.current;
    if (!el) return;
    // The input grows with its text, to a point.
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 208)}px`;
    // Only speak up near the ceiling — a permanent counter is noise.
    const c = charCount(el.value.length);
    setCount(c.near ? c : null);
  }, []);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (useChat.getState().streaming) {
      void stopGeneration();
      return;
    }
    const el = input.current;
    const text = el?.value.trim();
    if (!el || !text) return;
    el.value = "";
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 208)}px`;
    setCount(null);
    // Started here, from the event that asked for it, and never from an
    // effect: a development build runs effects twice, and a second POST would
    // store the question twice.
    void send(text);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  // The button changes what it does; the word is seen to change with it.
  const label = useRef<HTMLSpanElement>(null);
  const wasStreaming = useRef(streaming);
  useEffect(() => {
    if (wasStreaming.current === streaming) return;
    wasStreaming.current = streaming;
    const el = label.current;
    if (!el || reduced() || !el.animate) return;
    el.animate(
      [
        { opacity: 0, translate: "0 35%" },
        { opacity: 1, translate: "0 0" },
      ],
      { duration: 200, easing: EASE },
    );
  }, [streaming]);

  const placeholder = placeholderFor(info);

  return (
    <div
      className={
        empty
          ? cx(COL, "flex-none pt-3 pb-4")
          : // Pinned under a scrolling thread, the composer needs an edge to
            // scroll against. It is beside the scroller here, not inside it,
            // so it does not get the scroller's gutters; --sbw is the measured
            // scrollbar width, and adding it keeps the input exactly as wide
            // as the thread.
            cx(
              "w-full mx-auto flex-none pt-3 pb-4 border-t border-rule",
              "pl-[calc(max(var(--edge),env(safe-area-inset-left))+var(--sbw,0px))]",
              "pr-[calc(max(var(--edge),env(safe-area-inset-right))+var(--sbw,0px))]",
            )
      }
    >
      <form
        id="composer"
        ref={form}
        onSubmit={submit}
        // The one authored move on the page: when the first question is sent
        // the composer travels from under the headline to the foot of the
        // sheet.
        className={cx(
          "group/composer arrive relative [view-transition-name:composer]",
          !empty && "max-w-col mx-auto",
        )}
        style={{ "--i": 2 } as React.CSSProperties}
      >
        {/* The rule under the input is its boundary; it thickens to say the
            input has focus, since a textarea with no box has no ring to draw. */}
        <div
          className={cx(
            "flex items-end gap-3 pb-2 border-b-2 border-ink",
            "transition-[box-shadow] duration-(--dur-1)",
            "group-focus-within/composer:[box-shadow:0_1px_0_var(--ink)]",
          )}
        >
          <label className="sr-only" htmlFor="input">
            Message {info.model}
          </label>
          <textarea
            id="input"
            ref={input}
            rows={1}
            placeholder={placeholder}
            autoComplete="off"
            spellCheck="true"
            maxLength={8000}
            aria-describedby="input-hint"
            onInput={changed}
            onKeyDown={onKeyDown}
            className={cx(
              "flex-1 min-w-0 min-h-[2.5rem] max-h-[208px] py-2 px-0 border-0 bg-transparent",
              "resize-none outline-none leading-[1.5] [overflow-wrap:anywhere] caret-live",
              "placeholder:text-ink-2 placeholder:opacity-100",
              // Never below 16px: iOS Safari force-zooms a focused input under
              // that and leaves the page scrolled sideways. On the empty sheet
              // the input is the thing to do, and is set like it.
              empty ? "text-4" : "text-[length:max(16px,var(--t-3))]",
            )}
          />
          <button
            id="send"
            type="submit"
            data-mode={streaming ? "stop" : "send"}
            aria-label={streaming ? "Stop generating" : "Send message"}
            disabled={info.status !== "ready"}
            className={cx(
              buttonClass({ variant: "primary" }),
              "flex-none min-w-[4.5rem]",
              "data-[mode=stop]:border-fault data-[mode=stop]:bg-transparent",
              "data-[mode=stop]:text-fault data-[mode=stop]:hover:bg-fault-wash",
            )}
          >
            <span id="send-label" ref={label}>
              {streaming ? "Stop" : "Send"}
            </span>
          </button>
        </div>

        <div className="flex items-center gap-4 min-w-0 pt-2 text-ink-2 text-1">
          <span className="flex-1" />
          <span id="char-count" hidden={!count} className={cx("num", count?.warn && "text-fault")}>
            {count?.text}
          </span>
          {/* Keyboard hints are for keyboards: on a narrow screen they go. */}
          <span
            id="input-hint"
            className="inline-flex items-center gap-[6px] whitespace-nowrap le-760:hidden"
          >
            <kbd>Shift</kbd> <kbd>Enter</kbd> for a new line
            <span className="sr-only">. Enter sends.</span>
          </span>
        </div>
      </form>
    </div>
  );
}
