/* What the chat page does: send a question and follow its answer, stop it,
 * start over, open a stored conversation, delete one.
 *
 * Plain functions, not hooks. A request must start from the event that asked
 * for it and run to its end whatever the page is drawing meanwhile, and the
 * answer's text arrives far faster than anything should be rendered. So the
 * thread's structure goes through the store (run.ts) and React draws it; the
 * text of the answer being written goes straight to its element, on a 100 ms
 * tick, through the streaming renderer.
 *
 * The few elements that needs are handed over by the components that render
 * them (`dom`, below).
 */

import { flushSync } from "react-dom";
import { create } from "zustand";
import { ApiError, api, CHAT_URL } from "@/lib/api";
import { aurora } from "@/lib/aurora";
import { bootstrap } from "@/lib/bootstrap";
import { fmt, fmtInt } from "@/lib/format";
import { reduced } from "@/lib/frame";
import { Meter } from "@/lib/meter";
import { proseRenderer } from "@/lib/prose-stream";
import { sseFetch } from "@/lib/sse";
import type { CardKind, ChatEvents, Skill } from "@/lib/types";
import { livePhase, resetFigures, setContext, showFigures, traceSample } from "@/shell/figures";
import { refreshConversations } from "@/state/queries";
import { announce, holdPhase, toast } from "@/state/shell";
import { queueWording, trimmedWording } from "./rules";
import {
  addBot,
  addNotice,
  addUser,
  type BotItem,
  botItem,
  type CardEntry,
  newKey,
  patchBot,
  patchNotice,
  personaNow,
  removeItem,
  stepBot,
  type ThreadItem,
  useChat,
  type View,
} from "./run";

/* ------------------------------------------------------- the page's DOM -- */

/** Elements the controller reads or writes directly. Each is registered by
 *  the component that renders it and is null while the page is not on screen. */
export const dom = {
  /** The thread's scroller. */
  wrap: null as HTMLElement | null,
  input: null as HTMLTextAreaElement | null,
  rail: null as HTMLDialogElement | null,
  /** The text element of each answer on the sheet, by the answer's key. */
  prose: new Map<number, HTMLElement>(),
};

/** Answer elements the streaming renderer has written into. Such an element
 *  keeps what the renderer left in it; any other is drawn from the stored text. */
export const streamed = new WeakSet<Element>();

function scrollBottom(): void {
  if (dom.wrap) dom.wrap.scrollTop = dom.wrap.scrollHeight;
}

function nearBottom(): boolean {
  const el = dom.wrap;
  if (!el) return false;
  return el.scrollHeight - el.scrollTop - el.clientHeight < 140;
}

/* ------------------------------------------------------- history drawer -- */

interface RailState {
  open: boolean;
  /** Rows held unseen for the moment the fresh list takes to come back. */
  pending: boolean;
  /** Rows arriving one after another, once per opening. */
  stagger: boolean;
}

export const useRail = create<RailState>(() => ({ open: false, pending: false, stagger: false }));

/** Open or close the history drawer. A native dialog, so the focus trap,
 *  Escape and the return of focus to the opener are the browser's job. */
export function setRail(open: boolean): void {
  const rail = dom.rail;
  if (!rail) return;
  if (open && !rail.open) rail.showModal();
  else if (!open && rail.open) rail.close();
  useRail.setState({ open });
}

async function refreshList(): Promise<void> {
  try {
    await refreshConversations();
  } catch {
    /* the rail is a convenience; a failed refresh is not worth a toast */
  }
}

let staggerTimer: ReturnType<typeof setTimeout> | undefined;

/**
 * Open the history. The list is fetched as the drawer opens, so the times are
 * current and a conversation started in another tab is listed. The rows are
 * held unseen for the moment that takes (the drawer is still sliding in) and
 * then arrive as a list, once: showing the old rows, replacing them and
 * animating the new ones would be the same list entering twice.
 */
