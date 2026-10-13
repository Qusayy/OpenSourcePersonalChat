import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type TraceModule = typeof import("./trace");

let frameQueue = new Map<number, FrameRequestCallback>();
let frameId = 0;
let clock = 0;

/** One animation frame at `now`. */
function frameAt(now: number) {
  clock = now;
  const due = frameQueue;
  frameQueue = new Map();
  for (const cb of due.values()) cb(now);
}

/** A 2D context that writes down what it was asked to draw. */
function fakeContext() {
  const calls: [string, ...number[]][] = [];
  const log =
    (name: string) =>
    (...args: number[]) =>
      void calls.push([name, ...args]);
  return {
    calls,
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 0,
    lineJoin: "",
    lineCap: "",
    setTransform: log("setTransform"),
    clearRect: log("clearRect"),
    beginPath: log("beginPath"),
    moveTo: log("moveTo"),
    lineTo: log("lineTo"),
    stroke: log("stroke"),
    arc: log("arc"),
    fill: log("fill"),
  };
}

async function world({ reduce = false, width = 120, height = 20 } = {}) {
  vi.resetModules();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: query.includes("reduced-motion") ? reduce : false })),
  );
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    frameQueue.set(++frameId, cb);
    return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => void frameQueue.delete(id));

  const ctx = fakeContext();
  const canvas = document.createElement("canvas");
  canvas.style.color = "rgb(1, 2, 3)";
  Object.defineProperty(canvas, "clientWidth", { value: width, configurable: true });
  Object.defineProperty(canvas, "clientHeight", { value: height, configurable: true });
  canvas.getContext = vi.fn(() => ctx) as unknown as HTMLCanvasElement["getContext"];
  const figure = document.createElement("b");
  figure.textContent = "—";
  document.body.append(canvas, figure);

  const mod: TraceModule = await import("./trace");
  const onRate = vi.fn();
  const trace = mod.createTrace({ canvas: () => canvas, figure: () => figure, onRate });
  const drawn = (name: string) => ctx.calls.filter(([n]) => n === name).map(([, ...args]) => args);
  return { ...mod, trace, canvas, figure, ctx, onRate, drawn };
}

