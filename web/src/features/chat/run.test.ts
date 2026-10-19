import { beforeEach, describe, expect, it } from "vitest";
import type { StoredMessage, StoredToolCall } from "@/lib/types";
import {
  addBot,
  addNotice,
  addPart,
  addUser,
  botItem,
  newKey,
  patchBot,
  patchNotice,
  patchPart,
  questionBefore,
  removeItem,
  removePart,
  replacePart,
  stepBot,
  storedParts,
  useChat,
} from "./run";

beforeEach(() => {
  useChat.setState({
    view: "empty",
    conversationId: null,
    items: [],
    streaming: false,
  });
});

describe("the thread", () => {
  it("keeps items in the order they arrived, each with a key of its own", () => {
    const q = addUser("why is the sky blue?");
    const a = addBot({ live: true });
    const n = addNotice("queue", "Next in line — starting shortly.");
    expect(useChat.getState().items.map((i) => i.kind)).toEqual(["user", "bot", "notice"]);
    expect(new Set([q.key, a.key, n.key]).size).toBe(3);
    expect(a).toMatchObject({ live: true, content: "", steps: [], parts: [], cost: null });
    expect(n).toMatchObject({ variant: "queue", retry: null, leaving: false });
  });

  it("changes one answer and leaves the rest as they were", () => {
    const first = addBot();
    const second = addBot();
    patchBot(second.key, (b) => ({ ...b, content: "done", cost: { gen_tps: 11.7 } }));
    expect(botItem(first.key)).toMatchObject({ content: "", cost: null });
    expect(botItem(second.key)).toMatchObject({ content: "done", cost: { gen_tps: 11.7 } });
    // A patch aimed at an answer does not touch a notice that has the key.
    const note = addNotice("", "hello");
    patchBot(note.key, (b) => ({ ...b, content: "wrong" }));
    expect(useChat.getState().items.at(-1)).toMatchObject({ kind: "notice", text: "hello" });
  });

  it("marks a notice as leaving, then removes it", () => {
    const note = addNotice("queue", "One request ahead");
    patchNotice(note.key, (n) => ({ ...n, leaving: true }));
    expect(useChat.getState().items[0]).toMatchObject({ leaving: true });
    removeItem(note.key);
    expect(useChat.getState().items).toEqual([]);
  });

  it("applies a step to the calls of the answer it belongs to", () => {
    const a = addBot({ live: true });
    stepBot(a.key, { id: "reply-1", kind: "model", status: "running", label: "Thinking" });
    expect(botItem(a.key)?.steps).toEqual([]);
    stepBot(a.key, { id: "tool-0", kind: "tool", status: "running", label: "Weather" });
    expect(botItem(a.key)?.steps).toHaveLength(1);
    stepBot(a.key, { id: "tool-0", kind: "tool", status: "ok", label: "Weather", ms: 400 });
    expect(botItem(a.key)?.steps).toEqual([]);
  });

  it("keeps an answer's parts in the order they were written, each changed in place", () => {
    const a = addBot({ live: true });
    const thought = { kind: "thought", key: newKey(), text: "", ms: null } as const;
    const said = { kind: "text", key: newKey(), text: "" } as const;
    const file = { kind: "file", key: newKey(), path: "app.py" } as const;
    addPart(a.key, thought);
    addPart(a.key, said);
    addPart(a.key, file);
    patchPart(a.key, thought.key, (p) => ({ ...p, text: "hmm", ms: 1200 }));
    const card = { kind: "card", key: newKey(), card: "file", data: {}, meta: null } as const;
    replacePart(a.key, file.key, card);
    expect(botItem(a.key)?.parts).toEqual([
      { kind: "thought", key: thought.key, text: "hmm", ms: 1200 },
      said,
      card,
    ]);
    removePart(a.key, said.key);
    expect(botItem(a.key)?.parts.map((p) => p.kind)).toEqual(["thought", "card"]);
  });
});

