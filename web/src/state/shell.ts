/* What the frame around every page knows: the phase the status line shows,
 * whether the engine is busy with someone else, the toasts, and the one
 * screen-reader announcement.
 *
 * The functions are plain exports, callable from anywhere (a stream handler,
 * a timer), not only from inside a component. Components read the same state
 * with `useShell`.
 */

import { create } from "zustand";

/** A phase a request passes through, or "held": it ended while the tab was hidden. */
export type PhaseKey = "working" | "queued" | "reading" | "thinking" | "writing" | "held";

export interface Phase {
  key: PhaseKey;
  /** The word shown beside the dot. */
  text: string;
}

export type ToastKind = "" | "err";

export interface Toast {
  id: number;
  message: string;
  kind: ToastKind;
  /** Fading out; removed a moment later. */
  leaving: boolean;
}

interface ShellState {
  /** Set by the chat page while a request is in flight; null means "show what
   *  the health poll says". */
  phase: Phase | null;
  /** The engine is answering somebody else. Said on the line, so cores at full
   *  load beside the word "ready" are explained rather than left to puzzle over. */
  otherBusy: boolean;
  toasts: Toast[];
  /** The single screen-reader announcement channel. */
  announcement: string;
}

export const useShell = create<ShellState>(() => ({
  phase: null,
  otherBusy: false,
  toasts: [],
  announcement: "",
}));

/* --------------------------------------------------------------- phases -- */

// The engine is just as busy with this visitor's own request, and for a beat
// after it ends. A sample asked for before this time says nothing about
// anybody else, and must not turn "ready" into "busy" under a finished answer.
let ownUntil = 0;

/** When a telemetry sample may again be read as being about someone else. */
export function ownRequestUntil(): number {
  return ownUntil;
}

// A request starting or ending changes how often the machine is sampled; the
// poller registers here so it need not wait out the slower interval to notice.
let onPhaseBoundary: (() => void) | null = null;
export function onRequestBoundary(fn: (() => void) | null): void {
  onPhaseBoundary = fn;
}

/**
 * Put the status line into a request phase — "working", "queued", "reading",
 * "writing" — with the word to show, or pass nothing to hand it back to the
 * health poll.
 */
export function setPhase(key?: PhaseKey | null, text = ""): void {
  const was = Boolean(useShell.getState().phase);
  const phase = key ? { key, text } : null;
  const changed = was !== Boolean(phase);
  if (changed && !phase) ownUntil = performance.now() + 1000;
  useShell.setState(changed ? { phase, otherBusy: false } : { phase });
  if (changed) onPhaseBoundary?.();
}

/** The phase a request is in right now, or null. */
export function currentPhase(): PhaseKey | null {
  return useShell.getState().phase?.key ?? null;
}

/**
 * A change that landed while nobody was looking stays marked until the tab is
 * seen, then clears itself. Does nothing if the tab is already visible.
 */
export function holdPhase(text: string): void {
  if (!document.hidden) return;
  setPhase("held", text);
  const seen = () => {
    if (document.hidden) return;
    document.removeEventListener("visibilitychange", seen);
    setTimeout(() => {
      if (currentPhase() === "held") setPhase(null);
    }, 4000);
  };
  document.addEventListener("visibilitychange", seen);
}

export function setOtherBusy(busy: boolean): void {
  if (useShell.getState().otherBusy !== busy) useShell.setState({ otherBusy: busy });
}

/* --------------------------------------------------------------- toasts -- */

const TOAST_MS = 4200;
const TOAST_LEAVE_MS = 220;
let toastId = 0;

/** Say something briefly above the status line. */
export function toast(message: string, kind: ToastKind = ""): void {
  const id = ++toastId;
  useShell.setState((s) => ({ toasts: [...s.toasts, { id, message, kind, leaving: false }] }));
  setTimeout(() => {
    useShell.setState((s) => ({
      toasts: s.toasts.map((t) => (t.id === id ? { ...t, leaving: true } : t)),
    }));
    setTimeout(() => {
      useShell.setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, TOAST_LEAVE_MS);
  }, TOAST_MS);
}

/* -------------------------------------------------------- announcements -- */

/**
 * Tell a screen reader about a milestone. The answer itself is not a live
 * region: its markup is rebuilt ten times a second while streaming, which
 * floods a screen reader with partial sentences. Milestones are announced
 * here instead, and the finished answer is read normally by browsing the page.
 */
export function announce(message: string): void {
  useShell.setState({ announcement: message });
}