export async function openHistory(): Promise<void> {
  setRail(true);
  clearTimeout(staggerTimer);
  useRail.setState({ stagger: false, pending: true });
  await Promise.race([refreshList(), new Promise((done) => setTimeout(done, 300))]);
  useRail.setState({ pending: false });
  if (reduced()) return;
  useRail.setState({ stagger: true });
  staggerTimer = setTimeout(() => useRail.setState({ stagger: false }), 700);
}

/* ------------------------------------------------------------- the view -- */

/**
 * Switch between the empty sheet and the thread. One value carries it, so a
 * view can never be half applied.
 *
 * `animate` is for the one change a visitor causes by sending the first
 * question: the composer travels to the foot of the sheet. Loading a stored
 * conversation or starting a new one just changes.
 */
export function setView(view: View, { animate = false } = {}): void {
  if (useChat.getState().view === view) return;
  const apply = () => {
    // Committed inside the callback: the browser photographs the page before
    // and after it, and the new view has to be on screen when it returns.
    flushSync(() => useChat.setState({ view }));
    // The thread was display:none until now, so nothing could scroll it.
    scrollBottom();
  };
  if (animate && !reduced() && document.startViewTransition) document.startViewTransition(apply);
  else apply();
}

/** Arm a skill for the next message, or pass null to disarm. */
export function setSkill(skill: Skill | null): void {
  useChat.setState({ skill });
  if (skill) dom.input?.focus();
}

/* ------------------------------------------------------------ streaming -- */

const meter = new Meter({
  onSample: ({ rate, total, at }) => {
    // The meter ticks from the moment a request is sent. Until a token has
    // arrived there is no rate to report, and "0.0 tok/s" would be a claim.
    if (!total) return;
    traceSample(rate, at);
  },
});

// The run in flight. Its id comes with the stream's first event, so from then
// on Stop has something to name at every stage: queued, choosing a tool,
// waiting on a page, writing. Before that first event there is only a
// connection to close.
let requestId: string | null = null;
let connection: AbortController | null = null;

function setMode(streaming: boolean): void {
  useChat.setState({ streaming });
  livePhase(streaming ? "working" : null);
}

/** Fade a transient notice out before removing it. */
function dismiss(key: number | null): void {
  if (key === null) return;
  if (reduced()) {
    removeItem(key);
    return;
  }
  patchNotice(key, (n) => ({ ...n, leaving: true }));
  setTimeout(() => removeItem(key), 160);
}

/** Change the store and have React draw it before the next line runs. The
 *  controller then measures or scrolls a page that already shows the change. */
function commit<T>(change: () => T): T {
  let out!: T;
  flushSync(() => {
    out = change();
  });
  return out;
}

function notice(...args: Parameters<typeof addNotice>): number {
  const { key } = commit(() => addNotice(...args));
  scrollBottom();
  return key;
}

/** An error the user can act on: says what broke and offers the way back. */
function errorNotice(message: string, retryText: string | null): void {
  notice("err", message, retryText);
  announce(message);
}

/** Ask the server to stop this run, whatever it is doing. Until the stream has
 *  said which run this is, there is nothing to name and this does nothing. */
export async function stopGeneration(): Promise<void> {
  if (!requestId) return;
  try {
    await api.cancel(requestId);
  } catch {
    /* the stream's own teardown stops it anyway */
  }
}

/**
 * The page is being left with an answer in flight: stop it, and keep what was
 * written, the same as pressing Stop. A request whose stream has not said
 * anything yet has no id to stop by, so its connection is closed instead.
 */
export function leave(): void {
  if (!useChat.getState().streaming) return;
  if (requestId) void stopGeneration();
  else connection?.abort();
}

