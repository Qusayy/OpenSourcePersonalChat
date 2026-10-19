/* What the chat page does: send a question and follow its answer, stop it,
 * start over, open a stored conversation, delete one.
 *
 * Plain functions, not hooks. A request must start from the event that asked
 * for it and run to its end whatever the page is drawing meanwhile, and the
 * answer's text arrives far faster than anything should be rendered. So the
 * thread's structure goes through the store (run.ts) and React draws it: the
 * answer's parts, in the order they were written. The text of the part being
 * written, what the model is thinking or saying, goes straight to its
 * element on a 100 ms tick, through the streaming renderer.
 *
 * The few elements that needs are handed over by the components that render
 * them (`dom`, below).
 */

import { flushSync } from "react-dom";
import { create } from "zustand";
import { WIDE } from "@/features/project/ProjectPanel";
import {
  fileEvent,
  flushProject,
  loadProject,
  projectConversation,
  resetProject,
  setProjectOpen,
  useProject,
} from "@/features/project/store";
import { ApiError, api, CHAT_URL } from "@/lib/api";
import { aurora } from "@/lib/aurora";
import { bootstrap } from "@/lib/bootstrap";
import { fmt, fmtInt } from "@/lib/format";
import { reduced } from "@/lib/frame";
import { Meter } from "@/lib/meter";
import { type ProseRenderer, proseRenderer } from "@/lib/prose-stream";
import { sseFetch } from "@/lib/sse";
import type { ChatEvents } from "@/lib/types";
import {
  type LivePhase,
  livePhase,
  resetFigures,
  setContext,
  showFigures,
  traceSample,
} from "@/shell/figures";
import { refreshConversations } from "@/state/queries";
import { announce, holdPhase, toast } from "@/state/shell";
import { queueWording, trimmedWording } from "./rules";
import {
  addBot,
  addNotice,
  addPart,
  addUser,
  type BotItem,
  botItem,
  type CardPart,
  newKey,
  type Part,
  patchBot,
  patchNotice,
  patchPart,
  removeItem,
  removePart,
  replacePart,
  stepBot,
  storedParts,
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
  /** The element each written part of an answer is set in, by the part's
   *  key: the text of something it said, or of what it thought. */
  parts: new Map<number, HTMLElement>(),
};

/** Part elements the controller has written into while the answer streamed.
 *  Such an element keeps what was left in it; any other is drawn from the
 *  stored text. */
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
// on Stop has something to name at every stage: queued, thinking, waiting on
// a tool, writing. Before that first event there is only a connection to
// close.
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

/** The part of an answer being written now: what the model is thinking, or
 *  what it is saying. */
interface Writing {
  kind: "thought" | "text";
  key: number;
  raw: string;
  host: HTMLElement;
  /** For what it says; what it thinks is plain text. */
  prose: ProseRenderer | null;
  since: number;
}

