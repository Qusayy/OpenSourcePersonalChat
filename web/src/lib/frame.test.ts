import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Frame = typeof import("./frame");

// The frame loop and the reduced-motion answer are module state, so every test
// loads the module afresh.
async function load(): Promise<Frame> {
  vi.resetModules();
  return import("./frame");
}

function motion(reduce: boolean) {
  const matchMedia = vi.fn((query: string) => ({
    matches: query.includes("prefers-reduced-motion") ? reduce : false,
    media: query,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal("matchMedia", matchMedia);
  return matchMedia;
}

/** Animation frames that fire only when the test says so. */
function frames() {
  let queue = new Map<number, FrameRequestCallback>();
  let id = 0;
  const request = vi.fn((cb: FrameRequestCallback) => {
    queue.set(++id, cb);
    return id;
  });
  const cancel = vi.fn((handle: number) => void queue.delete(handle));
  vi.stubGlobal("requestAnimationFrame", request);
  vi.stubGlobal("cancelAnimationFrame", cancel);
  return {
    request,
    cancel,
    pending: () => queue.size,
    /** Run the frame that is due, as if the screen refreshed at `now`. */
    at(now: number) {
      const due = queue;
      queue = new Map();
      for (const cb of due.values()) cb(now);
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reduced", () => {
  it("is what the visitor's setting says", async () => {
    motion(true);
    expect((await load()).reduced()).toBe(true);
    motion(false);
    expect((await load()).reduced()).toBe(false);
  });

  it("asks once, on first use, and holds the answer", async () => {
    const asked = motion(true);
    const frame = await load();
    expect(asked).not.toHaveBeenCalled(); // importing reads nothing
    expect(frame.reduced()).toBe(true);
    motion(false); // the setting changes mid-session
    expect(frame.reduced()).toBe(true);
    expect(asked).toHaveBeenCalledTimes(1);
  });

  it("is false where there is no matchMedia to ask", async () => {
    vi.stubGlobal("matchMedia", undefined);
    expect((await load()).reduced()).toBe(false);
  });
});

describe("enter", () => {
  const element = () => {
    const el = document.createElement("div");
    const animate = vi.fn();
    Object.defineProperty(el, "animate", { value: animate, configurable: true });
    return { el, animate };
  };

  it("brings an element up from 8px below, on the arrival curve", async () => {
    motion(false);
    const { enter, EASE } = await load();
    const { el, animate } = element();
    enter(el);
    expect(animate).toHaveBeenCalledWith(
      [
        { opacity: 0, translate: "0 8px" },
        { opacity: 1, translate: "0 0" },
      ],
      { duration: 240, delay: 0, easing: EASE, fill: "backwards" },
    );
    expect(EASE).toBe("cubic-bezier(0.16, 1, 0.3, 1)");
  });

  it("takes a delay and a distance", async () => {
    motion(false);
    const { enter } = await load();
    const { el, animate } = element();
    enter(el, { delay: 120, distance: 6 });
    const [keyframes, options] = animate.mock.calls[0]!;
    expect(keyframes[0]).toEqual({ opacity: 0, translate: "0 6px" });
    expect(options.delay).toBe(120);
  });

  it("does nothing under reduced motion: the element is simply there", async () => {
    motion(true);
    const { enter } = await load();
    const { el, animate } = element();
    enter(el);
    expect(animate).not.toHaveBeenCalled();
  });

  it("does nothing where elements cannot animate", async () => {
    motion(false);
    const { enter } = await load();
    const el = document.createElement("div");
    Object.defineProperty(el, "animate", { value: undefined, configurable: true });
    expect(() => enter(el)).not.toThrow();
  });
});

describe("onFrame", () => {
  let clock: number;

  beforeEach(() => {
    clock = 1000;
    vi.spyOn(performance, "now").mockImplementation(() => clock);
  });

  it("asks for no frames until something subscribes", async () => {
    const raf = frames();
    await load();
    expect(raf.request).not.toHaveBeenCalled();
  });

  it("calls a subscriber every frame with the time and the step since the last", async () => {
    const raf = frames();
    const { onFrame } = await load();
    const fn = vi.fn();
    onFrame(fn);
    raf.at(1016);
    raf.at(1033);
    expect(fn.mock.calls).toEqual([
      [1016, 16],
      [1033, 17],
    ]);
  });

  it("caps the step, so a tab back from the background does not deliver one huge one", async () => {
    const raf = frames();
    const { onFrame } = await load();
    const fn = vi.fn();
    onFrame(fn);
    raf.at(1016);
    raf.at(9000);
    expect(fn.mock.calls[1]).toEqual([9000, 64]);
  });

  it("runs one loop for every subscriber", async () => {
    const raf = frames();
    const { onFrame } = await load();
    const a = vi.fn();
    const b = vi.fn();
    onFrame(a);
    onFrame(b);
    expect(raf.request).toHaveBeenCalledTimes(1);
    raf.at(1016);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
    expect(raf.request).toHaveBeenCalledTimes(2);
  });

  it("stops asking for frames once the last subscriber has gone", async () => {
    const raf = frames();
    const { onFrame } = await load();
    const fn = vi.fn();
    const off = onFrame(fn);
    raf.at(1016);
    off();
    raf.at(1032); // the frame already asked for arrives, finds nobody, and asks for no more
    expect(fn).toHaveBeenCalledTimes(1);
    expect(raf.pending()).toBe(0);
  });

  it("lets a subscriber leave from inside its own frame", async () => {
    const raf = frames();
    const { onFrame } = await load();
    const stay = vi.fn();
    const off = onFrame(() => off());
    onFrame(stay);
    raf.at(1016);
    raf.at(1032);
    expect(stay).toHaveBeenCalledTimes(2);
  });

  it("starts again after a rest, measuring from the new start", async () => {
    const raf = frames();
    const { onFrame } = await load();
    const off = onFrame(vi.fn());
    raf.at(1016);
    off();
    raf.at(1032);
    clock = 60_000;
    const fn = vi.fn();
    onFrame(fn);
    raf.at(60_016);
    expect(fn).toHaveBeenCalledWith(60_016, 16);
  });

  it("stopFrames drops every subscriber and cancels the frame that was pending", async () => {
    const raf = frames();
    const { onFrame, stopFrames } = await load();
    const fn = vi.fn();
    onFrame(fn);
    stopFrames();
    expect(raf.cancel).toHaveBeenCalledTimes(1);
    expect(raf.pending()).toBe(0);
    // and the loop is usable afterwards
    onFrame(fn);
    raf.at(1016);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
