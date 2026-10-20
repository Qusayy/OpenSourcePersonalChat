import { describe, expect, it } from "vitest";
import type { LedgerStep, StepEvent, StoredRun } from "@/lib/types";
import {
  applyStep,
  charCount,
  confirmWording,
  costFigures,
  isLongMessage,
  LONG_CHARS,
  ledgerRows,
  MAX_CHARS,
  OUTCOME_WORDS,
  outcomeOf,
  placeholderFor,
  queueWording,
  readWords,
  type StepRow,
  trimmedWording,
} from "./rules";

describe("placeholderFor", () => {
  it("invites a question when the model is ready", () => {
    expect(placeholderFor({ status: "ready" })).toBe("Ask something…");
  });
  it("says why nothing can be sent while it is not", () => {
    expect(placeholderFor({ status: "loading" })).toBe("The model is still loading…");
    expect(placeholderFor({ status: "cold" })).toBe("The model is still loading…");
    expect(placeholderFor({ status: "error" })).toBe(
      "The model could not be loaded — nothing can be sent right now",
    );
  });
});

describe("charCount", () => {
  it("stays quiet until 500 characters are left", () => {
    expect(charCount(0).near).toBe(false);
    expect(charCount(MAX_CHARS - 501).near).toBe(false);
    expect(charCount(MAX_CHARS - 500).near).toBe(true);
  });
  it("counts down and warns at the ceiling", () => {
    expect(charCount(MAX_CHARS - 12)).toMatchObject({ near: true, warn: false });
    expect(charCount(MAX_CHARS - 12).text).toMatch(/^12 characters left$/);
    expect(charCount(MAX_CHARS)).toMatchObject({ near: true, warn: true });
    expect(charCount(MAX_CHARS).text).toMatch(/^0 characters left$/);
  });
});

describe("isLongMessage", () => {
  it("is a question up to 280 characters and four lines", () => {
    expect(isLongMessage("why is the sky blue?")).toBe(false);
    expect(isLongMessage("a".repeat(LONG_CHARS))).toBe(false);
    expect(isLongMessage("one\ntwo\nthree\nfour")).toBe(false);
  });
  it("is a pasted document past either", () => {
    expect(isLongMessage("a".repeat(LONG_CHARS + 1))).toBe(true);
    expect(isLongMessage("one\ntwo\nthree\nfour\nfive")).toBe(true);
  });
});

describe("queueWording", () => {
  it("counts the requests ahead", () => {
    expect(queueWording(3)).toEqual({
      text: "3 requests ahead of yours — this server runs one generation at a time.",
      ahead: "3 ahead",
    });
  });
  it("says one in words", () => {
    expect(queueWording(1)).toEqual({
      text: "One request ahead of yours — this server runs one generation at a time.",
      ahead: "1 ahead",
    });
  });
  it("says next in line when none are", () => {
    expect(queueWording(0)).toEqual({
      text: "Next in line — starting shortly.",
      ahead: "next in line",
    });
  });
});

