import { describe, expect, it } from "vitest";
import type { Skill, StepEvent, Tool } from "@/lib/types";
import {
  applyStep,
  charCount,
  completeCommand,
  costFigures,
  isLongMessage,
  LONG_CHARS,
  MAX_CHARS,
  paletteQuery,
  paletteRows,
  pipeWords,
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

describe("trimmedWording", () => {
  it("agrees in number", () => {
    expect(trimmedWording(1)).toBe("The oldest message was dropped to fit the context window.");
    expect(trimmedWording(4)).toBe("The 4 oldest messages were dropped to fit the context window.");
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

  it("names a skill as a skill", () => {
    const rows = applyStep(
      none,
      step({ status: "ok", label: "Fact-check", detail: "skill", tool: "factcheck" }),
    );
    expect(rows[0]).toMatchObject({ icon: "route", label: "Skill: Fact-check" });
  });

  it("drops a slash command's route once decided: the question already is one", () => {
    const running = applyStep(none, step({}));
    expect(applyStep(running, step({ status: "ok", detail: "slash", tool: "calculator" }))).toEqual(
      [],
    );
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

describe("the palette", () => {
  const tool = (name: string, example: string, description = `${name} description`): Tool => ({
    name,
    label: name,
    glyph: "",
    description,
    card: null,
    network: false,
    example,
    args: [],
  });
  const skill = (id: string, name: string): Skill => ({
    id,
    name,
    glyph: "",
    blurb: `${name} blurb`,
    pipeline: [],
    persona: "explainer",
    placeholder: "",
  });
  const tools = [
    tool("calculator", "/calc 1247 * 89"),
    tool("weather", "/weather Paris"),
    tool("wikipedia", "/wiki Ada Lovelace"),
    tool("bare", ""),
  ];
  const skills = [skill("summarise", "Summarise a link"), skill("factcheck", "Fact-check")];

  it("asks for the word after the slash", () => {
    expect(paletteQuery("/")).toBe("");
    expect(paletteQuery("/we")).toBe("we");
  });
  it("closes without a slash, on a second line, or once arguments are typed", () => {
    expect(paletteQuery("")).toBeNull();
    expect(paletteQuery("what about /calc")).toBeNull();
    expect(paletteQuery("/calc\n")).toBeNull();
    expect(paletteQuery("/calc 2+2")).toBeNull();
    expect(paletteQuery("/calc ")).toBeNull();
  });

  it("offers only the tools on the empty sheet, where the skills are already listed", () => {
    const rows = paletteRows("", tools, skills, true);
    expect(rows.map((r) => r.kind)).toEqual(["tool", "tool", "tool", "tool"]);
    expect(rows[0]).toEqual({
      kind: "tool",
      id: "calculator",
      cmd: "/calc 1247 * 89",
      desc: "calculator description",
    });
    // A tool with no example is offered by its name.
    expect(rows[3]?.cmd).toBe("/bare");
  });

  it("lists skills first, then tools, in a thread", () => {
    const rows = paletteRows("", tools, skills, false);
    expect(rows.map((r) => r.cmd)).toEqual([
      "Summarise a link",
      "Fact-check",
      "/calc 1247 * 89",
      "/weather Paris",
      "/wiki Ada Lovelace",
      "/bare",
    ]);
  });

  it("filters tools by name or example and skills by id or name, whatever the case", () => {
    expect(paletteRows("w", tools, skills, true).map((r) => r.id)).toEqual([
      "weather",
      "wikipedia",
    ]);
    expect(paletteRows("CALC", tools, skills, true).map((r) => r.id)).toEqual(["calculator"]);
    expect(paletteRows("fact", tools, skills, false).map((r) => r.id)).toEqual(["factcheck"]);
    expect(paletteRows("zzz", tools, skills, false)).toEqual([]);
  });

  it("completes a command with its example arguments selected", () => {
    expect(completeCommand("/calc 1247 * 89")).toEqual({
      value: "/calc 1247 * 89",
      select: [6, 15],
    });
    expect(completeCommand("/clock")).toEqual({ value: "/clock ", select: null });
  });
});

describe("pipeWords", () => {
  it("names each tool in plain words and the model as the answer", () => {
    expect(pipeWords(["read_url", "model"])).toEqual(["read url", "answer"]);
    expect(pipeWords(["wikipedia", "wikipedia", "model"])).toEqual([
      "wikipedia",
      "wikipedia",
      "answer",
    ]);
  });
});