export async function send(text: string): Promise<void> {
  if (useChat.getState().streaming) return;
  setRail(false);
  commit(() => addUser(text));
  scrollBottom();
  setView("thread", { animate: true });

  const skill = useChat.getState().skill;
  const bot: BotItem = commit(() => addBot({ live: true }));
  scrollBottom();
  // The element React drew for this answer's text, empty. From here on it is
  // the renderer's: React never looks inside it.
  const host = dom.prose.get(bot.key) ?? document.createElement("div");
  streamed.add(host);
  const prose = proseRenderer(host);

  let queueKey: number | null = null;
  let queuedAs: string | null = null; // the queue wording currently on the status line
  let finishedUnseen = false;
  // What each tool step reported, kept for the figure that follows it.
  const stepMeta = new Map<string, { label: string; ms: number | undefined }>();
  let raw = "";
  let dirty = false;
  let caretOn = true;

  const paint = () => {
    // A backgrounded tab still receives tokens; there is no point laying them
    // out until someone can see them.
    if (!dirty || document.hidden) return;
    dirty = false;
    // Read first, then write: measuring the scroll position after the DOM has
    // grown would force a second synchronous layout.
    const stick = nearBottom();
    prose.update(raw, caretOn);
    if (stick) scrollBottom();
  };
  // 100ms is well under the eye's threshold for text appearing.
  const painter = setInterval(paint, 100);

  setMode(true);
  meter.start();
  resetFigures();

  const controller = new AbortController();
  connection = controller;

  try {
    await sseFetch<ChatEvents>(CHAT_URL, {
      body: {
        message: text,
        conversation_id: useChat.getState().conversationId,
        persona: personaNow(),
        skill: skill ? skill.id : null,
      },
      signal: controller.signal,
      onEvent: (event, data) => {
        if (event === "conversation") {
          // From here on the run can be stopped by name.
          if (data.request_id) requestId = data.request_id;
          useChat.setState({ conversationId: data.id });
          if (data.created) void refreshList();
        } else if (event === "queue") {
          if (queueKey === null) queueKey = notice("queue", "");
          const key = queueKey;
          const { text: queueText, ahead } = queueWording(data.position);
          patchNotice(key, (n) => ({ ...n, text: queueText }));
          announce(queueText);
          if (queuedAs !== ahead) {
            queuedAs = ahead;
            livePhase("queued", ahead);
          }
        } else if (event === "step") {
          stepBot(bot.key, data);
          if (data.kind === "tool") stepMeta.set(data.id, { label: data.label, ms: data.ms });
          if (data.status === "failed") {
            announce(`${data.label} failed: ${data.detail || "unknown error"}`);
          }
        } else if (event === "card") {
          const entry: CardEntry = {
            key: newKey(),
            card: data.card,
            data: data.data,
            meta: stepMeta.get(data.step_id) ?? null,
          };
          commit(() => patchBot(bot.key, (b) => ({ ...b, cards: [...b.cards, entry] })));
          if (nearBottom()) scrollBottom();
        } else if (event === "trimmed") {
          notice("", trimmedWording(data.dropped));
        } else if (event === "start") {
          requestId = data.request_id;
          livePhase("reading"); // holds until the first token lands
          dismiss(queueKey);
          queueKey = null;
          setContext(data.prompt_tokens);
        } else if (event === "token") {
          // No layout reads here — the tick above batches one read and one
          // write per frame instead of a pair per token.
          // The release: prefill is over, the model is talking.
          if (!raw) {
            livePhase("writing");
            showFigures(data.ttft_ms);
          }
          raw += data.t;
          dirty = true;
          meter.token(1);
          aurora.pulse();
        } else if (event === "done") {
          caretOn = false;
          dirty = false;
          prose.finish(raw);
          // The footer adds height below the last line of the answer. If the
          // reader was at the bottom they stay there; without this the figures
          // and the actions land just out of sight.
          const stick = nearBottom();
          const answer = raw;
          commit(() => patchBot(bot.key, (b) => ({ ...b, content: answer, cost: data })));
          if (stick) scrollBottom();
          setContext((data.prompt_tokens || 0) + (data.completion_tokens || 0));
          finishedUnseen = document.hidden;
          announce(
            data.cancelled
              ? "Generation stopped."
              : `Answer complete — ${fmtInt(data.completion_tokens)} tokens at ${fmt(
                  data.gen_tps,
                  1,
                )} per second.`,
          );
          void refreshList();
        } else if (event === "error") {
          caretOn = false;
          prose.finish(raw);
          errorNotice(data.message || "The model could not answer that.", text);
        }
      },
    });
  } catch (err) {
    caretOn = false;
    dirty = false;
    prose.finish(raw);
    const error = err instanceof Error ? err : new Error(String(err));
    if (error.name !== "AbortError") {
      errorNotice(
        navigator.onLine ? error.message : "You appear to be offline — the answer stopped partway.",
        text,
      );
    }
  } finally {
    clearInterval(painter);
    caretOn = false;
    if (raw) prose.finish(raw);
    dismiss(queueKey);
    meter.stop();
    setMode(false);
    resetFigures();
    // Finished while the tab was in the background: say so until it is seen.
    if (finishedUnseen) holdPhase("answer ready");
    requestId = null;
    connection = null;
    const final = raw;
    if (!final.trim() && !botItem(bot.key)?.cards.length) removeItem(bot.key);
    else patchBot(bot.key, (b) => ({ ...b, live: false, content: final }));
    setSkill(null); // a skill applies to one message, not the whole thread
    dom.input?.focus();
  }
}

