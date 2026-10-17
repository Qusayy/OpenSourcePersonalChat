/* The chat page's rules that are not about the DOM: what counts as a long
 * message, what a wait is called, which steps of an answer's route are worth
 * a line. Pure, so each can be tested by value.
 */

import { fmt, fmtInt, fmtMs } from "@/lib/format";
import { hasIcon } from "@/lib/icons";
import type { EngineInfo, LedgerStep, StepEvent, StoredRun } from "@/lib/types";

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

/* ------------------------------------------------------- the route line -- */

// How a tool came to be chosen, where that says something. A slash command
// is not listed: the question above the answer already is one. Only "model"
// costs a pass through the model; a pattern match is decided before the model
// is touched.
const ROUTE_WORDS: Record<string, string> = {
  heuristic: "Pattern match",
  model: "Model's choice",
};

/** One step of an answer's route, as the line draws it. */
export interface StepRow {
  id: string;
  status: "running" | "ok" | "failed";
  /** An icon name the set is known to have. */
  icon: string;
  label: string;
  /** Said in a word as well as a colour; empty once the step is over. */
  state: string;
  ms: string;
}

/**
 * Apply one `step` event to the route line.
 *
 * The line carries only what is said nowhere else. The answer step is the
 * status line's job while it runs and the footer's once it has. A finished
 * tool is named, timed and qualified by its own figure below. So a step is
 * drawn here while it is running, and afterwards only if it was a routing
 * decision worth knowing about.
 */
export function applyStep(rows: readonly StepRow[], data: StepEvent): StepRow[] {
  if (data.kind === "model") return rows as StepRow[];
  const status = data.status || "running";
  // A step the run was stopped in the middle of decided nothing and found
  // nothing: there is nothing about it left to say.
  if (status === "stopped") return rows.filter((row) => row.id !== data.id);
  const over = status !== "running";
  // A command and a greeting decided nothing worth showing: the visitor knows
  // what they asked for.
  const said = data.kind === "route" && (data.detail === "slash" || data.detail === "chat");
  if (over && (data.kind === "tool" || said)) {
    return rows.filter((row) => row.id !== data.id);
  }
  let icon = data.tool && hasIcon(data.tool) ? data.tool : "tool";
  let label = data.label || "";
  if (data.kind === "route") {
    icon = "route";
    if (data.tool && data.detail && ROUTE_WORDS[data.detail]) label = ROUTE_WORDS[data.detail];
  }
  const row: StepRow = {
    id: data.id,
    status,
    icon,
    label,
    state: over ? "" : "running",
    ms: data.ms ? fmtMs(data.ms) : "",
  };
  return rows.some((r) => r.id === data.id)
    ? rows.map((r) => (r.id === data.id ? row : r))
    : [...rows, row];
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

// What each kind of step is called. A choice made without the model is
// named by how it was made.
const CHOSEN_BY: Record<string, string> = {
  slash: "command",
  heuristic: "pattern",
  chat: "small talk",
  // Kept for answers stored while a visitor could arm a skill themselves.
  skill: "skill",
};
const KIND_WORDS: Record<string, string> = {
  route: "choice",
  decide: "choice again",
  repair: "sent back",
  compose: "wrote code",
  fill: "filled in",
  tool: "tool",
  answer: "answer",
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
 * splits its time. `label` names a tool or skill in the page's words.
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
    return {
      kind: free
        ? (CHOSEN_BY[step.via as string] ?? "choice")
        : (KIND_WORDS[step.kind] ?? step.kind),
      name,
      tokens: tokensOf(step),
      ms: fmtMs(step.ms),
      failed: !step.ok,
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
