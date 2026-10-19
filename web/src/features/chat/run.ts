/* The conversation on the sheet: which view is showing, and the thread
 * itself.
 *
 * It lives outside the page component, so a visit to Benchmark or About and
 * back finds the conversation as it was left. What changes many times a
 * second while an answer is written (its text, the figures) is not here: the
 * controller writes that straight to the page. The store holds what the
 * thread is made of.
 */

import { create } from "zustand";
import type { CardData, CardKind, StepEvent, StoredMessage, StoredRun } from "@/lib/types";
import { applyStep, type Cost, type StepRow } from "./rules";

export type View = "empty" | "thread";

/* --------------------------------------------------- an answer's parts -- */

/* An answer reads in the order it was written: what the model thought, what
   it said, the result of a call it made, what it said after that. Each piece
   is a part, and the answer is the list of them. */

/** What the model thought before it said anything, folded away until asked for. */
export interface ThoughtPart {
  kind: "thought";
  key: number;
  text: string;
  /** How long it thought, where that was seen; null for a stored answer,
   *  and while it is still thinking. */
  ms: number | null;
}

/** Something the model said: markdown. While it is being written its text
 *  is on the page, not here. */
export interface TextPart {
  kind: "text";
  key: number;
  text: string;
  /** Kept from before files were written with calls, when an answer wrote
   *  them out in its text and the project kept them from there: each is
   *  drawn as a chip that opens it. Never set on a reply written since. */
  fold?: boolean;
}

/** A call's result, drawn as a figure where it came in the reply. */
export interface CardPart {
  kind: "card";
  key: number;
  card: CardKind;
  data: CardData;
  /** What the tool's step reported: its name on screen and how long it took. */
  meta: { label?: string; ms?: number | null } | null;
}

/** A file the model is writing as it replies, until the call that writes it
 *  has been made and its card takes this one's place. */
export interface FilePart {
  kind: "file";
  key: number;
  path: string;
}

export type Part = ThoughtPart | TextPart | CardPart | FilePart;

export interface UserItem {
  kind: "user";
  key: number;
  /** Kept verbatim for "Ask again": what is drawn may be folded. */
  text: string;
}

export interface BotItem {
  kind: "bot";
  key: number;
  /** Being written right now. The text of its parts is on the page, not
   *  here, until each part ends. */
  live: boolean;
  /** Everything it said, as one markdown text, once there is a whole one:
   *  what Copy copies. */
  content: string;
  /** What it is made of, in the order it was written. */
  parts: Part[];
  /** The calls under way, each drawn while it runs. */
  steps: StepRow[];
  /** What it cost; null until it has ended (or for a stored answer that kept nothing). */
  cost: Cost | null;
  /** A figure in it that nothing it looked up returned, said in a line. */
  correction: string | null;
  /** How it ran: every step and what it cost. Null until the run has ended,
   *  or for a stored answer whose steps are no longer kept. */
  run: StoredRun | null;
}

export type NoticeVariant = "" | "queue" | "err";

export interface NoticeItem {
  kind: "notice";
  key: number;
  variant: NoticeVariant;
  text: string;
  /** For an error: the question to send again. */
  retry: string | null;
  /** Fading out; removed a moment later. */
  leaving: boolean;
}

export type ThreadItem = UserItem | BotItem | NoticeItem;

interface ChatState {
  view: View;
  conversationId: string | null;
  items: ThreadItem[];
  streaming: boolean;
}

export const useChat = create<ChatState>(() => ({
  view: "empty",
  conversationId: null,
  items: [],
  streaming: false,
}));

let nextKey = 0;
/** A key for a new thread item or part. Never reused, so React never
 *  mistakes a new answer for an old one. */
export const newKey = (): number => ++nextKey;

const set = useChat.setState;
const get = useChat.getState;

/* -------------------------------------------------------------- actions -- */

export function addUser(text: string): UserItem {
  const item: UserItem = { kind: "user", key: newKey(), text };
  set((s) => ({ items: [...s.items, item] }));
  return item;
}

export function addBot(over: Partial<BotItem> = {}): BotItem {
  const item: BotItem = {
    kind: "bot",
    key: newKey(),
    live: false,
    content: "",
    parts: [],
    steps: [],
    cost: null,
    correction: null,
    run: null,
    ...over,
  };
  set((s) => ({ items: [...s.items, item] }));
  return item;
}

