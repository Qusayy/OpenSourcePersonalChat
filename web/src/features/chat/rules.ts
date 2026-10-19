/* The chat page's rules that are not about the DOM: what counts as a long
 * message, what a wait is called, which steps of a run are worth a line.
 * Pure, so each can be tested by value.
 */

import { fmt, fmtInt, fmtMs } from "@/lib/format";
import { hasIcon } from "@/lib/icons";
import type { ConfirmEvent, EngineInfo, LedgerStep, StepEvent, StoredRun } from "@/lib/types";

/* ------------------------------------------------------------- composer -- */

export const MAX_CHARS = 8000; // matches the server's Field(max_length=8000)

/** What the empty input says, given the server's condition. */
export function placeholderFor(info: Pick<EngineInfo, "status">): string {
  if (info.status === "ready") return "Ask something…";
  return info.status === "error"
    ? "The model could not be loaded — nothing can be sent right now"
    : "The model is still loading…";
}

export interface CharCount {
  /** Only speak up near the ceiling — a permanent counter is noise. */
  near: boolean;
  /** At the ceiling. */
  warn: boolean;
  text: string;
}

export function charCount(length: number): CharCount {
  const left = MAX_CHARS - length;
  return {
    near: left <= 500,
    warn: left <= 0,
    text: `${left.toLocaleString()} characters left`,
  };
}

/* ------------------------------------------------------------- messages -- */

// Past this a message is a pasted document, not a question, and is set as
// text and folded rather than as the heading of the exchange.
export const LONG_CHARS = 280;
export const LONG_LINES = 4;

export function isLongMessage(text: string): boolean {
  return text.length > LONG_CHARS || text.split("\n").length > LONG_LINES;
}

/** What the queue line says, and the short form for the status line. */
export function queueWording(position: number): { text: string; ahead: string } {
  const text =
    position > 1
      ? `${position} requests ahead of yours — this server runs one generation at a time.`
      : position === 1
        ? "One request ahead of yours — this server runs one generation at a time."
        : "Next in line — starting shortly.";
  return { text, ahead: position > 0 ? `${position} ahead` : "next in line" };
}

export function trimmedWording(dropped: number, cut = false): string {
  const said: string[] = [];
  if (dropped > 1) {
    said.push(`The ${dropped} oldest messages were dropped to fit the context window.`);
  } else if (dropped === 1) {
    said.push("The oldest message was dropped to fit the context window.");
  }
  if (cut) said.push("Your message was too long to read whole; the model read its first part.");
  return said.join(" ");
}

/* ---------------------------------------------------- calls under way -- */

/** A call under way, as the line under the reply draws it. */
export interface StepRow {
  id: string;
  /** An icon name the set is known to have. */
  icon: string;
  label: string;
}

/**
 * Apply one `step` event to the calls an answer is making.
 *
 * The line carries only what is said nowhere else. A reply of the model is
 * the status line's job while it runs and the footer's once it has. A
 * finished call is named, timed and qualified by its own figure. So a call
 * is drawn while it runs, and goes as soon as it has ended, however it
 * ended. Nothing else is drawn: there is no step that decides anything.
 */
export function applyStep(rows: readonly StepRow[], data: StepEvent): StepRow[] {
  if (data.kind !== "tool") return rows as StepRow[];
  if ((data.status || "running") !== "running") return rows.filter((row) => row.id !== data.id);
  const row: StepRow = {
    id: data.id,
    icon: data.tool && hasIcon(data.tool) ? data.tool : "tool",
    label: data.label || "",
  };
  return rows.some((r) => r.id === data.id)
    ? rows.map((r) => (r.id === data.id ? row : r))
    : [...rows, row];
}

/* ------------------------------------------------ a change put to you -- */

// What a change is, as the sentence that asks about it says it: "the agent
// wants to write app/main.py". A tool not named here is said by its label.
const CHANGE_VERBS: Record<string, string> = {
  write_file: "write",
  edit_file: "edit",
  run_file: "run",
  move_file: "move",
  delete_file: "delete",
};

// What was read, as the same sentence says it: "It read a web search first".
const READ_WORDS: Record<string, string> = {
  "Web search": "a web search",
  News: "the news",
  "Read page": "a web page",
  "Open result": "a page from a search",
  Wikipedia: "Wikipedia",
  Recall: "notes kept in other conversations",
};

