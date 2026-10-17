/* Visual answers.
 *
 * A tool returns structured data; these turn it into something worth looking
 * at. One component per kind of result. Each reads its payload through
 * fields.ts, so a field the tool did not send is simply absent from the
 * figure.
 */

import type { ComponentType } from "react";
import { ago, num, weekdayShort } from "@/lib/format";
import { Icon } from "@/lib/icons";
import { sparkPaths } from "@/lib/meter";
import type { CardData, CardKind } from "@/lib/types";
import { ANSWER, Figure, SOURCE, TITLE } from "./Figure";
import { list, record, text } from "./fields";
import type { ToolCardMeta } from "./ToolCard";
import { WeatherArt } from "./WeatherArt";

export interface KindProps {
  data: CardData;
  meta?: ToolCardMeta | null;
}

/* ---------------------------------------------------------- calculator -- */

// The result is printed exactly as the tool returned it. It used to count up
// from zero, which showed several wrong numbers on the way to an answer whose
// whole point is that it is exact.
function Calculator({ data, meta }: KindProps) {
  const fraction = text(data, "fraction");
  return (
    <Figure
      tool="calculator"
      card="calculator"
      name="Calculator"
      note={data.exact ? "exact" : "rounded"}
      meta={meta}
    >
      <div>
        <div className="text-ink-2 [overflow-wrap:anywhere] num">{text(data, "expression")}</div>
        <div className="flex items-baseline gap-3 mt-1">
          <span className="text-ink-2 text-5 num">=</span>
          <span className={`${ANSWER} [overflow-wrap:anywhere] num`}>{String(data.result)}</span>
        </div>
        {fraction ? <div className="mt-1 text-ink-2 text-1 num">{fraction}</div> : null}
      </div>
    </Figure>
  );
}

/* ------------------------------------------------------------- weather -- */

function Weather({ data, meta }: KindProps) {
  const hourly = list(data, "hourly").filter((v): v is number => typeof v === "number");
  const drawn = hourly.length > 2;
  const [line, fill] = sparkPaths(drawn ? hourly : [0, 0], 120, 38, { autoMin: true });
  const place = text(data, "place");
  const country = text(data, "country");

  return (
    <Figure
      tool="weather"
      card="weather"
      name="Weather"
      note={`${place}${country ? `, ${country}` : ""}`}
      meta={meta}
    >
      <div className="flex items-center gap-4">
        <WeatherArt condition={text(data, "icon")} />
        <div className="flex items-baseline gap-[2px]">
          <span className={`${ANSWER} num`}>{num(data.temperature, 1)}</span>
          <span className="text-ink-2">°C</span>
        </div>
        <div>
          <div>{text(data, "condition")}</div>
          <div className="text-ink-2 text-1">
            {"feels "}
            <span className="num">{`${num(data.feels_like, 1)}°`}</span>
            {" · wind "}
            <span className="num">{`${num(data.wind)} km/h`}</span>
            {" · humidity "}
            <span className="num">{`${num(data.humidity)}%`}</span>
          </div>
        </div>
      </div>
      {drawn ? (
        <>
          <svg
            className="w-spark block w-full h-[40px] mt-3"
            viewBox="0 0 120 38"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path className="w-area" d={fill} />
            <path className="w-line" d={line} />
          </svg>
          <div className="flex justify-between text-ink-2 text-1">
            <span>now</span>
            <span>next 24h</span>
          </div>
        </>
      ) : null}
      <div className="grid grid-cols-[repeat(5,1fr)] gap-2 mt-3 pt-3 border-t border-rule le-620:grid-cols-[repeat(3,1fr)]">
        {list(data, "days").map((entry, i) => {
          const day = record(entry);
          return (
            // biome-ignore lint/suspicious/noArrayIndexKey: a forecast is an ordered list that is never reordered
            <div key={i} className="flex flex-col items-center gap-[2px] text-1">
              <span className="text-ink-2">{weekdayShort(day.date as string)}</span>
              <WeatherArt condition={String(day.icon ?? "")} small />
              <span className="num">{`${num(day.max)}°`}</span>
              <span className="text-ink-2 num">{`${num(day.min)}°`}</span>
            </div>
          );
        })}
      </div>
    </Figure>
  );
}

/* ------------------------------------------------------ wikipedia / url -- */

function Wikipedia({ data, meta }: KindProps) {
  return (
    <Figure tool="wikipedia" card="wikipedia" name="Wikipedia" meta={meta}>
      <div className={TITLE}>{text(data, "title")}</div>
      <div className="mb-2 text-ink-2">{text(data, "description")}</div>
      <p className="my-2 line-clamp-5">{text(data, "extract")}</p>
      <a
        className={SOURCE}
        href={text(data, "url") || "#"}
        target="_blank"
        rel="noopener noreferrer"
      >
        en.wikipedia.org
        <Icon name="external" size={12} />
      </a>
    </Figure>
  );
}