/* -------------------------------------------------------- conversations -- */

// What a tool is called on screen, for cards replayed from a stored
// conversation, where only its registry name was kept.
let toolLabels: Map<string, string> | null = null;
function toolLabel(name: string): string {
  toolLabels ??= new Map(bootstrap().tools.map((t) => [t.name, t.label || t.name]));
  return toolLabels.get(name) ?? name;
}

export async function loadConversation(cid: string): Promise<void> {
  if (useChat.getState().streaming) return;
  try {
    const convo = await api.conversation(cid).catch((err: unknown) => {
      throw err instanceof ApiError ? new Error("Conversation not found") : err;
    });

    const items: ThreadItem[] = [];
    let ctx = 0;
    for (const m of convo.messages) {
      if (m.role === "user") {
        items.push({ kind: "user", key: newKey(), text: m.content });
        continue;
      }
      // Replay stored tool cards, so reloading a conversation is not lossy.
      const cards: CardEntry[] = (m.tool_calls || []).map((call) => {
        const meta = { label: toolLabel(call.tool), ms: call.ms };
        return call.ok && call.card
          ? { key: newKey(), card: call.card, data: call.data, meta }
          : { key: newKey(), card: "error" as CardKind, data: { message: call.summary }, meta };
      });
      items.push({
        kind: "bot",
        key: newKey(),
        live: false,
        content: m.content,
        steps: [],
        cards,
        cost:
          m.gen_tps != null || m.ttft_ms != null
            ? {
                gen_tps: m.gen_tps,
                ttft_ms: m.ttft_ms,
                completion_tokens: m.completion_tokens,
              }
            : null,
      });
      ctx = (m.prompt_tokens || 0) + (m.completion_tokens || 0) || ctx;
    }
    commit(() =>
      useChat.setState((s) => ({
        conversationId: cid,
        persona: convo.persona || s.persona,
        items,
      })),
    );
    setContext(ctx);
    setRail(false);
    setView("thread");
    scrollBottom();
    void refreshList();
  } catch (err) {
    toast(err instanceof Error ? err.message : String(err), "err");
  }
}

export function newChat(): void {
  if (useChat.getState().streaming) return;
  useChat.setState({ conversationId: null, items: [] });
  setView("empty");
  setSkill(null);
  setContext(0);
  setRail(false);
  void refreshList();
  dom.input?.focus();
}

export async function deleteConversation(id: string, title: string): Promise<void> {
  try {
    await api.deleteConversation(id);
    announce(`Deleted conversation ${title}`);
  } catch (err) {
    const why =
      err instanceof ApiError
        ? `Server returned ${err.status}`
        : err instanceof Error
          ? err.message
          : String(err);
    toast(`Could not delete that conversation — ${why}`, "err");
    return;
  }
  if (useChat.getState().conversationId === id) newChat();
  void refreshList();
}
