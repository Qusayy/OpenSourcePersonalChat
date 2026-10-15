/* The conversation on the sheet: which view is showing, who is being asked,
 * and the thread itself.
 *
 * It lives outside the page component, so a visit to Benchmark or About and
 * back finds the conversation as it was left. What changes many times a
 * second while an answer is written (its text, the figures) is not here: the
 * controller writes that straight to the page. The store holds what the
 * thread is made of.
 */

import { create } from "zustand";
import { bootstrap } from "@/lib/bootstrap";
import type { CardData, CardKind, Skill, StepEvent, StoredRun } from "@/lib/types";
import { applyStep, type Cost, type StepRow } from "./rules";

export type View = "empty" | "thread";

/** A tool result, drawn as a figure above the answer it informed. */
export interface CardEntry {
  key: number;
  card: CardKind;
  data: CardData;
  /** What the tool's step reported: its name on screen and how long it took. */
  meta: { label?: string; ms?: number | null } | null;
}

export interface UserItem {
  kind: "user";
  key: number;
  /** Kept verbatim for "Ask again": what is drawn may be folded. */
  text: string;
}

export interface BotItem {
  kind: "bot";
  key: number;
  /** Being written right now. Its text is on the page, not here, until it ends. */
  live: boolean;
  /** The answer's markdown, once there is a whole one: stored or finished. */
  content: string;
  steps: StepRow[];
  cards: CardEntry[];
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
  /** The persona chosen in this visit; null until one is, see `personaNow`. */
  persona: string | null;
  /** The armed skill: applies to the next message only. */
  skill: Skill | null;
  conversationId: string | null;
  items: ThreadItem[];
  streaming: boolean;
}

const PERSONA_KEY = "aurora.persona";

/** The persona last chosen here, or the server's default. Stored as the bare
 *  id, the same value the previous interface wrote, so a returning visitor
 *  keeps their choice. */
function storedPersona(): string {
  try {
    const kept = localStorage.getItem(PERSONA_KEY);
    if (kept) return kept;
  } catch {
    /* storage is off; the default will do */
  }
  return bootstrap().defaultPersona;
}

export const useChat = create<ChatState>(() => ({
  view: "empty",
  // Not read here: nothing may touch storage or the bootstrap when the module
  // is loaded, only once the page is up.
  persona: null,
  skill: null,
  conversationId: null,
  items: [],
  streaming: false,
}));

let nextKey = 0;
/** A key for a new thread item or card. Never reused, so React never mistakes
 *  a new answer for an old one. */
export const newKey = (): number => ++nextKey;

const set = useChat.setState;
const get = useChat.getState;

/** Who the next message goes to. */
export function personaNow(): string {
  return get().persona ?? storedPersona();
}

/** The same, for a component. */
export function usePersona(): string {
  return useChat((s) => s.persona) ?? storedPersona();
}

/* -------------------------------------------------------------- actions -- */

/** Choose a persona, and remember it for the next visit. */
export function choosePersona(id: string): void {
  try {
    localStorage.setItem(PERSONA_KEY, id);
  } catch {
    /* storage is off; the choice lasts as long as the page */
  }
  set({ persona: id });
}

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
    steps: [],
    cards: [],
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

/** One `step` event of the stream, applied to an answer's route line. */
export function stepBot(key: number, data: StepEvent): void {
  patchBot(key, (item) => ({ ...item, steps: applyStep(item.steps, data) }));
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