describe("ledgerRows", () => {
  const step = (over: Partial<LedgerStep>): LedgerStep => ({
    kind: "route",
    name: null,
    via: "model",
    ok: true,
    error: null,
    tokens_in: null,
    tokens_read: null,
    tokens_out: null,
    ms: 0,
    ...over,
  });
  const run = (ledger: LedgerStep[]): StoredRun => ({
    id: "r",
    outcome: null,
    steps: ledger.length,
    passes: 0,
    total_ms: 4480,
    waiting_ms: 0,
    reading_ms: 890,
    writing_ms: 2710,
    tools_ms: 880,
    wall_ms: null,
    ledger,
  });
  const label = (name: string) =>
    ({ weather: "Weather", compare: "Compare two things" })[name] ?? name;

  it("lists each reply with what it read of its prompt, what it wrote, and its time", () => {
    const { rows, split } = ledgerRows(
      run([
        step({
          kind: "call",
          name: "weather",
          tokens_in: 667,
          tokens_read: 18,
          tokens_out: 12,
          ms: 310,
        }),
        step({ kind: "tool", name: "weather", ms: 880 }),
        step({
          kind: "answer",
          via: "model",
          tokens_in: 740,
          tokens_read: 61,
          tokens_out: 48,
          ms: 2900,
        }),
      ]),
      label,
    );
    expect(rows).toEqual([
      {
        kind: "chose",
        name: "Weather",
        tokens: "read 18 of 667 · wrote 12",
        ms: "310ms",
        failed: false,
      },
      { kind: "tool", name: "Weather", tokens: "", ms: "880ms", failed: false },
      { kind: "answer", name: "", tokens: "read 61 of 740 · wrote 48", ms: "2.90s", failed: false },
    ]);
    expect(split).toBe("run 4.48s · waiting 0ms · reading 890ms · writing 2.71s · tools 880ms");
  });

  it("marks a reply that failed or was stopped", () => {
    const { rows } = ledgerRows(
      run([step({ kind: "call", name: "weather", ok: false, error: "stopped" })]),
      label,
    );
    expect(rows[0]).toMatchObject({ kind: "chose", name: "Weather", failed: true });
  });

  it("keeps the words of a run kept while a separate pass chose the tool", () => {
    const { rows } = ledgerRows(
      run([
        step({ name: "weather", tokens_in: 667, tokens_read: 18, tokens_out: 12, ms: 310 }),
        step({ kind: "decide", tokens_in: 702, tokens_read: 36, tokens_out: 5, ms: 400 }),
        step({ kind: "compose", name: "python" }),
      ]),
      label,
    );
    expect(rows.map((r) => [r.kind, r.name, r.tokens])).toEqual([
      ["choice", "Weather", "read 18 of 667 · wrote 12"],
      ["choice again", "none", "read 36 of 702 · wrote 5"],
      ["wrote code", "python", ""],
    ]);
  });

  it("says what read was not reported by the whole prompt it was given", () => {
    const { rows } = ledgerRows(run([step({ tokens_in: 400, tokens_out: 9, ms: 1 })]), label);
    expect(rows[0]?.tokens).toBe("read 400 · wrote 9");
  });

  it("names a choice made without the model by how it was made", () => {
    const { rows } = ledgerRows(
      run([
        step({ via: "slash", name: "weather" }),
        step({ via: "heuristic", name: "weather" }),
        step({ via: "skill", name: "compare" }),
        step({ kind: "fill", name: "compare" }),
        step({ name: "ask" }),
        step({ kind: "repair", name: "weather", ok: false, error: "rejected" }),
      ]),
      label,
    );
    expect(rows.map((r) => [r.kind, r.name, r.failed])).toEqual([
      ["command", "Weather", false],
      ["pattern", "Weather", false],
      ["skill", "Compare two things", false],
      ["filled in", "Compare two things", false],
      ["choice", "asked you", false],
      ["sent back", "Weather", true],
    ]);
  });

  it("says what you answered when a change was put to you, and never that it failed", () => {
    const { rows } = ledgerRows(
      run([
        step({ kind: "confirm", name: "write_file", via: "user", ms: 4200 }),
        step({ kind: "confirm", name: "write_file", via: "user", ok: false, error: "no" }),
        step({ kind: "confirm", name: "python", via: "user", ok: false, error: "no answer" }),
        step({ kind: "confirm", name: "run_file", via: "user", ok: false, error: "stopped" }),
      ]),
      (name) =>
        ({ write_file: "Write file", python: "Python", run_file: "Run file" })[name] ?? name,
    );
    expect(rows.map((r) => [r.kind, r.name, r.failed])).toEqual([
      ["asked you", "Write file · allowed", false],
      ["asked you", "Write file · not allowed", false],
      ["asked you", "Python · no answer", false],
      ["asked you", "Run file · stopped", false],
    ]);
    expect(rows[0]?.ms).toBe("4.20s");
  });
});

describe("a change put to you", () => {
  const ask = (over: { tool: string; label: string; what: string; because?: string }) =>
    confirmWording({ because: "Web search", ...over });

  it("says what it wants to do, in a verb and what it touches", () => {
    expect(ask({ tool: "write_file", label: "Write file", what: "app/main.py" })).toEqual({
      action: "write app/main.py",
      code: null,
      why: "It read a web search first, so check this is what you asked for and not something it read there.",
    });
    expect(ask({ tool: "edit_file", label: "Edit file", what: "app/main.py" }).action).toBe(
      "edit app/main.py",
    );
    expect(ask({ tool: "run_file", label: "Run file", what: "app/main.py" }).action).toBe(
      "run app/main.py",
    );
    expect(ask({ tool: "move_file", label: "Move file", what: "a.py to b.py" }).action).toBe(
      "move a.py to b.py",
    );
    expect(ask({ tool: "delete_file", label: "Delete file", what: "old.py" }).action).toBe(
      "delete old.py",
    );
  });

  it("shows a program as code, and a tool it has no verb for by its name", () => {
    const program = ask({
      tool: "python",
      label: "Python",
      what: "import os\nos.remove('x')\n...",
    });
    expect(program.action).toBe("run this program");
    expect(program.code).toBe("import os\nos.remove('x')\n...");
    expect(ask({ tool: "deploy", label: "Deploy", what: "site" }).action).toBe(
      "use Deploy on site",
    );
    expect(ask({ tool: "deploy", label: "", what: "" }).action).toBe("use deploy");
    expect(ask({ tool: "write_file", label: "Write file", what: " " }).action).toBe("write a file");
  });

  it("names what was read as a sentence would, each thing once", () => {
    expect(readWords("Web search")).toBe("a web search");
    expect(readWords("Web search, Read page")).toBe("a web search and a web page");
    expect(readWords("News, Wikipedia, Open result")).toBe(
      "the news, Wikipedia and a page from a search",
    );
    expect(readWords("Read page, Read page")).toBe("a web page");
    expect(readWords("Archive")).toBe("what Archive returned");
    // The server's own words, where it had no label to give.
    expect(readWords("a page")).toBe("a page");
    expect(readWords("")).toBe("a page");
  });

  it("ends as one line: what you chose, or why nothing was", () => {
    expect(outcomeOf({ status: "ok", detail: "once" })).toBe("once");
    expect(outcomeOf({ status: "ok", detail: "reply" })).toBe("reply");
    expect(outcomeOf({ status: "failed", detail: "no" })).toBe("no");
    expect(outcomeOf({ status: "failed", detail: "no answer" })).toBe("no answer");
    expect(outcomeOf({ status: "stopped", detail: "stopped" })).toBe("stopped");
    // Said some other way: by its status.
    expect(outcomeOf({ status: "ok", detail: null })).toBe("once");
    expect(outcomeOf({ status: "failed", detail: "?" })).toBe("no");
    expect(OUTCOME_WORDS).toEqual({
      once: "Allowed.",
      reply: "Allowed for this reply.",
      no: "Not allowed.",
      "no answer": "No answer in time, so not allowed.",
      stopped: "Stopped before you answered.",
    });
  });

  it("is not drawn as a call under way", () => {
    const row = applyStep([], {
      id: "confirm-0",
      kind: "confirm",
      status: "ok",
      label: "Allowed",
      detail: "once",
    });
    expect(row).toEqual([]);
  });
});

