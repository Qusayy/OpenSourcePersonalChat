import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  announce,
  currentPhase,
  holdPhase,
  onRequestBoundary,
  ownRequestUntil,
  setOtherBusy,
  setPhase,
  toast,
  useShell,
} from "./shell";

beforeEach(() => {
  vi.useFakeTimers();
  useShell.setState({ phase: null, otherBusy: false, toasts: [], announcement: "" });
});
afterEach(() => {
  onRequestBoundary(null);
  vi.useRealTimers();
});

describe("setPhase", () => {
  it("sets and clears the phase the status line shows", () => {
    setPhase("reading", "reading");
    expect(currentPhase()).toBe("reading");
    expect(useShell.getState().phase).toEqual({ key: "reading", text: "reading" });
    setPhase(null);
    expect(currentPhase()).toBeNull();
  });

  it("tells the poller when a request starts and when it ends, not in between", () => {
    const boundary = vi.fn();
    onRequestBoundary(boundary);
    setPhase("working", "working");
    setPhase("reading", "reading");
    setPhase("writing", "writing");
    expect(boundary).toHaveBeenCalledTimes(1);
    setPhase(null);
    expect(boundary).toHaveBeenCalledTimes(2);
  });

  it("forgets that someone else was busy when our own request starts", () => {
    setOtherBusy(true);
    setPhase("working", "working");
    expect(useShell.getState().otherBusy).toBe(false);
  });

  it("marks the second after a request as still its own", () => {
    setPhase("writing", "writing");
    const before = performance.now();
    setPhase(null);
    expect(ownRequestUntil()).toBeGreaterThanOrEqual(before + 1000);
  });
});

describe("holdPhase", () => {
  const hide = (hidden: boolean) => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    document.dispatchEvent(new Event("visibilitychange"));
  };
  afterEach(() => hide(false));

  it("does nothing while the tab is being looked at", () => {
    hide(false);
    holdPhase("answer ready");
    expect(currentPhase()).toBeNull();
  });

  it("stays until the tab is seen, then clears four seconds later", () => {
    hide(true);
    holdPhase("answer ready");
    expect(useShell.getState().phase).toEqual({ key: "held", text: "answer ready" });
    vi.advanceTimersByTime(60_000);
    expect(currentPhase()).toBe("held");
    hide(false);
    vi.advanceTimersByTime(3999);
    expect(currentPhase()).toBe("held");
    vi.advanceTimersByTime(1);
    expect(currentPhase()).toBeNull();
  });

  it("does not clear a new request that started in those four seconds", () => {
    hide(true);
    holdPhase("answer ready");
    hide(false);
    setPhase("working", "working");
    vi.advanceTimersByTime(5000);
    expect(currentPhase()).toBe("working");
  });
});

describe("toast", () => {
  it("shows, fades after 4.2 seconds, and is gone a moment later", () => {
    toast("Model warm — ready to go");
    toast("Clipboard blocked by the browser", "err");
    expect(useShell.getState().toasts.map((t) => [t.message, t.kind, t.leaving])).toEqual([
      ["Model warm — ready to go", "", false],
      ["Clipboard blocked by the browser", "err", false],
    ]);
    vi.advanceTimersByTime(4200);
    expect(useShell.getState().toasts.every((t) => t.leaving)).toBe(true);
    vi.advanceTimersByTime(220);
    expect(useShell.getState().toasts).toEqual([]);
  });
});

describe("announce", () => {
  it("replaces what the screen reader was last told", () => {
    announce("Next in line — starting shortly.");
    announce("Generation stopped.");
    expect(useShell.getState().announcement).toBe("Generation stopped.");
  });
});
