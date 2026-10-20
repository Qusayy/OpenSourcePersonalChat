import { Fragment, memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ToolCard } from "@/features/cards/ToolCard";
import { useProject } from "@/features/project/store";
import { copyText } from "@/lib/clipboard";
import { cx } from "@/lib/cx";
import { enter } from "@/lib/frame";
import { Icon } from "@/lib/icons";
import { renderAnswer } from "@/lib/markdown";
import { Button, buttonClass } from "@/shell/Button";
import { toast } from "@/state/shell";
import { answerConfirm, dom, send, streamed, toolLabel } from "./controller";
import {
  confirmWording,
  costFigures,
  isLongMessage,
  ledgerRows,
  OUTCOME_WORDS,
  type StepRow,
} from "./rules";
import {
  type BotItem,
  type CardPart,
  type ConfirmPart,
  type NoticeItem,
  type Part,
  questionBefore,
  removeItem,
  type ThoughtPart,
  type UserItem,
  useChat,
} from "./run";

/* The thread: questions, the answers under them, and the notices between.

   A question heads its exchange and an answer is set as text; neither is a
   bubble. Everything here is drawn from the store except one thing: the text
   of the part of an answer being written, what the model is saying or
   thinking, which the controller writes while it arrives and which is
   otherwise set once from the stored text. React is given that element empty
   and never looks inside it. */

// An arrival: from just below, out of nothing. Under reduced motion it is
// simply there.
const ARRIVES = "animate-msg-in motion-reduce:animate-none";

export function Thread() {
  const items = useChat((s) => s.items);
  return (
    <>
      {items.map((item) =>
        item.kind === "user" ? (
          <Question key={item.key} item={item} />
        ) : item.kind === "bot" ? (
          <Answer key={item.key} item={item} />
        ) : (
          <Notice key={item.key} item={item} />
        ),
      )}
    </>
  );
}

/* ------------------------------------------------------------ question -- */

/**
 * The question heads its exchange: more room above it than below, like any
 * heading, and no bubble — weight and size already say whose line it is.
 * A pasted page is not a heading. It is set as text and folded until asked for.
 */
const Question = memo(function Question({ item }: { item: UserItem }) {
  const long = isLongMessage(item.text);
  const [expanded, setExpanded] = useState(false);
  const lines = item.text.split("\n");
  return (
    <div
      data-part="question"
      data-long={long || undefined}
      className={cx(ARRIVES, "not-first:mt-7 le-620:not-first:mt-6")}
    >
      {/* dir="auto" so Arabic or Hebrew input lays out right-to-left on its own. */}
      <div
        dir="auto"
        // A pasted URL is one unbroken word; without `anywhere` it pushes the
        // column wider than the viewport instead of wrapping.
        className={cx(
          "[overflow-wrap:anywhere] text-pretty",
          long
            ? cx(
                "text-3 font-medium leading-[1.5] tracking-[0]",
                expanded ? "overflow-hidden" : "line-clamp-5",
              )
            : "text-4 font-semibold leading-[1.35] tracking-[-0.01em]",
        )}
      >
        {lines.map((line, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: lines of one fixed text
          <Fragment key={i}>
            {i > 0 && <br />}
            {line}
          </Fragment>
        ))}
      </div>
      {long && (
        <Button
          variant="text"
          small
          quiet
          className="mt-1 -ml-2"
          aria-expanded={expanded}
          onClick={() => setExpanded((was) => !was)}
        >
          {expanded ? "Show less" : "Show all"}
        </Button>
      )}
    </div>
  );
});

/* -------------------------------------------------------------- answer -- */

/** A run of parts as the answer sets them: cards that came one after another
 *  stand together between two hairlines; anything else stands alone. */
type Block = { kind: "cards"; key: number; cards: CardPart[] } | Exclude<Part, CardPart>;

function blocks(parts: readonly Part[]): Block[] {
  const out: Block[] = [];
  for (const part of parts) {
    if (part.kind !== "card") {
      out.push(part);
      continue;
    }
    const last = out.at(-1);
    if (last?.kind === "cards") last.cards.push(part);
    else out.push({ kind: "cards", key: part.key, cards: [part] });
  }
  return out;
}

/**
 * An answer reads in the order it was written, as the agent wrote it: what
 * it thought, folded away; what it said; the result of each call it made,
 * where it made it; what it said after that. A call under way is drawn
 * after the last of it, where its result will come. Then, once it has
 * ended, what nothing it looked up returned, and what it cost.
 */
