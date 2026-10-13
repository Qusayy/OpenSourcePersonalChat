import {
  type FormEvent,
  Fragment,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { bootstrap } from "@/lib/bootstrap";
import { cx } from "@/lib/cx";
import { EASE, reduced } from "@/lib/frame";
import { hasIcon, Icon } from "@/lib/icons";
import { Button, buttonClass } from "@/shell/Button";
import { useInfo } from "@/state/queries";
import { dom, send, setSkill, stopGeneration } from "./controller";
import { COL } from "./layout";
import { PersonaToggle } from "./PersonaToggle";
import {
  type CharCount,
  charCount,
  completeCommand,
  type PaletteRow,
  paletteQuery,
  paletteRows,
  placeholderFor,
} from "./rules";
import { useChat } from "./run";

/* The composer: the input on its rule, Send, and under them the persona and
   the hint. Above the input, when called for, the armed skill and the "/"
   palette.

   The textarea is not a controlled input. Its text is read when it is needed
   (on Send, on a keystroke that might open the palette) and written when a
   command is completed, with the example's arguments left selected; that is
   the DOM's own job and React is not asked to mirror it on every key. */

interface Palette {
  rows: PaletteRow[];
  /** The row the keyboard has selected. */
  index: number;
  /** The row the pointer is on, described in its place; null when on none. */
  pointed: number | null;
}

const CLOSED: Palette = { rows: [], index: 0, pointed: null };

export function Composer() {
  const view = useChat((s) => s.view);
  const skill = useChat((s) => s.skill);
  const streaming = useChat((s) => s.streaming);
  const info = useInfo();
  const empty = view === "empty";

  const input = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const [count, setCount] = useState<CharCount | null>(null);
  const [palette, setPalette] = useState<Palette>(CLOSED);
  // The handlers below run between renders and need the palette as it is now.
  const live = useRef(palette);
  live.current = palette;
  const open = palette.rows.length > 0;

  useLayoutEffect(() => {
    dom.input = input.current;
    return () => {
      dom.input = null;
    };
  }, []);

  const closePalette = useCallback(() => {
    live.current = CLOSED;
    setPalette(CLOSED);
  }, []);

  /** Everything that follows from the input's text having changed. */
  const changed = useCallback(() => {
    const el = input.current;
    if (!el) return;
    // The palette: typing a slash lists tools and skills.
    const query = paletteQuery(el.value);
    const next: Palette =
      query === null
        ? CLOSED
        : {
            rows: paletteRows(
              query,
              bootstrap().tools,
              bootstrap().skills,
              useChat.getState().view === "empty",
            ),
            index: 0,
            pointed: null,
          };
    live.current = next;
    setPalette(next);
    // The input grows with its text, to a point.
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 208)}px`;
    // Only speak up near the ceiling — a permanent counter is noise.
    const c = charCount(el.value.length);
    setCount(c.near ? c : null);
  }, []);

  /**
   * Take a row. Picking a tool completes the command, with the example's
   * arguments selected so typing replaces them; picking a skill arms it.
   * Either way the routing pass is skipped entirely, which on 2 vCPU is worth
   * several seconds.
   */
  const choose = useCallback(
    (i: number) => {
      const el = input.current;
      const row = live.current.rows[i];
      if (!el || !row) return;
      if (row.kind === "skill") {
        setSkill(bootstrap().skills.find((s) => s.id === row.id) ?? null);
        el.value = "";
      } else {
        const { value, select } = completeCommand(row.cmd);
        el.value = value;
        if (select) el.setSelectionRange(select[0], select[1]);
      }
      closePalette();
      el.focus();
      changed();
    },
    [changed, closePalette],
  );

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

  /* Two things listen to a key in the input, in this order: the palette, if
     it is open, and then the composer. The order is the behaviour. Enter on a
     tool row first completes the command, which closes the palette, and the
     composer, finding it closed, then sends: so Enter on a tool runs its
     example at once, and Tab is the key that only completes it. Enter on a
     skill row arms the skill and leaves an empty input, which sends nothing. */
  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    const { rows, index } = live.current;
    if (rows.length) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = (index + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
        const moved = { ...live.current, index: next };
        live.current = moved;
        setPalette(moved);
      } else if (e.key === "Tab" || e.key === "Enter") {
        e.preventDefault();
        choose(index);
      } else if (e.key === "Escape") {
        closePalette();
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !live.current.rows.length) {
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

  const placeholder = skill ? skill.placeholder || `${skill.name}…` : placeholderFor(info);

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
        // sheet. `relative` anchors the palette.
        className={cx(
          "group/composer arrive relative [view-transition-name:composer]",
          !empty && "max-w-col mx-auto",
        )}
        style={{ "--i": 2 } as React.CSSProperties}
      >
        <PaletteList
          palette={palette}
          open={open}
          below={empty}
          onChoose={choose}
          onPoint={(pointed) => setPalette((p) => ({ ...p, pointed }))}
        />

        {/* the armed skill, named above the input */}
        <div
          id="skill-active"
          hidden={!skill}
          className="flex items-center gap-2 pb-1 text-ink-2 text-1"
        >
          {skill && (
            <>
              <span>Skill</span>
              <b className="text-ink">{skill.name}</b>
              <Button variant="text" small quiet onClick={() => setSkill(null)}>
                Clear
              </Button>
            </>
          )}
        </div>

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
          {/* biome-ignore lint/a11y/useAriaPropsSupportedByRole: the input owns the palette's listbox, and a listbox's options are announced through the element that owns it */}
          <textarea
            id="input"
            ref={input}
            rows={1}
            placeholder={placeholder}
            autoComplete="off"
            spellCheck="true"
            maxLength={8000}
            aria-describedby="input-hint"
            aria-controls="palette"
            aria-expanded={open}
            aria-activedescendant={open ? `pal-opt-${palette.index}` : undefined}
            onInput={changed}
            onKeyDown={onKeyDown}
            // After a moment: a click on a palette row lands first.
            onBlur={() => setTimeout(closePalette, 120)}
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

        <div
          className={cx(
            "flex items-center gap-4 min-w-0 pt-2 text-ink-2 text-1",
            // Five personas do not fit one row at phone width; they wrap, and
            // the row's own gap would be spent on an empty spacer.
            "le-620:items-start le-620:gap-x-0",
          )}
        >
          <PersonaToggle />
          <span className="flex-1" />
          <span
            id="char-count"
            hidden={!count}
            className={cx("num le-620:ml-3", count?.warn && "text-fault")}
          >
            {count?.text}
          </span>
          {/* The whole hint is there for a screen reader; only the part a
              sighted visitor would not guess is shown. Keyboard hints are for
              keyboards: on a narrow screen the row is for the personas. */}
          <span
            id="input-hint"
            className="inline-flex items-center gap-[6px] whitespace-nowrap le-760:hidden"
          >
            <kbd>/</kbd> for tools
            <span className="sr-only">. Enter sends, Shift and Enter adds a line.</span>{" "}
          </span>
        </div>
      </form>
    </div>
  );
}

/* --------------------------------------------------------- the palette -- */

/**
 * The "/" palette. The list holds commands; what the highlighted one does is
 * written out in full beneath it. Each option also carries its description
 * for a screen reader, which reads options, not the panel under them.
 */
function PaletteList({
  palette,
  open,
  below,
  onChoose,
  onPoint,
}: {
  palette: Palette;
  open: boolean;
  /** Under the headline there is no room above the input, and a list opening
   *  upward would be cut off by the top bar. It opens downward there. */
  below: boolean;
  onChoose: (i: number) => void;
  onPoint: (i: number | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const { rows, index, pointed } = palette;

  // The panel arrives; its rows do not. They are rebuilt on every keystroke,
  // and a list that re-entered each time a letter was typed would flicker.
  const wasOpen = useRef(false);
  useLayoutEffect(() => {
    const el = host.current;
    if (open && !wasOpen.current && el && !reduced() && el.animate) {
      const from = getComputedStyle(el).bottom === "auto" ? "-6px" : "6px";
      el.animate(
        [
          { opacity: 0, translate: `0 ${from}` },
          { opacity: 1, translate: "0 0" },
        ],
        { duration: 160, easing: EASE },
      );
    }
    wasOpen.current = open;
  }, [open]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a new list or a new selection is when to bring the selected row into view
  useLayoutEffect(() => {
    host.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [rows, index]);

  return (
    <div
      id="palette"
      ref={host}
      hidden={!open}
      role="listbox"
      aria-label="Tools and skills"
      // Every line in the list, heading or option, is --row tall. The list is
      // capped at a whole number of them and snaps to them, so it never ends
      // partway through a row.
      className={cx(
        "absolute inset-x-0 z-(--z-pop) flex flex-col p-1",
        "border border-rule rounded-2 bg-sheet shadow-pop [--row:36px] coarse:[--row:48px]",
        below ? "top-[calc(100%+var(--s-2))]" : "bottom-[calc(100%+var(--s-2))]",
      )}
    >
      {open && (
        <>
          <div className="max-h-[calc(var(--row)*9)] overflow-y-auto snap-y snap-proximity">
            {rows.map((row, i) => (
              <Fragment key={`${row.kind}:${row.id}`}>
                {(i === 0 || rows[i - 1]?.kind !== row.kind) && (
                  <div
                    data-group
                    className="flex items-end h-(--row) px-3 pb-2 text-ink-2 text-1 snap-start"
                  >
                    {row.kind === "skill" ? "Skills" : "Tools"}
                  </div>
                )}
                {/* biome-ignore lint/a11y/useFocusableInteractive: focus stays in the input, which names the active option */}
                <div
                  role="option"
                  id={`pal-opt-${i}`}
                  data-i={i}
                  aria-selected={i === index}
                  onMouseDown={(e) => {
                    e.preventDefault(); // keep focus in the textarea
                    onChoose(i);
                  }}
                  // Pointing at a command describes it; leaving goes back to
                  // the one the keyboard has selected.
                  onMouseEnter={() => onPoint(i)}
                  onMouseLeave={() => onPoint(null)}
                  className={cx(
                    "flex items-center gap-3 w-full h-(--row) px-3 snap-start rounded-1",
                    "text-ink text-2 cursor-pointer hover:bg-sheet-2 aria-selected:bg-sheet-2",
                  )}
                >
                  {/* Every row gets a drawn icon. A tool or skill the set has
                      no picture for takes the generic one. */}
                  <span className="text-ink-2">
                    <Icon name={hasIcon(row.id) ? row.id : "tool"} size={14} />
                  </span>
                  <span className={row.kind === "tool" ? "num" : undefined}>{row.cmd}</span>
                  <span className="sr-only">. {row.desc}</span>
                </div>
              </Fragment>
            ))}
          </div>
          {/* What the highlighted command does, in full, under the list. A
              description squeezed into each row was cut off mid-sentence on
              every one of them. */}
          <div
            data-desc
            aria-hidden="true"
            className={cx(
              "min-h-[calc(2*1.45em+var(--s-2)*2)] mt-1 py-2 px-3 border-t border-rule",
              "text-ink-2 text-1 leading-[1.45] text-pretty",
            )}
          >
            {rows[pointed ?? index]?.desc || ""}
          </div>
        </>
      )}
    </div>
  );
}