/** A list said as a sentence says it: "a, b and c". */
function spoken(items: readonly string[]): string {
  if (items.length < 2) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** What a run read that somebody else wrote, from the tools' names on screen. */
export function readWords(because: string): string {
  const labels = because
    .split(",")
    .map((l) => l.trim())
    .filter(Boolean);
  // The server's own words, where it had no label to give: said as they are.
  const said = labels.map((l) => READ_WORDS[l] ?? (/^[a-z]/.test(l) ? l : `what ${l} returned`));
  return spoken([...new Set(said)]) || "a page";
}

export interface ConfirmWording {
  /** What it wants to do, set in bold: "write app/main.py", "run this program". */
  action: string;
  /** For a program, the start of its code, shown as code; null otherwise. */
  code: string | null;
  /** Why it is asking, and what to check. */
  why: string;
}

/** The words of a change put to the user, from the event that put it. */
export function confirmWording(
  c: Pick<ConfirmEvent, "tool" | "label" | "what" | "because">,
): ConfirmWording {
  const what = c.what.trim();
  const verb = CHANGE_VERBS[c.tool];
  const program = !verb && c.tool === "python";
  const action = program
    ? "run this program"
    : verb
      ? `${verb} ${what || "a file"}`
      : `use ${c.label || c.tool}${what ? ` on ${what}` : ""}`;
  return {
    action,
    code: program && what ? what : null,
    why: `It read ${readWords(c.because)} first, so check this is what you asked for and not something it read there.`,
  };
}

/** How a change put to you ended: what you chose, or why nothing was. */
export type ConfirmOutcome = "once" | "reply" | "no" | "no answer" | "stopped";

/** The one quiet line a change put to you becomes once it has been answered. */
export const OUTCOME_WORDS: Record<ConfirmOutcome, string> = {
  once: "Allowed.",
  reply: "Allowed for this reply.",
  no: "Not allowed.",
  "no answer": "No answer in time, so not allowed.",
  stopped: "Stopped before you answered.",
};

// The same, as "How it ran" says it after the tool's name.
const ANSWERED: Record<string, string> = {
  no: "not allowed",
  "no answer": "no answer",
  stopped: "stopped",
};

/** The outcome a `confirm` step reports, as one of the five. */
export function outcomeOf(data: Pick<StepEvent, "status" | "detail">): ConfirmOutcome {
  const said = data.detail ?? "";
  if (said === "once" || said === "reply" || said === "no" || said === "no answer") return said;
  if (data.status === "stopped" || said === "stopped") return "stopped";
  return data.status === "ok" ? "once" : "no";
}

/* ------------------------------------------------------- how it ran -- */

/** One row of "How it ran". */
export interface LedgerRow {
  /** What the step was, in a word or two. */
  kind: string;
  /** What it chose or called, in the page's own words. */
  name: string;
  /** "read 18 of 667 · wrote 12" for a pass through the model; empty otherwise. */
  tokens: string;
  ms: string;
  failed: boolean;
}

// What each kind of step is called. A reply that called a tool "chose" it;
// the last reply is the answer. The rest are the words of runs kept before
// the model simply replied, where a choice made without the model is named
// by how it was made.
const CHOSEN_BY: Record<string, string> = {
  slash: "command",
  heuristic: "pattern",
  chat: "small talk",
  skill: "skill",
};
const KIND_WORDS: Record<string, string> = {
  call: "chose",
  confirm: "asked you",
  tool: "tool",
  answer: "answer",
  route: "choice",
  decide: "choice again",
  repair: "sent back",
  compose: "wrote code",
  fill: "filled in",
};

function tokensOf(step: LedgerStep): string {
  if (step.tokens_in == null && step.tokens_out == null) return "";
  const said: string[] = [];
  if (step.tokens_in != null) {
    said.push(
      step.tokens_read != null && step.tokens_read !== step.tokens_in
        ? `read ${fmtInt(step.tokens_read)} of ${fmtInt(step.tokens_in)}`
        : `read ${fmtInt(step.tokens_in)}`,
    );
  }
  if (step.tokens_out != null) said.push(`wrote ${fmtInt(step.tokens_out)}`);
  return said.join(" · ");
}

/**
 * A run's ledger as the rows "How it ran" lists, in order, and the line that
 * splits its time. `label` names a tool in the page's words.
 */
export function ledgerRows(
  run: StoredRun,
  label: (name: string) => string,
): { rows: LedgerRow[]; split: string } {
  const rows = run.ledger.map((step) => {
    const free = step.kind === "route" && step.via && step.via !== "model";
    let name = "";
    if (step.kind !== "answer") {
      if (step.name === "ask") name = "asked you";
      else if (step.name) name = label(step.name);
      else name = "none";
    }
    // A change put to you did not fail when you said no: it says what you said.
    const asked = step.kind === "confirm";
    if (asked) name += ` · ${step.ok ? "allowed" : (ANSWERED[step.error ?? ""] ?? "not allowed")}`;
    return {
      kind: free
        ? (CHOSEN_BY[step.via as string] ?? "choice")
        : (KIND_WORDS[step.kind] ?? step.kind),
      name,
      tokens: tokensOf(step),
      ms: fmtMs(step.ms),
      failed: !asked && !step.ok,
    };
  });
  const split = [
    `run ${fmtMs(run.total_ms)}`,
    `waiting ${fmtMs(run.waiting_ms)}`,
    `reading ${fmtMs(run.reading_ms)}`,
    `writing ${fmtMs(run.writing_ms)}`,
    `tools ${fmtMs(run.tools_ms)}`,
  ].join(" · ");
  return { rows, split };
}

/* -------------------------------------------------- what an answer cost -- */

/** The figures an answer's footer is built from: a finished stream's `done`
 *  event, or a stored message, which kept fewer of them. */
export interface Cost {
  gen_tps?: number | null;
  ttft_ms?: number | null;
  completion_tokens?: number | null;
  total_ms?: number | null;
  cancelled?: boolean;
}

export interface CostFigure {
  /** The numeral, set fixed-width so a column of these lines up. */
  value: string;
  words: string;
}

export function costFigures(m: Cost): CostFigure[] {
  const has = (v: unknown): v is number => v !== null && v !== undefined;
  const figs: CostFigure[] = [];
  if (has(m.gen_tps)) figs.push({ value: fmt(m.gen_tps, 1), words: "tok/s" });
  if (has(m.ttft_ms)) figs.push({ value: fmtMs(m.ttft_ms), words: "to first token" });
  if (has(m.completion_tokens)) figs.push({ value: fmtInt(m.completion_tokens), words: "tokens" });
  if (has(m.total_ms)) figs.push({ value: fmtMs(m.total_ms), words: "to answer" });
  return figs;
}