const Answer = memo(function Answer({ item }: { item: BotItem }) {
  return (
    <div data-part="answer" className={cx(ARRIVES, "mt-3")}>
      <div className="flex flex-col gap-4">
        {blocks(item.parts).map((block) =>
          block.kind === "cards" ? (
            <div key={block.key} data-part="cards" className="border-b border-rule">
              {block.cards.map((card, i) => (
                <ToolCard
                  key={card.key}
                  card={card.card}
                  data={card.data}
                  index={i}
                  meta={card.meta}
                />
              ))}
            </div>
          ) : block.kind === "text" ? (
            <Prose
              key={block.key}
              partKey={block.key}
              live={item.live}
              content={block.text}
              fold={block.fold === true}
            />
          ) : block.kind === "thought" ? (
            <Thought key={block.key} part={block} live={item.live} />
          ) : block.kind === "confirm" ? (
            <Confirm key={block.key} part={block} botKey={item.key} />
          ) : (
            <WritingFile key={block.key} path={block.path} />
          ),
        )}
        {item.steps.length > 0 && <Calls steps={item.steps} />}
      </div>
      {item.correction && <Correction text={item.correction} />}
      {item.cost && <CostLine item={item} />}
    </div>
  );
});

/** Give the controller the element a written part is set in, for as long as
 *  it is on the page. */
function usePartHost(partKey: number) {
  const host = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = host.current;
    if (!el) return;
    dom.parts.set(partKey, el);
    return () => {
      if (dom.parts.get(partKey) === el) dom.parts.delete(partKey);
    };
  }, [partKey]);
  return host;
}

/**
 * Something the answer said. While it is being written the streaming
 * renderer owns this element: settled blocks are appended once, the block
 * being written is rebuilt every tenth of a second, and what it leaves when
 * the part ends is what stays. An element the renderer never wrote to (a
 * stored conversation, or the page come back to) is drawn once from the
 * part's markdown; with `fold`, an answer kept from before files were
 * written with calls, the files it wrote out are drawn as chips.
 */
const Prose = memo(function Prose({
  partKey,
  live,
  content,
  fold,
}: {
  partKey: number;
  live: boolean;
  content: string;
  fold: boolean;
}) {
  const host = usePartHost(partKey);

  useLayoutEffect(() => {
    const el = host.current;
    if (!el || live || streamed.has(el)) return;
    // The renderer escapes everything it is given before it adds markup.
    el.innerHTML = renderAnswer(content, { files: fold });
  }, [host, live, content, fold]);

  return <div className="prose" dir="auto" ref={host} />;
});

/**
 * What the model thought before it said anything: folded away under one
 * quiet line, and there to be read when asked for. Plain text, set by the
 * controller while it is being thought and from the stored text otherwise.
 */
const Thought = memo(function Thought({ part, live }: { part: ThoughtPart; live: boolean }) {
  const body = usePartHost(part.key);

  useLayoutEffect(() => {
    const el = body.current;
    if (!el || live || streamed.has(el)) return;
    el.textContent = part.text.trim();
  }, [body, live, part.text]);

  const said =
    part.ms !== null
      ? `Thought for ${Math.max(1, Math.round(part.ms / 1000))} s`
      : live
        ? "Thinking…"
        : "Thought";
  return (
    <details data-part="thought" className="group text-ink-2">
      <summary
        className={cx(
          buttonClass({ variant: "text", quiet: true, small: true }),
          "-ml-2 w-fit list-none [&::-webkit-details-marker]:hidden",
        )}
      >
        <span className="inline-flex transition-transform duration-(--dur-1) group-open:rotate-90 motion-reduce:transition-none">
          <Icon name="arrow" size={12} />
        </span>
        {said}
      </summary>
      <div
        ref={body}
        dir="auto"
        className="mt-2 pl-4 border-l border-rule text-2 whitespace-pre-wrap [overflow-wrap:anywhere]"
      />
    </details>
  );
});

/**
 * A file the model is writing as it replies. It is a file chip, as the
 * project draws it, and it says how far the writing has got; when the call
 * that writes it has been made, the call's own card takes its place.
 */
function WritingFile({ path }: { path: string }) {
  const file = useProject((s) => s.files.find((f) => f.path === path));
  const lines = file?.lines ?? 0;
  const count = `${lines} line${lines === 1 ? "" : "s"}`;
  const writing = file?.writing ?? true;
  return (
    <button
      type="button"
      className="file-chip my-0"
      data-part="file"
      data-file={path}
      data-state={writing ? "writing" : "done"}
    >
      <span className="file-chip-name">{path}</span>
      <span className="file-chip-meta num">{writing ? `writing · ${count}` : count}</span>
    </button>
  );
}

/* -------------------------------------------------- a change put to you -- */