beforeEach(() => {
  frameQueue = new Map();
  frameId = 0;
  clock = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("the figure", () => {
  it("eases toward the last measured rate, a frame at a time", async () => {
    const { trace, figure } = await world();
    trace.start();
    trace.sample(10, 1000);
    frameAt(1016);
    expect(figure.textContent).toBe((10 * (1 - Math.exp(-16 / 140))).toFixed(1)); // 1.1
    for (let t = 1032; t <= 3000; t += 16) frameAt(t);
    expect(figure.textContent).toBe("10.0");
  });

  it("starts each request from nothing", async () => {
    const { trace, figure } = await world();
    trace.start();
    trace.sample(10, 1000);
    for (let t = 1016; t <= 3000; t += 16) frameAt(t);
    trace.stop();
    trace.start();
    frameAt(3016);
    expect(figure.textContent).toBe("0.0");
  });

  it("is written only when it changes", async () => {
    const { trace, figure } = await world();
    trace.start();
    frameAt(1016);
    const node = figure.firstChild;
    frameAt(1032);
    expect(figure.firstChild).toBe(node);
  });
});

describe("the line", () => {
  it("is told each rate before anything is drawn from it", async () => {
    const { trace, onRate } = await world();
    trace.start();
    trace.sample(7.5, 1000);
    expect(onRate).toHaveBeenCalledWith(7.5);
  });

  it("matches the canvas's pixels to its box", async () => {
    vi.stubGlobal("devicePixelRatio", 3); // capped at 2
    const { trace, canvas, drawn } = await world();
    vi.stubGlobal("devicePixelRatio", 3);
    trace.start();
    frameAt(1016);
    expect(canvas.width).toBe(240);
    expect(canvas.height).toBe(40);
    expect(drawn("setTransform").at(-1)).toEqual([2, 0, 0, 2, 0, 0]);
  });

  it("draws nothing from fewer than two samples, but clears the box", async () => {
    const { trace, drawn } = await world();
    trace.start();
    trace.sample(5, 1000);
    frameAt(1300);
    expect(drawn("clearRect").at(-1)).toEqual([0, 0, 120, 20]);
    expect(drawn("stroke")).toHaveLength(0);
  });

  it("is drawn a quarter of a second behind now, its head on the line between two samples", async () => {
    const { trace, ctx, canvas, drawn } = await world();
    trace.start();
    trace.sample(0, 1000);
    trace.sample(0.5, 1250);
    frameAt(1385); // the head is at 1125: half way from the first sample to the second
    const right = 117;
    expect(drawn("moveTo")).toEqual([[right - (125 / 12000) * right, 17]]);
    // 0.25 tok/s against a scale of 1, in a box 20 high with 3 of padding
    expect(drawn("lineTo")).toEqual([[right, 13.5]]);
    expect(drawn("arc")).toEqual([[right, 13.5, 2.5, 0, Math.PI * 2]]);
    expect(ctx.strokeStyle).toBe(getComputedStyle(canvas).color);
    expect(ctx.fillStyle).toBe(ctx.strokeStyle);
    expect(ctx.lineWidth).toBe(1.5);
    expect(ctx.lineJoin).toBe("round");
    expect(ctx.lineCap).toBe("round");
  });

  it("holds the head at the last sample once the lag has passed it", async () => {
    const { trace, drawn } = await world();
    trace.start();
    trace.sample(0, 1000);
    trace.sample(0.5, 1250);
    frameAt(2000);
    expect(drawn("lineTo").at(-1)).toEqual([117, 10]);
  });

  it("eases its scale to a new peak instead of jumping", async () => {
    const { trace, drawn } = await world();
    trace.start();
    trace.sample(0, 1000);
    trace.sample(10, 1250);
    frameAt(1510); // head at the second sample; scale has moved a tenth of the way from 1 to 10
    expect(drawn("arc").at(-1)?.[1]).toBe(3); // 10 is off the top of a scale of 1.9: clipped to it
    for (let t = 1526; t < 4000; t += 16) frameAt(t);
    expect(drawn("arc").at(-1)?.[1]).toBeCloseTo(3, 3); // and at the top of a scale of 10
    trace.sample(5, 4000);
    frameAt(4260);
    expect(drawn("arc").at(-1)?.[1]).toBeCloseTo(10, 2); // half way up, now the scale has settled
  });

  it("leaves out what has scrolled off the left, and forgets it a second later", async () => {
    const { trace, drawn } = await world();
    trace.start();
    for (let t = 1000; t <= 16000; t += 250) trace.sample(1, t);
    frameAt(16260);
    // 12 s across the width, one sample every 250 ms, and a little past the edge
    const points = drawn("moveTo").length + drawn("lineTo").length;
    expect(points).toBeGreaterThan(48);
    expect(points).toBeLessThan(54);
  });

  it("does not draw while the canvas has no box, and carries on easing the figure", async () => {
    const { trace, figure, ctx } = await world({ width: 0 });
    trace.start();
    trace.sample(4, 1000);
    trace.sample(4, 1250);
    frameAt(1600);
    expect(ctx.calls).toHaveLength(0);
    expect(figure.textContent).not.toBe("—");
  });

  it("stops asking for frames and wipes the line when the request ends", async () => {
    const { trace, drawn } = await world();
    trace.start();
    trace.sample(1, 1000);
    trace.sample(1, 1250);
    frameAt(1600);
    const strokes = drawn("stroke").length;
    trace.stop();
    expect(drawn("clearRect").at(-1)).toEqual([0, 0, 120, 20]);
    frameAt(1616);
    frameAt(1632);
    expect(drawn("stroke")).toHaveLength(strokes);
    expect(frameQueue.size).toBe(0);
  });

  it("subscribes to the frame loop once, however often it is started", async () => {
    const { trace, figure } = await world();
    trace.start();
    trace.start();
    trace.sample(10, 1000);
    frameAt(1016);
    expect(figure.textContent).toBe("1.1"); // one step, not two
  });
});

describe("under reduced motion", () => {
  it("asks for no frames: the figure and the line step from sample to sample", async () => {
    const { trace, figure, drawn } = await world({ reduce: true });
    trace.start();
    expect(frameQueue.size).toBe(0);
    trace.sample(0, 1000);
    expect(figure.textContent).toBe("0.0");
    trace.sample(10, 1250);
    expect(figure.textContent).toBe("10.0");
    // Drawn as of the sample itself, on a scale that is already the peak's.
    expect(drawn("moveTo")).toEqual([[117 - (250 / 12000) * 117, 17]]);
    expect(drawn("lineTo")).toEqual([
      [117, 3],
      [117, 3],
    ]);
  });
});

describe("where the elements are not on the page", () => {
  it("does nothing, and does not throw", async () => {
    const { createTrace } = await world();
    const onRate = vi.fn();
    const trace = createTrace({ canvas: () => null, figure: () => null, onRate });
    expect(() => {
      trace.start();
      trace.sample(3, 1000);
      trace.sample(3, 1250);
      frameAt(1600);
      trace.stop();
    }).not.toThrow();
    expect(onRate).toHaveBeenCalledTimes(2);
  });
});