export async function send(text: string): Promise<void> {
  if (useChat.getState().streaming) return;
  setRail(false);
  commit(() => addUser(text));
  scrollBottom();
  setView("thread", { animate: true });

  const bot: BotItem = commit(() => addBot({ live: true }));
  scrollBottom();

  let queueKey: number | null = null;
  let queuedAs: string | null = null; // the queue wording currently on the status line
  let finishedUnseen = false;
  // What each tool step reported, kept for the figure that follows it.
  const stepMeta = new Map<string, { label: string; ms: number | undefined }>();
  // The part being written, and the text of every part said so far: the
  // answer that Copy copies.
  let writing: Writing | null = null;
  const said: string[] = [];
  // A reply is under way: a file that opens now is one the model is writing.
  let replying = false;
  // The part standing in for that file, until its call's card arrives.
  let fileKey: number | null = null;
  let figures = false;
  let phase: LivePhase | null = null;
  let dirty = false;
  let wroteFiles = false;
  let caretOn = true;

  /** Say a phase on the line, unless it already says it. */
  const enter = (key: LivePhase) => {
    if (phase === key) return;
    phase = key;
    livePhase(key);
  };

  /** The part being written has ended: set it once, whole, and keep it. A
   *  part that said nothing is taken out again. */
  const close = () => {
    const w = writing;
    if (!w) return;
    writing = null;
    dirty = false;
    const words = w.raw.trim();
    if (w.prose) {
      w.prose.finish(w.raw);
      if (words) said.push(words);
    } else {
      w.host.textContent = words;
    }
    if (!words) {
      w.prose?.dispose();
      removePart(bot.key, w.key);
      return;
    }
    const ms = performance.now() - w.since;
    patchPart(bot.key, w.key, (part) =>
      part.kind === "thought" ? { ...part, text: w.raw, ms } : { ...part, text: w.raw },
    );
  };

  /** Begin a part of the answer, below everything written so far. */
  const open = (kind: Writing["kind"]): Writing => {
    close();
    const key = newKey();
    const part: Part =
      kind === "thought" ? { kind, key, text: "", ms: null } : { kind, key, text: "" };
    commit(() => addPart(bot.key, part));
    // The element React drew for it, empty. From here on it is the
    // controller's: React never looks inside it.
    const host = dom.parts.get(key) ?? document.createElement("div");
    streamed.add(host);
    const next: Writing = {
      kind,
      key,
      raw: "",
      host,
      prose: kind === "text" ? proseRenderer(host) : null,
      since: performance.now(),
    };
    writing = next;
    return next;
  };

  /** Add a part that is whole as it arrives: a card, or a file being written. */
  const place = (part: Part, instead: number | null = null) => {
    close();
    const stick = nearBottom();
    commit(() => (instead === null ? addPart(bot.key, part) : replacePart(bot.key, instead, part)));
    if (stick) scrollBottom();
  };

  /** A piece of what the model thinks or says. */
  const write = (kind: Writing["kind"], piece: string, ttft?: number) => {
    // Space before anything is said is nothing to show.
    if (writing?.kind !== kind && !piece.trim()) return;
    const w = writing?.kind === kind ? writing : open(kind);
    // The release: prefill is over, the model is talking.
    if (!figures) {
      figures = true;
      showFigures(ttft);
    }
    enter(kind === "thought" ? "thinking" : "writing");
    w.raw += piece;
    dirty = true;
  };

  const paint = () => {
    // A backgrounded tab still receives tokens; there is no point laying them
    // out until someone can see them.
    flushProject();
    const w = writing;
    if (!w || !dirty || document.hidden) return;
    dirty = false;
    // Read first, then write: measuring the scroll position after the DOM has
    // grown would force a second synchronous layout.
    const stick = nearBottom();
    if (w.prose) w.prose.update(w.raw, caretOn);
    else w.host.textContent = w.raw.trim();
    if (stick) scrollBottom();
  };
  // 100ms is well under the eye's threshold for text appearing.
  const painter = setInterval(paint, 100);

  setMode(true);
  phase = "working";
  meter.start();
  resetFigures();

  const controller = new AbortController();
  connection = controller;

  try {
    await sseFetch<ChatEvents>(CHAT_URL, {
      body: {
        message: text,
        conversation_id: useChat.getState().conversationId,
      },
      signal: controller.signal,
      onEvent: (event, data) => {
        if (event === "conversation") {
          // From here on the run can be stopped by name.
          if (data.request_id) requestId = data.request_id;
          useChat.setState({ conversationId: data.id });
          projectConversation(data.id);
          if (data.created) void refreshList();
        } else if (event === "queue") {
          if (queueKey === null) queueKey = notice("queue", "");
          const key = queueKey;
          const { text: queueText, ahead } = queueWording(data.position);
          patchNotice(key, (n) => ({ ...n, text: queueText }));
          announce(queueText);
          if (queuedAs !== ahead) {
            queuedAs = ahead;
            phase = "queued";
            livePhase("queued", ahead);
          }
        } else if (event === "step") {
          // A run keeps the engine for all its replies and waits for it once,
          // before its first step. A step arriving while the queue notice is
          // up means that wait is over.
          if (queueKey !== null) {
            dismiss(queueKey);
            queueKey = null;
            queuedAs = null;
            enter("working");
          }
          stepBot(bot.key, data);
          if (data.kind === "model") {
            // A new reply: what it says starts a part of its own.
            replying = data.status === "running";
            if (replying) close();
          } else if (data.kind === "tool") {
            stepMeta.set(data.id, { label: data.label, ms: data.ms });
            if (data.status === "running") {
              close();
              enter("working");
            }
          }
          if (data.status === "failed") {
            announce(`${data.label} failed: ${data.detail || "unknown error"}`);
          }
        } else if (event === "card") {
          // Where it came: after what was said before the call, and before
          // anything said after it. It takes the place of the file its call
          // was writing, if it was writing one.
          const part: CardPart = {
            kind: "card",
            key: newKey(),
            card: data.card,
            data: data.data,
            meta: stepMeta.get(data.step_id) ?? null,
          };
          place(part, fileKey);
          fileKey = null;
        } else if (event === "trimmed") {
          notice("", trimmedWording(data.dropped, data.cut));
        } else if (event === "file") {
          // The first file of a run opens the panel beside the thread where
          // there is room for it; on a narrow screen the top bar offers it.
          wroteFiles = true;
          if (fileEvent(data) && window.matchMedia(WIDE).matches) setProjectOpen(true);
          // A file the model is writing as it replies is said in the reply,
          // where it is being written. One a call reports after it was made
          // has its card already.
          if (data.type === "open" && replying && fileKey === null) {
            const part: Part = { kind: "file", key: newKey(), path: data.path };
            place(part);
            fileKey = part.key;
            enter("writing");
          }
        } else if (event === "start") {
          requestId = data.request_id;
          enter("reading"); // holds until the first token lands
          dismiss(queueKey);
          queueKey = null;
          setContext(data.prompt_tokens);
        } else if (event === "think" || event === "token") {
          // No layout reads here — the tick above batches one read and one
          // write per frame instead of a pair per token.
          write(event === "think" ? "thought" : "text", data.t, data.ttft_ms);
          meter.token(1);
          aurora.pulse();
        } else if (event === "done") {
          caretOn = false;
          close();
          // The footer adds height below the last line of the answer. If the
          // reader was at the bottom they stay there; without this the figures
          // and the actions land just out of sight.
          const stick = nearBottom();
          const content = said.join("\n\n");
          commit(() =>
            patchBot(bot.key, (b) => ({
              ...b,
              content,
              cost: data,
              correction: data.correction ?? null,
            })),
          );
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
        } else if (event === "run") {
          const run = data;
          commit(() => patchBot(bot.key, (b) => ({ ...b, run })));
        } else if (event === "error") {
          caretOn = false;
          close();
          errorNotice(data.message || "The model could not answer that.", text);
        }
      },
    });
  } catch (err) {
    caretOn = false;
    close();
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
    close();
    // A file whose call was never made is not part of the answer.
    if (fileKey !== null) removePart(bot.key, fileKey);
    dismiss(queueKey);
    meter.stop();
    setMode(false);
    resetFigures();
    // Finished while the tab was in the background: say so until it is seen.
    if (finishedUnseen) holdPhase("answer ready");
    requestId = null;
    connection = null;
    // The project as it was kept, in place of what the stream showed of it.
    const projectOf = useProject.getState().cid;
    if (wroteFiles && projectOf) void loadProject(projectOf);
    const content = said.join("\n\n");
    if (!botItem(bot.key)?.parts.length) removeItem(bot.key);
    else patchBot(bot.key, (b) => ({ ...b, live: false, steps: [], content }));
    dom.input?.focus();
  }
}

/* -------------------------------------------------------- conversations -- */

// What a tool is called on screen, for cards replayed from a stored
// conversation, where only its registry name was kept. A tool this server
// does not offer now (no network, no sandbox) is named from that name:
// "run_file" is "Run file".
let toolLabels: Map<string, string> | null = null;
export function toolLabel(name: string): string {
  toolLabels ??= new Map(bootstrap().tools.map((t) => [t.name, t.label || t.name] as const));
  const known = toolLabels.get(name);
  if (known) return known;
  const words = name.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : name;
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
      items.push({
        kind: "bot",
        key: newKey(),
        live: false,
        content: m.content,
        // Its thoughts, words and cards in the order they were said, so
        // reloading a conversation is not lossy.
        parts: storedParts(m, toolLabel),
        steps: [],
        correction: m.correction ?? null,
        run: m.run ?? null,
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
    commit(() => useChat.setState({ conversationId: cid, items }));
    setContext(ctx);
    if (convo.project?.length) void loadProject(cid);
    else resetProject(cid);
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
  resetProject();
  setView("empty");
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
