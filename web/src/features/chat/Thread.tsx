import { Fragment, memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { CardStack } from "@/features/cards/CardStack";
import { ToolCard } from "@/features/cards/ToolCard";
import { copyText } from "@/lib/clipboard";
import { cx } from "@/lib/cx";
import { enter } from "@/lib/frame";
import { Icon } from "@/lib/icons";
import { renderMarkdown } from "@/lib/markdown";
import { Button } from "@/shell/Button";
import { toast } from "@/state/shell";
import { dom, send, streamed } from "./controller";
import { costFigures, isLongMessage, type StepRow } from "./rules";
import {
  type BotItem,
  type NoticeItem,
  questionBefore,
  removeItem,
  type UserItem,
  useChat,
} from "./run";

/* The thread: questions, the answers under them, and the notices between.

   A question heads its exchange and an answer is set as text; neither is a
   bubble. Everything here is drawn from the store except one thing: the text
   of an answer, which the streaming renderer writes while it arrives and
   which is otherwise set once from the stored markdown. React is given that
   element empty and never looks inside it. */

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

const Answer = memo(function Answer({ item }: { item: BotItem }) {
  return (
    <div data-part="answer" className={cx(ARRIVES, "mt-3")}>
      <div>
        {item.steps.length > 0 && <RouteLine steps={item.steps} />}
        {/* A tool result is set as a figure above the answer it informed. */}
        {item.cards.length > 0 && (
          <CardStack data-part="cards">
            {item.cards.map((entry, i) => (
              <ToolCard
                key={entry.key}
                card={entry.card}
                data={entry.data}
                index={i}
                meta={entry.meta}
              />
            ))}
          </CardStack>
        )}
        <Prose itemKey={item.key} live={item.live} content={item.content} />
        {item.cost && <CostLine item={item} />}
      </div>
    </div>
  );
});

/**
 * The answer's text. While it is being written the streaming renderer owns
 * this element: settled blocks are appended once, the block being written is
 * rebuilt every tenth of a second, and what it leaves when the answer ends is
 * what stays. An element the renderer never wrote to (a stored conversation,
 * or the page come back to) is drawn once from the answer's markdown.
 */
const Prose = memo(function Prose({
  itemKey,
  live,
  content,
}: {
  itemKey: number;
  live: boolean;
  content: string;
}) {
  const host = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = host.current;
    if (!el) return;
    dom.prose.set(itemKey, el);
    return () => {
      if (dom.prose.get(itemKey) === el) dom.prose.delete(itemKey);
    };
  }, [itemKey]);

  useLayoutEffect(() => {
    const el = host.current;
    if (!el || live || streamed.has(el)) return;
    // The renderer escapes everything it is given before it adds markup.
    el.innerHTML = renderMarkdown(content);
  }, [live, content]);

  return <div className="prose" dir="auto" ref={host} />;
});

/* ----------------------------------------------------------- the route -- */

/**
 * How the answer was reached, as one line of text: each step, and how long it
 * took. Steps are separated by a hairline, not boxed.
 */
function RouteLine({ steps }: { steps: StepRow[] }) {
  return (
    <div data-part="route" className="flex flex-wrap gap-y-1 gap-x-4 mb-3 text-ink-2 text-1">
      {steps.map((step) => (
        <Step key={step.id} step={step} />
      ))}
    </div>
  );
}

function Step({ step }: { step: StepRow }) {
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (row.current) enter(row.current, { distance: 6 });
  }, []);
  // The step that is running is the live thing in this line.
  const running = step.status === "running";
  return (
    <div
      ref={row}
      data-id={step.id}
      data-status={step.status}
      className={cx(
        "inline-flex items-center gap-[0.5em] min-w-0",
        "[&+&]:pl-4 [&+&]:border-l [&+&]:border-rule",
      )}
    >
      <span className={running ? "text-live" : undefined}>
        <Icon name={step.icon} size={14} />
      </span>
      {step.label && (
        <span className="text-ink overflow-hidden text-ellipsis whitespace-nowrap">
          {step.label}
        </span>
      )}
      {/* Said in a word as well as a colour. */}
      {step.state && <span className="text-live">{step.state}</span>}
      {step.ms && <span className="num">{step.ms}</span>}
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
  const cost = item.cost;
  if (!cost) return null;

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
        <Button variant="text" small quiet data-act="copy" onClick={copy}>
          Copy
        </Button>
        <Button variant="text" small quiet data-act="again" onClick={again}>
          Ask again
        </Button>
      </span>
    </div>
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