/**
 * The run has read text somebody else wrote, and the call it is about to make
 * changes the project or runs code: it is put to the owner first. A ruled
 * note with the caution mark, saying what it wants to do and why it asks,
 * then three word buttons. It is the one thing on the page that needs the
 * owner, so focus goes to it as it arrives. Answered, or ended, it becomes
 * one quiet line saying how; the call's card, after it, says what came of it.
 */
const Confirm = memo(function Confirm({ part, botKey }: { part: ConfirmPart; botKey: number }) {
  const box = useRef<HTMLDivElement>(null);
  // Only as it arrives, still waiting: not when a settled one is drawn again.
  const arrived = useRef(part.outcome === null);
  useEffect(() => {
    if (arrived.current) box.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, []);

  if (part.outcome !== null) {
    return (
      <p data-part="confirm" data-outcome={part.outcome} className="m-0 text-ink-2 text-1">
        {OUTCOME_WORDS[part.outcome]}
      </p>
    );
  }
  const words = confirmWording(part.ask);
  const title = `confirm-${part.key}`;
  const busy = part.chosen !== null;
  const choose = (choice: NonNullable<ConfirmPart["chosen"]>) =>
    void answerConfirm(botKey, part.key, choice);
  return (
    <div
      ref={box}
      role="group"
      aria-labelledby={title}
      aria-busy={busy || undefined}
      data-part="confirm"
      className="py-3 border-y border-rule text-2"
    >
      <p
        id={title}
        className="flex items-start gap-2 m-0 [&>.icon]:flex-none [&>.icon]:mt-[3px] [&>.icon]:text-caution"
      >
        <Icon name="error" size={16} />
        <span>
          {"Before it goes on, the agent wants to "}
          <b className="font-semibold">{words.action}</b>
          {words.code ? ":" : `. ${words.why}`}
        </span>
      </p>
      {words.code ? (
        <div className="ml-6">
          <div className="codeblock mt-2 mb-0">
            <header>
              <span>python</span>
            </header>
            <pre>
              <code>{words.code}</code>
            </pre>
          </div>
          <p className="mt-2 mb-0">{words.why}</p>
        </div>
      ) : null}
      {/* Word buttons, their words lined up with the note's. */}
      <div className="flex flex-wrap gap-x-1 gap-y-1 mt-2 ml-4">
        {(
          [
            ["once", "Allow"],
            ["reply", "Allow for this reply"],
            ["no", "Don't allow"],
          ] as const
        ).map(([choice, said]) => (
          <Button
            key={choice}
            variant="text"
            data-choice={choice}
            data-chosen={part.chosen === choice || undefined}
            disabled={busy}
            onClick={() => choose(choice)}
          >
            {said}
          </Button>
        ))}
      </div>
    </div>
  );
});

/* ------------------------------------------------------ calls under way -- */

/**
 * The calls being made right now, as one line of text: each tool, in the
 * live colour, with the word "running". A call goes from here as soon as it
 * has ended; its card says the rest.
 */
function Calls({ steps }: { steps: StepRow[] }) {
  return (
    <div data-part="calls" className="flex flex-wrap gap-y-1 gap-x-4 text-ink-2 text-1">
      {steps.map((step) => (
        <Call key={step.id} step={step} />
      ))}
    </div>
  );
}

function Call({ step }: { step: StepRow }) {
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (row.current) enter(row.current, { distance: 6 });
  }, []);
  return (
    <div
      ref={row}
      data-id={step.id}
      data-status="running"
      className={cx(
        "inline-flex items-center gap-[0.5em] min-w-0",
        "[&+&]:pl-4 [&+&]:border-l [&+&]:border-rule",
      )}
    >
      <span className="text-live">
        <Icon name={step.icon} size={14} />
      </span>
      {step.label && (
        <span className="text-ink overflow-hidden text-ellipsis whitespace-nowrap">
          {step.label}
        </span>
      )}
      {/* Said in a word as well as a colour. */}
      <span className="text-live">running</span>
    </div>
  );
}

/* ------------------------------------------------- what the answer cost -- */

/**
 * What this answer cost, and what can be done with it. The figures wrap among
 * themselves and the two actions stay together, so a narrow screen never
 * strands one figure on a line with the buttons. The actions are always
 * there, in words: an action that only appears on hover does not exist on a
 * phone, and an unlabelled glyph has to be guessed at.
 */