describe("trimmedWording", () => {
  it("agrees in number", () => {
    expect(trimmedWording(1)).toBe("The oldest message was dropped to fit the context window.");
    expect(trimmedWording(4)).toBe("The 4 oldest messages were dropped to fit the context window.");
  });
  it("says when the message itself was cut, alone or after what was dropped", () => {
    const cut = "Your message was too long to read whole; the model read its first part.";
    expect(trimmedWording(0, true)).toBe(cut);
    expect(trimmedWording(2, true)).toBe(
      `The 2 oldest messages were dropped to fit the context window. ${cut}`,
    );
  });
});

describe("applyStep", () => {
  const step = (over: Partial<StepEvent>): StepEvent => ({
    id: "tool-0",
    kind: "tool",
    status: "running",
    label: "Weather",
    tool: "weather",
    ...over,
  });
  const none: StepRow[] = [];

  it("draws a call while it runs", () => {
    expect(applyStep(none, step({}))).toEqual([
      { id: "tool-0", icon: "weather", label: "Weather" },
    ]);
  });

  it("takes a call off the line once it has ended, however it ended: its figure says the rest", () => {
    const running = applyStep(none, step({}));
    expect(applyStep(running, step({ status: "ok", ms: 412 }))).toEqual([]);
    expect(applyStep(running, step({ status: "failed", detail: "no such place" }))).toEqual([]);
    expect(applyStep(running, step({ status: "stopped" }))).toEqual([]);
  });

  it("never draws a reply of the model: the status line and the footer carry it", () => {
    for (const label of ["Thinking", "Writing", "Answer", "Called Weather"]) {
      expect(applyStep(none, step({ id: "reply-1", kind: "model", label, tool: null }))).toEqual(
        [],
      );
    }
    const running = applyStep(none, step({}));
    expect(
      applyStep(running, step({ id: "reply-2", kind: "model", status: "ok", label: "Answer" })),
    ).toBe(running);
  });

  it("draws nothing for a step that decides, which an older server may still send", () => {
    const old = { ...step({ id: "route", label: "Choosing a tool" }), kind: "route" };
    expect(applyStep(none, old as unknown as StepEvent)).toEqual([]);
  });

  it("gives a tool with no picture the generic one, and a project tool the file", () => {
    expect(applyStep(none, step({ label: "Mystery", tool: "no_such_tool" }))[0]?.icon).toBe("tool");
    expect(applyStep(none, step({ label: "Edit file", tool: "edit_file" }))[0]?.icon).toBe(
      "edit_file",
    );
  });

  it("updates a call in place and leaves the others alone", () => {
    let rows = applyStep(none, step({}));
    rows = applyStep(rows, step({ id: "tool-1", label: "Clock", tool: "clock" }));
    rows = applyStep(rows, step({ id: "tool-1", label: "World clock", tool: "clock" }));
    expect(rows.map((r) => [r.id, r.label])).toEqual([
      ["tool-0", "Weather"],
      ["tool-1", "World clock"],
    ]);
    expect(applyStep(rows, step({ status: "ok" })).map((r) => r.id)).toEqual(["tool-1"]);
  });
});

describe("costFigures", () => {
  it("prints the four figures of a finished stream, in order", () => {
    expect(
      costFigures({ gen_tps: 11.72, ttft_ms: 86.4, completion_tokens: 1234, total_ms: 8776 }),
    ).toEqual([
      { value: "11.7", words: "tok/s" },
      { value: "86ms", words: "to first token" },
      { value: "1,234", words: "tokens" },
      { value: "8.78s", words: "to answer" },
    ]);
  });
  it("prints only what a stored answer kept", () => {
    expect(costFigures({ gen_tps: 9.8, ttft_ms: 412, completion_tokens: 24 })).toHaveLength(3);
    expect(costFigures({ gen_tps: null, ttft_ms: 412 })).toEqual([
      { value: "412ms", words: "to first token" },
    ]);
    expect(costFigures({})).toEqual([]);
  });
});