describe("storedParts", () => {
  const call = (over: Partial<StoredToolCall>): StoredToolCall => ({
    id: "t",
    conversation_id: "c",
    message_id: "m",
    tool: "weather",
    via: "model",
    args: {},
    card: "weather",
    data: { place: "Paris" },
    summary: "Paris: 12°C.",
    ms: 400,
    ok: true,
    created_at: 0,
    ...over,
  });
  const message = (over: Partial<StoredMessage>): StoredMessage => ({
    id: "m",
    role: "assistant",
    content: "",
    created_at: 0,
    ttft_ms: null,
    gen_tps: null,
    prompt_tokens: null,
    completion_tokens: null,
    tool_calls: [],
    ...over,
  });
  const label = (name: string) => ({ weather: "Weather", clock: "Clock" })[name] ?? name;
  const shape = (m: StoredMessage) =>
    storedParts(m, label).map((p) =>
      p.kind === "card" ? ["card", p.card, p.meta?.label] : [p.kind, "text" in p ? p.text : ""],
    );

  it("draws an answer kept with its segments in the order it was said", () => {
    const m = message({
      content: "Let me look.\n\nIt is mild.",
      tool_calls: [call({}), call({ tool: "clock", ok: false, card: null, summary: "No zone." })],
      segments: [
        { kind: "thought", text: "The user wants the weather." },
        { kind: "text", text: "Let me look." },
        { kind: "card", call: 0 },
        { kind: "card", call: 1 },
        { kind: "text", text: "It is mild." },
      ],
    });
    expect(shape(m)).toEqual([
      ["thought", "The user wants the weather."],
      ["text", "Let me look."],
      ["card", "weather", "Weather"],
      ["card", "error", "Clock"],
      ["text", "It is mild."],
    ]);
    const parts = storedParts(m, label);
    expect(parts[3]).toMatchObject({ data: { message: "No zone." }, meta: { ms: 400 } });
    // Written since files were written with calls: nothing in its text is folded.
    expect(parts.filter((p) => p.kind === "text").every((p) => !("fold" in p))).toBe(true);
    expect(parts[0]).toMatchObject({ ms: null });
    expect(new Set(parts.map((p) => p.key)).size).toBe(parts.length);
  });

  it("draws an answer kept before then as it always was: its cards, then its text", () => {
    const m = message({ content: "It is mild.", tool_calls: [call({})] });
    expect(shape(m)).toEqual([
      ["card", "weather", "Weather"],
      ["text", "It is mild."],
    ]);
    expect(shape({ ...m, segments: null })).toEqual(shape(m));
    expect(shape(message({ content: "" }))).toEqual([]);
    // Its text may hold the files it wrote out, which the project kept from it.
    expect(storedParts(m, label).at(-1)).toMatchObject({ kind: "text", fold: true });
  });

  it("loses nothing when the segments leave something out", () => {
    const m = message({
      content: "It is mild.",
      tool_calls: [call({}), call({ tool: "clock", card: "clock" })],
      segments: [
        { kind: "thought", text: "Hmm." },
        { kind: "card", call: 1 },
        { kind: "card", call: 7 },
      ],
    });
    // The call no segment placed comes first, as every call used to; the
    // words no segment held come last; a segment naming no call is skipped.
    expect(shape(m)).toEqual([
      ["card", "weather", "Weather"],
      ["thought", "Hmm."],
      ["card", "clock", "Clock"],
      ["text", "It is mild."],
    ]);
  });

  it("skips a segment with nothing in it", () => {
    const m = message({
      content: "Done.",
      segments: [
        { kind: "thought", text: "  " },
        { kind: "text", text: "Done." },
      ],
    });
    expect(shape(m)).toEqual([["text", "Done."]]);
  });
});

describe("questionBefore", () => {
  it("is the question an answer was given to", () => {
    addUser("first");
    const a1 = addBot();
    addUser("second");
    const a2 = addBot();
    expect(questionBefore(a1.key)).toBe("first");
    expect(questionBefore(a2.key)).toBe("second");
  });

  it("is nothing when the item before an answer is not a question", () => {
    const orphan = addBot();
    expect(questionBefore(orphan.key)).toBeNull();
    addUser("asked");
    addNotice("", "The oldest message was dropped to fit the context window.");
    const after = addBot();
    expect(questionBefore(after.key)).toBeNull();
    expect(questionBefore(9999)).toBeNull();
  });
});