function CostLine({ item }: { item: BotItem }) {
  const [open, setOpen] = useState(false);
  const cost = item.cost;
  if (!cost) return null;
  const run = item.run && item.run.ledger.length > 0 ? item.run : null;

  const copy = () => {
    void copyText(item.content).then((copied) =>
      copied ? toast("Answer copied") : toast("Clipboard blocked by the browser", "err"),
    );
  };
  const again = () => {
    if (useChat.getState().streaming) return;
    const question = questionBefore(item.key);
    if (question) void send(question);
  };

  return (
    <>
      <div
        data-part="cost"
        className="flex flex-wrap items-center gap-y-1 gap-x-4 mt-3 text-ink-2 text-1"
      >
        <span className="flex flex-wrap gap-y-1 gap-x-4">
          {/* The numeral is fixed-width so a column of these lines up; the words
            around it are words and are set as words. */}
          {costFigures(cost).map((fig) => (
            <span key={fig.words}>
              <b className="num text-ink font-[400]">{fig.value}</b>
              {` ${fig.words}`}
            </span>
          ))}
          {cost.cancelled && <span>stopped</span>}
        </span>
        <span className="flex gap-1 ml-auto">
          {run && (
            <Button
              variant="text"
              small
              quiet
              data-act="ledger"
              aria-expanded={open}
              onClick={() => setOpen((was) => !was)}
            >
              How it ran
            </Button>
          )}
          <Button variant="text" small quiet data-act="copy" onClick={copy}>
            Copy
          </Button>
          <Button variant="text" small quiet data-act="again" onClick={again}>
            Ask again
          </Button>
        </span>
      </div>
      {run && open && <Ledger run={run} />}
    </>
  );
}

/**
 * How the answer was arrived at: every step, what it read and wrote, and how
 * long it took, then the run's time split four ways. The one place a step's
 * cost is itemised, so it is shown only when asked for.
 */
function Ledger({ run }: { run: NonNullable<BotItem["run"]> }) {
  const { rows, split } = ledgerRows(run, toolLabel);
  return (
    <div data-part="ledger" className="mt-2 text-ink-2 text-1">
      <ol className="grid grid-cols-[auto_minmax(0,1fr)_auto_auto] gap-x-4 gap-y-1">
        {rows.map((row, i) => (
          <li
            // biome-ignore lint/suspicious/noArrayIndexKey: the steps are in order, and two may be alike
            key={i}
            className="contents"
            data-failed={row.failed || undefined}
          >
            <span>{row.kind}</span>
            <span className="text-ink overflow-hidden text-ellipsis whitespace-nowrap">
              {row.name}
              {row.failed && <span className="text-fault"> failed</span>}
            </span>
            <span className="num">{row.tokens}</span>
            <span className="num text-right">{row.ms}</span>
          </li>
        ))}
      </ol>
      <p className="mt-2 num">{split}</p>
    </div>
  );
}

/**
 * What the answer said that nothing it looked up returned. A caption, not a
 * notice: it belongs to this answer, stays with it on reload, and is set in
 * the caption's size and ink, with the caution mark to say what kind of line
 * it is.
 */
function Correction({ text }: { text: string }) {
  return (
    <p
      data-part="correction"
      className="flex items-start gap-2 mt-3 text-ink-2 text-1 [&>.icon]:mt-[2px] [&>.icon]:text-caution"
    >
      <Icon name="error" size={14} />
      <span>{text}</span>
    </p>
  );
}

/* -------------------------------------------------------------- notice -- */

/** A line in the thread, with a mark that says what kind. */
function Notice({ item }: { item: NoticeItem }) {
  // An error the user can act on: says what broke and offers the way back.
  if (item.variant === "err") {
    const retry = item.retry;
    return (
      <div
        data-part="notice"
        data-kind="err"
        className={cx(
          "flex items-start gap-2 mt-4 py-3 px-4 rounded-1 bg-fault-tint text-ink text-2",
          "transition-[opacity] duration-(--dur-1)",
          "[&>.icon]:mt-[3px] [&>.icon]:text-fault",
        )}
      >
        <Icon name="error" size={16} />
        <span>{item.text}</span>
        {retry && (
          <Button
            className="ml-auto flex-none"
            onClick={() => {
              removeItem(item.key);
              void send(retry);
            }}
          >
            Try again
          </Button>
        )}
      </div>
    );
  }
  const queue = item.variant === "queue";
  return (
    <div
      data-part="notice"
      data-kind={queue ? "queue" : "note"}
      // The queue line is answering "when do I start?", so when the answer
      // arrives it should resolve rather than vanish.
      data-leaving={item.leaving || undefined}
      className={cx(
        "flex items-center gap-2 mt-4 text-2 transition-[opacity] duration-(--dur-1)",
        queue ? "text-caution" : "text-ink-2",
        item.leaving && "opacity-0",
      )}
    >
      {queue && (
        <span
          className={cx(
            "size-[12px] flex-none rounded-[50%] border-2",
            "border-[color-mix(in_oklch,currentColor_30%,transparent)] border-t-[currentColor]",
            "animate-spin motion-reduce:animate-none",
          )}
        />
      )}
      <span>{item.text}</span>
    </div>
  );
}