/* ------------------------------------------------------ search / news -- */

/* A list the model was shown, numbered as it read it, so "the second one"
   means the same thing on the page as in the answer. Each entry links to its
   page; the age is worked out from when it was published, here and now,
   rather than taken from when it was looked up. */

interface Entry {
  title: string;
  url: string;
  source: string;
  published: string;
  snippet: string;
}

/** When an entry was published, as an age; empty when the date cannot be read. */
function published(iso: string): string {
  const at = Date.parse(iso);
  return Number.isNaN(at) ? "" : ago(at / 1000);
}

function entries(data: CardData): Entry[] {
  return list(data, "items").map((raw) => {
    const e = record(raw);
    return {
      title: text(e, "title") || text(e, "url"),
      url: text(e, "url"),
      source: text(e, "source"),
      published: text(e, "published"),
      snippet: text(e, "snippet"),
    };
  });
}

function Entries({ items, snippets }: { items: Entry[]; snippets: boolean }) {
  return (
    <ol className="m-0 p-0 list-none flex flex-col gap-3">
      {items.map((e, i) => (
        <li key={e.url || i} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-1">
          <span className="text-ink-2 num">{i + 1}.</span>
          <div className="min-w-0">
            <a
              className="font-semibold [overflow-wrap:anywhere]"
              href={e.url || "#"}
              target="_blank"
              rel="noopener noreferrer"
            >
              {e.title}
            </a>
            <div className="text-ink-2 text-1">
              {[e.source, published(e.published)].filter(Boolean).join(" · ")}
            </div>
            {snippets && e.snippet ? <p className="mt-1 mb-0 line-clamp-2">{e.snippet}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Search({ data, meta }: KindProps) {
  const answer = text(data, "answer");
  const query = text(data, "query");
  return (
    <Figure
      tool="search"
      card="search"
      name="Web search"
      note={query ? `“${query}”` : undefined}
      meta={meta}
    >
      {answer ? <p className="mt-0 mb-3">{answer}</p> : null}
      <Entries items={entries(data)} snippets />
    </Figure>
  );
}

function News({ data, meta }: KindProps) {
  const topic = text(data, "topic");
  return (
    <Figure tool="news" card="news" name="News" note={topic || "top stories"} meta={meta}>
      <Entries items={entries(data)} snippets={false} />
    </Figure>
  );
}

/* -------------------------------------------------------------- python -- */

/* The program as it was run, then what it printed. A program the model wrote
   is shown whole, so what was run is never a guess; a failure keeps the end
   of its traceback, where Python says what went wrong. */
function Python({ data, meta }: KindProps) {
  const code = text(data, "code");
  const out = text(data, "stdout");
  const err = text(data, "stderr");
  const exit = data.exit_code;
  const timedOut = data.timed_out === true;
  const failed = timedOut || (typeof exit === "number" && exit !== 0);
  const note = timedOut
    ? "stopped: out of time"
    : typeof exit === "number"
      ? exit === 0
        ? "exited 0"
        : `exited ${exit}`
      : undefined;
  return (
    <Figure tool="python" card="python" name="Python" note={note} meta={meta} failed={failed}>
      {code ? (
        <div className="codeblock" data-code={code}>
          <header>
            <span>python</span>
            <button type="button" className="copy-btn" data-copy>
              Copy
            </button>
          </header>
          <pre>
            <code>{code}</code>
          </pre>
        </div>
      ) : null}
      {out ? (
        <pre className="mt-2 mb-0 text-1 whitespace-pre-wrap [overflow-wrap:anywhere]">{out}</pre>
      ) : !failed ? (
        <p className="mt-2 mb-0 text-ink-2">It printed nothing.</p>
      ) : null}
      {failed && err ? (
        <pre className="mt-2 mb-0 text-1 text-fault whitespace-pre-wrap [overflow-wrap:anywhere]">
          {err.length > 1200 ? `…${err.slice(-1200)}` : err}
        </pre>
      ) : null}
      {data.truncated ? (
        <p className="mt-1 mb-0 text-ink-2 text-1">The output was cut off.</p>
      ) : null}
    </Figure>
  );
}

/* ---------------------------------------------------------------- note -- */

// A note kept, or the notes found. The words are the user's own, set as
// they were kept; whose notes they are is the caption's qualifier.
function Note({ data, meta }: KindProps) {
  const kept = text(data, "action") === "remember";
  const notes = list(data, "notes").map((n) => String(n ?? ""));
  const whose = text(data, "scope") === "site" ? "this site" : "this conversation";
  const query = text(data, "query");
  return (
    <Figure
      tool="note"
      card="note"
      name={kept ? "Remember" : "Recall"}
      note={kept ? `kept for ${whose}` : query ? `“${query}”` : whose}
      meta={meta}
    >
      {kept ? (
        <p className="my-1">{text(data, "text")}</p>
      ) : notes.length > 0 ? (
        <ul className="my-1 pl-5 list-disc">
          {notes.map((n, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: two notes may say the same thing
            <li key={i}>{n}</li>
          ))}
        </ul>
      ) : (
        <p className="my-1 text-ink-2">
          {query ? `No note matches “${query}”.` : "There are no notes yet."}
        </p>
      )}
    </Figure>
  );
}

function PageRead({ data, meta }: KindProps) {
  return (
    <Figure tool="url" card="url" name="Page read" note={text(data, "domain")} meta={meta}>
      <div className={TITLE}>{text(data, "title")}</div>
      <p className="my-2">{text(data, "lede")}</p>
      <div className="flex flex-wrap items-center gap-y-1 gap-x-4 text-ink-2 text-1">
        <span>
          <span className="num">{num(data.words)}</span>
          {" words · about "}
          <span className="num">{num(data.reading_minutes, 1)}</span>
          {` min read${data.truncated ? " · truncated" : ""}`}
        </span>
        <a
          className={`${SOURCE} ml-auto`}
          href={text(data, "url") || "#"}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open the page
          <Icon name="external" size={12} />
        </a>
      </div>
    </Figure>
  );
}

/* ------------------------------------------------ currency / conversion -- */

/* Read left to right like the sentence it stands for: this much of that
   becomes this much of the other. The converted side carries the weight. */

const EXCHANGE = "flex flex-wrap items-baseline gap-y-2 gap-x-4";
const SIDE = "inline-flex items-baseline gap-2";
const GIVEN = "text-ink-2 text-5 num";
const CODE = "text-ink-2 text-1";
const ARROW = "inline-flex items-center gap-2 self-center text-ink-2 text-1";

function Currency({ data, meta }: KindProps) {
  return (
    <Figure
      tool="currency"
      card="currency"
      name="Currency"
      note={`ECB ${text(data, "date")}`}
      meta={meta}
    >
      <div className={EXCHANGE}>
        <div className={SIDE}>
          <span className={GIVEN}>{num(data.amount, 2)}</span>
          <span className={CODE}>{text(data, "from")}</span>
        </div>
        <div className={ARROW}>
          <Icon name="arrow" size={16} />
          <span className="num">{`×${num(data.rate, 4)}`}</span>
        </div>
        <div className={SIDE}>
          <span className={`${ANSWER} num`}>{num(data.result, 2)}</span>
          <span className={CODE}>{text(data, "to")}</span>
        </div>
      </div>
    </Figure>
  );
}

function Convert({ data, meta }: KindProps) {
  return (
    <Figure tool="convert" card="convert" name="Convert" note={text(data, "dimension")} meta={meta}>
      <div className={EXCHANGE}>
        <div className={SIDE}>
          <span className={GIVEN}>{num(data.value, 4).replace(/\.?0+$/, "")}</span>
          <span className={CODE}>{text(data, "from")}</span>
        </div>
        <div className={ARROW}>
          <Icon name="arrow" size={16} />
        </div>
        <div className={SIDE}>
          <span className={`${ANSWER} num`}>{text(data, "result_text")}</span>
          <span className={CODE}>{text(data, "to")}</span>
        </div>
      </div>
    </Figure>
  );
}

/* --------------------------------------------------------------- clock -- */

const TICKS = Array.from({ length: 12 }, (_, i) => {
  const a = (i * Math.PI) / 6;
  return {
    x1: (40 + Math.sin(a) * 30).toFixed(1),
    y1: (40 - Math.cos(a) * 30).toFixed(1),
    x2: (40 + Math.sin(a) * 34).toFixed(1),
    y2: (40 - Math.cos(a) * 34).toFixed(1),
  };
});

function Clock({ data, meta }: KindProps) {
  const [hh = 0, mm = 0] = (text(data, "time") || "00:00").split(":").map(Number);
  const minuteAngle = mm * 6;
  const hourAngle = ((hh % 12) + mm / 60) * 30;
  const note = text(data, "note");

  return (
    <Figure tool="clock" card="clock" name="Clock" note={text(data, "zone") || "UTC"} meta={meta}>
      <div className="flex items-center gap-4">
        <svg className="face flex-none size-[76px]" viewBox="0 0 80 80" aria-hidden="true">
          <circle className="rim" cx="40" cy="40" r="36" />
          <g className="ticks">
            {TICKS.map((t) => (
              <line key={`${t.x1} ${t.y1}`} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} />
            ))}
          </g>
          <line
            className="hand hour"
            x1="40"
            y1="40"
            x2="40"
            y2="22"
            transform={`rotate(${hourAngle.toFixed(1)} 40 40)`}
          />
          <line
            className="hand minute"
            x1="40"
            y1="40"
            x2="40"
            y2="14"
            transform={`rotate(${minuteAngle.toFixed(1)} 40 40)`}
          />
          <circle className="pin" cx="40" cy="40" r="2.4" />
        </svg>
        <div>
          <div className={`${ANSWER} num`}>{text(data, "time")}</div>
          <div className="mb-1 text-ink-2">{text(data, "date")}</div>
          <div className="flex flex-wrap gap-y-1 gap-x-4 text-ink-2 text-1">
            {list(data, "others").map((entry, i) => {
              const other = record(entry);
              // "Asia/Ho_Chi_Minh" is read as "Ho Chi Minh".
              const city = String(other.zone ?? "")
                .split("/")
                .pop()
                ?.replace(/_/g, " ");
              return (
                // biome-ignore lint/suspicious/noArrayIndexKey: the zones are a fixed, ordered list
                <div key={i}>
                  <span>{city}</span>
                  <b className="ml-1 text-ink font-[400] num">{String(other.time ?? "")}</b>
                </div>
              );
            })}
          </div>
          {note ? <div className="mt-1 text-caution text-1">{note}</div> : null}
        </div>
      </div>
    </Figure>
  );
}

/* --------------------------------------------------------------- stats -- */

function TextStats({ data, meta }: KindProps) {
  const terms = list(data, "top_terms").map(record);
  const max = Math.max(...terms.map((t) => Number(t.count)), 1);
  const STAT = "flex flex-col";
  const FIGURE = "text-4 num";
  const LABEL = "text-ink-2 text-1";

  return (
    <Figure tool="textstats" card="textstats" name="Text stats" meta={meta}>
      <div className="grid grid-cols-[repeat(4,1fr)] gap-3 mb-4 le-620:grid-cols-[repeat(2,1fr)]">
        <div className={STAT}>
          <b className={FIGURE}>{num(data.words)}</b>
          <span className={LABEL}>words</span>
        </div>
        <div className={STAT}>
          <b className={FIGURE}>{num(data.sentences)}</b>
          <span className={LABEL}>sentences</span>
        </div>
        <div className={STAT}>
          <b className={FIGURE}>{num(data.reading_minutes, 1)}</b>
          <span className={LABEL}>min read</span>
        </div>
        <div className={STAT}>
          <b className={FIGURE}>{num(data.avg_sentence_words, 1)}</b>
          <span className={LABEL}>words per sentence</span>
        </div>
      </div>
      <div>
        {terms.map((term, i) => (
          <div
            // biome-ignore lint/suspicious/noArrayIndexKey: the terms are ranked, and two may be the same word
            key={i}
            className="grid grid-cols-[7rem_minmax(0,1fr)_2rem] items-center gap-2 mb-1 text-1"
          >
            <span className="overflow-hidden text-ellipsis">{String(term.term ?? "")}</span>
            <span className="h-[6px] bg-sheet-2">
              <i
                className="block h-full bg-ink-2"
                style={{ width: `${((Number(term.count) / max) * 100).toFixed(1)}%` }}
              />
            </span>
            <span className="text-ink-2 text-right num">{String(term.count ?? "")}</span>
          </div>
        ))}
      </div>
    </Figure>
  );
}

/* -------------------------------------------------------------- failure -- */

/** A failed tool is shown failing. The mark and the caption say so; the
 *  message is ordinary text, because it is there to be read.
 *
 *  Named for the tool that failed when that is known, so the line reads
 *  "Weather failed" rather than leaving the reader to work out which. */
export function Failure({ data, meta, bare }: KindProps & { bare?: boolean }) {
  return (
    <Figure
      tool="error"
      // `bare`: standing in for a card that could not be drawn, which is not
      // the same as a tool that reported its own failure.
      card={bare ? undefined : "error"}
      name={meta?.label ? `${meta.label} failed` : "Tool failed"}
      meta={meta}
      failed
    >
      <p className="m-0">{text(data, "message") || "Something went wrong."}</p>
    </Figure>
  );
}

export const KINDS: Record<CardKind, ComponentType<KindProps>> = {
  calculator: Calculator,
  weather: Weather,
  wikipedia: Wikipedia,
  url: PageRead,
  currency: Currency,
  convert: Convert,
  clock: Clock,
  textstats: TextStats,
  note: Note,
  search: Search,
  news: News,
  python: Python,
  error: Failure,
};