export function addNotice(variant: NoticeVariant, text: string, retry: string | null = null) {
  const item: NoticeItem = { kind: "notice", key: newKey(), variant, text, retry, leaving: false };
  set((s) => ({ items: [...s.items, item] }));
  return item;
}

export function removeItem(key: number): void {
  set((s) => ({ items: s.items.filter((item) => item.key !== key) }));
}

function patch<T extends ThreadItem>(key: number, kind: T["kind"], change: (item: T) => T): void {
  set((s) => ({
    items: s.items.map((item) =>
      item.key === key && item.kind === kind ? change(item as T) : item,
    ),
  }));
}

export const patchBot = (key: number, change: (item: BotItem) => BotItem) =>
  patch<BotItem>(key, "bot", change);

export const patchNotice = (key: number, change: (item: NoticeItem) => NoticeItem) =>
  patch<NoticeItem>(key, "notice", change);

/** One `step` event of the stream, applied to the calls an answer is making. */
export function stepBot(key: number, data: StepEvent): void {
  patchBot(key, (item) => ({ ...item, steps: applyStep(item.steps, data) }));
}

/** Add a part to the end of an answer. */
export function addPart(key: number, part: Part): void {
  patchBot(key, (item) => ({ ...item, parts: [...item.parts, part] }));
}

/** Change one part of an answer, in place. */
export function patchPart(key: number, partKey: number, change: (part: Part) => Part): void {
  patchBot(key, (item) => ({
    ...item,
    parts: item.parts.map((part) => (part.key === partKey ? change(part) : part)),
  }));
}

/** Put a part where another was. */
export function replacePart(key: number, partKey: number, part: Part): void {
  patchPart(key, partKey, () => part);
}

/** Take one part out of an answer. */
export function removePart(key: number, partKey: number): void {
  patchBot(key, (item) => ({ ...item, parts: item.parts.filter((part) => part.key !== partKey) }));
}

/**
 * A stored answer's parts. One kept with its segments is drawn in the order
 * it was said; one kept before then is drawn as it always was, its cards
 * first and its text under them. `label` names a tool in the page's words,
 * since only a call's registry name was kept.
 */
export function storedParts(m: StoredMessage, label: (name: string) => string): Part[] {
  const cards: CardPart[] = (m.tool_calls || []).map((call) => {
    const meta = { label: label(call.tool), ms: call.ms };
    return call.ok && call.card
      ? { kind: "card", key: newKey(), card: call.card, data: call.data, meta }
      : { kind: "card", key: newKey(), card: "error", data: { message: call.summary }, meta };
  });
  const text = (body: string, fold = false): TextPart[] =>
    body.trim() ? [{ kind: "text", key: newKey(), text: body, ...(fold ? { fold } : {}) }] : [];
  if (!m.segments?.length) return [...cards, ...text(m.content, true)];

  const parts: Part[] = [];
  const placed = new Set<number>();
  for (const seg of m.segments) {
    if (seg.kind === "card") {
      const card = cards[seg.call];
      if (card && !placed.has(seg.call)) {
        parts.push(card);
        placed.add(seg.call);
      }
    } else if (seg.text.trim()) {
      parts.push(
        seg.kind === "thought"
          ? { kind: "thought", key: newKey(), text: seg.text, ms: null }
          : { kind: "text", key: newKey(), text: seg.text },
      );
    }
  }
  // A call no segment placed goes where every call used to: first.
  const unplaced = cards.filter((_, i) => !placed.has(i));
  // Said in the text but in no segment: the words stored for it.
  const words = parts.some((p) => p.kind === "text") ? [] : text(m.content);
  return [...unplaced, ...parts, ...words];
}

/** The bot item with this key, as it stands. */
export function botItem(key: number): BotItem | null {
  const item = get().items.find((i) => i.key === key);
  return item?.kind === "bot" ? item : null;
}

/** The question an answer was given to: the item just before it, if that is a
 *  question. "Ask again" sends this. */
export function questionBefore(key: number): string | null {
  const items = get().items;
  const at = items.findIndex((item) => item.key === key);
  const prev = at > 0 ? items[at - 1] : null;
  return prev?.kind === "user" ? prev.text : null;
}
