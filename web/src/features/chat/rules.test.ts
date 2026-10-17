import { describe, expect, it } from "vitest";
import type { LedgerStep, StepEvent, StoredRun } from "@/lib/types";
import {
  applyStep,
  charCount,
  costFigures,
  isLongMessage,
  LONG_CHARS,
  ledgerRows,
  MAX_CHARS,
  placeholderFor,
  queueWording,
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

  it("lists each pass with what it read of its prompt, what it wrote, and its time", () => {
    const { rows, split } = ledgerRows(
      run([
        step({ name: "weather", tokens_in: 667, tokens_read: 18, tokens_out: 12, ms: 310 }),
        step({ kind: "tool", name: "weather", ms: 880 }),
        step({ kind: "decide", tokens_in: 702, tokens_read: 36, tokens_out: 5, ms: 400 }),
        step({
          kind: "answer",
          via: null,
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
        kind: "choice",
        name: "Weather",
        tokens: "read 18 of 667 · wrote 12",
        ms: "310ms",
        failed: false,
      },
      { kind: "tool", name: "Weather", tokens: "", ms: "880ms", failed: false },
      {
        kind: "choice again",
        name: "none",
        tokens: "read 36 of 702 · wrote 5",
        ms: "400ms",
        failed: false,
      },
      { kind: "answer", name: "", tokens: "read 61 of 740 · wrote 48", ms: "2.90s", failed: false },
    ]);
    expect(split).toBe("run 4.48s · waiting 0ms · reading 890ms · writing 2.71s · tools 880ms");
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
    id: "route",
    kind: "route",
    status: "running",
    label: "Choosing a tool",
    ...over,
  });
  const none: StepRow[] = [];

  it("draws a routing pass while it runs", () => {
    expect(applyStep(none, step({}))).toEqual([
      {
        id: "route",
        status: "running",
        icon: "route",
        label: "Choosing a tool",
        state: "running",
        ms: "",
      },
    ]);
  });

  it("keeps a finished routing decision, worded by how it was made", () => {
    const running = applyStep(none, step({}));
    const byModel = applyStep(
      running,
      step({ status: "ok", label: "Weather", detail: "model", tool: "weather", ms: 2140 }),
    );
    expect(byModel).toEqual([
      { id: "route", status: "ok", icon: "route", label: "Model's choice", state: "", ms: "2.14s" },
    ]);
    const byPattern = applyStep(
      none,
      step({ status: "ok", label: "Calculator", detail: "heuristic", tool: "calculator", ms: 0.4 }),
    );
    expect(byPattern[0]).toMatchObject({ label: "Pattern match", ms: "0ms" });
  });

  it("keeps the server's own label when no tool was chosen", () => {
    const rows = applyStep(none, step({ status: "ok", label: "No tool needed", detail: "direct" }));
    expect(rows[0]).toMatchObject({ label: "No tool needed", state: "" });
  });

  it("names a skill the model chose as the model's choice, as it does a tool", () => {
    const rows = applyStep(
      none,
      step({ status: "ok", label: "Compare two things", detail: "model", tool: "compare" }),
    );
    expect(rows[0]).toMatchObject({ icon: "route", label: "Model's choice" });
  });

  it("drops a slash command's route once decided: the question already is one", () => {
    const running = applyStep(none, step({}));
    expect(applyStep(running, step({ status: "ok", detail: "slash", tool: "calculator" }))).toEqual(
      [],
    );
  });

  it("drops a greeting's route once decided: nothing was looked up or chosen", () => {
    const running = applyStep(none, step({}));
    expect(applyStep(running, step({ status: "ok", detail: "chat", tool: null }))).toEqual([]);
  });

  it("draws a tool only while it runs: its figure says the rest", () => {
    const running = applyStep(
      none,
      step({ id: "tool-0", kind: "tool", label: "Weather", tool: "weather" }),
    );
    expect(running[0]).toMatchObject({ id: "tool-0", icon: "weather", state: "running" });
    const done = applyStep(
      running,
      step({
        id: "tool-0",
        kind: "tool",
        status: "ok",
        label: "Weather",
        tool: "weather",
        ms: 412,
      }),
    );
    expect(done).toEqual([]);
    const failed = applyStep(
      running,
      step({ id: "tool-0", kind: "tool", status: "failed", label: "Weather", tool: "weather" }),
    );
    expect(failed).toEqual([]);
  });

  it("takes a step off the line when the run was stopped in the middle of it", () => {
    const choosing = applyStep(none, step({}));
    expect(applyStep(choosing, step({ status: "stopped", ms: 310 }))).toEqual([]);
    const fetching = applyStep(
      none,
      step({ id: "tool-0", kind: "tool", label: "Weather", tool: "weather" }),
    );
    expect(
      applyStep(
        fetching,
        step({ id: "tool-0", kind: "tool", status: "stopped", label: "Weather", tool: "weather" }),
      ),
    ).toEqual([]);
    // Only that step: a decision already made stays where it is.
    let rows = applyStep(
      none,
      step({ status: "ok", label: "Weather", detail: "model", tool: "weather" }),
    );
    rows = applyStep(rows, step({ id: "tool-0", kind: "tool", label: "Weather", tool: "weather" }));
    rows = applyStep(
      rows,
      step({ id: "tool-0", kind: "tool", status: "stopped", label: "Weather" }),
    );
    expect(rows.map((r) => r.id)).toEqual(["route"]);
  });

  it("gives a tool with no picture the generic one", () => {
    const rows = applyStep(
      none,
      step({ id: "tool-0", kind: "tool", label: "Mystery", tool: "no_such_tool" }),
    );
    expect(rows[0]?.icon).toBe("tool");
  });

  it("never draws the answer step: the status line and the footer carry it", () => {
    expect(applyStep(none, step({ id: "answer", kind: "model", label: "Writing" }))).toEqual([]);
  });

  it("updates a step in place and leaves the others alone", () => {
    let rows = applyStep(none, step({}));
    rows = applyStep(rows, step({ id: "tool-0", kind: "tool", label: "Clock", tool: "clock" }));
    rows = applyStep(rows, step({ status: "ok", label: "Clock", detail: "model", tool: "clock" }));
    expect(rows.map((r) => [r.id, r.status])).toEqual([
      ["route", "ok"],
      ["tool-0", "running"],
    ]);
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
