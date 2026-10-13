import { beforeEach, describe, expect, it } from "vitest";
import { setBootstrap } from "@/lib/bootstrap";
import type { Bootstrap } from "@/lib/types";
import {
  addBot,
  addNotice,
  addUser,
  botItem,
  choosePersona,
  patchBot,
  patchNotice,
  personaNow,
  questionBefore,
  removeItem,
  stepBot,
  useChat,
} from "./run";

beforeEach(() => {
  localStorage.clear();
  setBootstrap({ defaultPersona: "explainer" } as Bootstrap);
  useChat.setState({
    view: "empty",
    persona: null,
    skill: null,
    conversationId: null,
    items: [],
    streaming: false,
  });
});

describe("the persona", () => {
  it("is the server's default until one is chosen", () => {
    expect(personaNow()).toBe("explainer");
  });

  it("is remembered as the bare id, the value the page has always stored", () => {
    choosePersona("code");
    expect(personaNow()).toBe("code");
    expect(localStorage.getItem("aurora.persona")).toBe("code");
  });

  it("comes back from storage on the next visit", () => {
    localStorage.setItem("aurora.persona", "agent");
    expect(personaNow()).toBe("agent");
  });
});

describe("the thread", () => {
  it("keeps items in the order they arrived, each with a key of its own", () => {
    const q = addUser("why is the sky blue?");
    const a = addBot({ live: true });
    const n = addNotice("queue", "Next in line — starting shortly.");
    expect(useChat.getState().items.map((i) => i.kind)).toEqual(["user", "bot", "notice"]);
    expect(new Set([q.key, a.key, n.key]).size).toBe(3);
    expect(a).toMatchObject({ live: true, content: "", steps: [], cards: [], cost: null });
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

  it("applies a step to the route line of the answer it belongs to", () => {
    const a = addBot({ live: true });
    stepBot(a.key, { id: "route", kind: "route", status: "running", label: "Choosing a tool" });
    expect(botItem(a.key)?.steps).toHaveLength(1);
    stepBot(a.key, { id: "route", kind: "route", status: "ok", label: "x", detail: "slash" });
    expect(botItem(a.key)?.steps).toEqual([]);
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
