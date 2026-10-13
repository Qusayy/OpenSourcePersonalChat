import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Aurora = typeof import("./aurora");

let clock = 0;
let frameQueue = new Map<number, FrameRequestCallback>();
let frameId = 0;
let idleQueue: Array<(() => void) | null> = [];
let schemeListeners: Array<() => void> = [];

/** Run animation frames 16 ms apart for `ms` of the page's time. */
function run(ms: number) {
  for (let t = 0; t < ms; t += 16) {
    clock += 16;
    const due = frameQueue;
    frameQueue = new Map();
    for (const cb of due.values()) cb(clock);
  }
}

/** Let the browser be idle: whatever was waiting for that runs now. */
function idle() {
  const due = idleQueue;
  idleQueue = [];
  for (const cb of due) cb?.();
}

function fakeGL() {
  const lost = { value: false };
  const gl = {
    VERTEX_SHADER: 1,
    FRAGMENT_SHADER: 2,
    COMPILE_STATUS: 3,
    LINK_STATUS: 4,
    COLOR_BUFFER_BIT: 5,
    TRIANGLES: 6,
    createShader: vi.fn(() => ({})),
    shaderSource: vi.fn(),
    compileShader: vi.fn(),
    getShaderParameter: vi.fn(() => true),
    getShaderInfoLog: vi.fn(() => ""),
    deleteShader: vi.fn(),
    createProgram: vi.fn(() => ({})),
    attachShader: vi.fn(),
    linkProgram: vi.fn(),
    getProgramParameter: vi.fn(() => true),
    getProgramInfoLog: vi.fn(() => ""),
    useProgram: vi.fn(),
    bindVertexArray: vi.fn(),
    createVertexArray: vi.fn(() => ({})),
    clearColor: vi.fn(),
    getUniformLocation: vi.fn((_program: unknown, name: string) => name),
    isContextLost: vi.fn(() => lost.value),
    viewport: vi.fn(),
    clear: vi.fn(),
    uniform1f: vi.fn(),
    uniform2f: vi.fn(),
    uniform3f: vi.fn(),
    drawArrays: vi.fn(),
  };
  return { gl, lost };
}

interface World {
  /** The visitor has asked for reduced motion. */
  reduce?: boolean;
  /** What `getContext("webgl2")` gives back. Nothing by default: no WebGL here. */
  gl?: ReturnType<typeof fakeGL>["gl"] | null;
  /** The page's background, as the pixel a 2D canvas reads back. Absent: no 2D canvas either. */
  sheet?: [number, number, number];
  /** Whether the browser has requestIdleCallback. */
  idleCallback?: boolean;
}

async function world({ reduce = false, gl = null, sheet, idleCallback = true }: World = {}) {
  vi.resetModules();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("prefers-reduced-motion") ? reduce : false,
      media: query,
      addEventListener: (_type: string, fn: () => void) => void schemeListeners.push(fn),
      removeEventListener: (_type: string, fn: () => void) => {
        schemeListeners = schemeListeners.filter((l) => l !== fn);
      },
    })),
  );
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    frameQueue.set(++frameId, cb);
    return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => void frameQueue.delete(id));
  if (idleCallback) {
    vi.stubGlobal("requestIdleCallback", (cb: () => void) => idleQueue.push(cb));
    vi.stubGlobal("cancelIdleCallback", (id: number) => {
      idleQueue[id - 1] = null;
    });
  } else {
    vi.stubGlobal("requestIdleCallback", undefined);
  }

  // A 2D canvas that reads back the live colour first and the sheet second,
  // which is the order resolveColour paints them in.
  let reads = 0;
  const twoD = sheet
    ? {
        fillStyle: "",
        fillRect() {},
        clearRect() {},
        getImageData: () => ({ data: reads++ % 2 === 0 ? [0, 128, 64, 255] : [...sheet, 255] }),
      }
    : null;
  const getContext = vi.fn((kind: string) =>
    kind === "webgl2" ? gl : kind === "2d" ? twoD : null,
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    getContext as unknown as HTMLCanvasElement["getContext"],
  );

  const host = document.createElement("div");
  host.id = "aurora";
  host.hidden = true;
  const canvas = document.createElement("canvas");
  Object.defineProperty(canvas, "clientWidth", { value: 800, configurable: true });
  Object.defineProperty(canvas, "clientHeight", { value: 200, configurable: true });
  host.append(canvas);
  document.body.append(host);

  const mod: Aurora = await import("./aurora");
  const energy = () => Number(host.style.getPropertyValue("--e"));
  return { ...mod, host, canvas, getContext, energy };
}

beforeEach(() => {
  clock = 10_000;
  frameQueue = new Map();
  frameId = 0;
  idleQueue = [];
  schemeListeners = [];
  vi.spyOn(performance, "now").mockImplementation(() => clock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("before it is given its element", () => {
  it("reads nothing and starts nothing when the module is imported", async () => {
    const { getContext } = await world();
    expect(window.matchMedia).not.toHaveBeenCalled();
    expect(getContext).not.toHaveBeenCalled();
    expect(frameQueue.size).toBe(0);
    expect(idleQueue).toHaveLength(0);
  });

  it("takes phases, rates and pulses and does nothing with them", async () => {
    const { aurora, host } = await world();
    expect(() => {
      aurora.phase("writing");
      aurora.rate(12);
      aurora.pulse();
      aurora.phase(null);
    }).not.toThrow();
    expect(host.hidden).toBe(true);
    expect(frameQueue.size).toBe(0);
  });
});

describe("without WebGL: the CSS strip", () => {
  it("is in css mode, and stays there when the context cannot be had", async () => {
    const { mountAurora, host, getContext } = await world({ gl: null });
    mountAurora(host);
    expect(host.dataset.mode).toBe("css");
    idle();
    expect(getContext).toHaveBeenCalledWith("webgl2", expect.objectContaining({ alpha: true }));
    expect(host.dataset.mode).toBe("css");
  });

  it("stays in css mode when the context comes back already lost", async () => {
    const { gl, lost } = fakeGL();
    lost.value = true;
    const { mountAurora, host } = await world({ gl });
    mountAurora(host);
    idle();
    expect(host.dataset.mode).toBe("css");
    expect(gl.createShader).not.toHaveBeenCalled();
  });

  it("stays in css mode when the shader does not compile", async () => {
    const { gl } = fakeGL();
    gl.getShaderParameter.mockReturnValue(false);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { mountAurora, host } = await world({ gl });
    mountAurora(host);
    idle();
    expect(host.dataset.mode).toBe("css");
    expect(warn).toHaveBeenCalled();
    expect(gl.deleteShader).toHaveBeenCalled();
  });

  it("waits a moment instead where the browser has no idle callback", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { gl } = fakeGL();
    const { mountAurora, host } = await world({ gl, idleCallback: false });
    mountAurora(host);
    vi.advanceTimersByTime(1199);
    expect(host.dataset.mode).toBe("css");
    vi.advanceTimersByTime(1);
    expect(host.dataset.mode).toBe("gl");
  });

  it("holds a low swell while the prompt is read, with its energy on --e", async () => {
    const { aurora, mountAurora, host, energy } = await world();
    mountAurora(host);
    aurora.phase("reading");
    expect(host.hidden).toBe(false);
    run(3000);
    expect(energy()).toBeGreaterThanOrEqual(0.13);
    expect(energy()).toBeLessThanOrEqual(0.27);
  });

  it("is not lit by working or by waiting in the queue", async () => {
    const { aurora, mountAurora, host } = await world();
    mountAurora(host);
    aurora.phase("working");
    aurora.phase("queued");
    expect(host.hidden).toBe(true);
    expect(frameQueue.size).toBe(0);
  });

  it("fades out when the request ends, then hides and asks for no more frames", async () => {
    const { aurora, mountAurora, host, energy } = await world();
    mountAurora(host);
    aurora.phase("writing");
    aurora.rate(10);
    run(2000);
    expect(energy()).toBeGreaterThan(0.9);
    aurora.phase(null);
    run(160);
    expect(host.hidden).toBe(false); // still fading
    expect(energy()).toBeGreaterThan(0.004);
    run(4000);
    expect(host.hidden).toBe(true);
    expect(host.style.getPropertyValue("--e")).toBe("0");
    expect(frameQueue.size).toBe(0);
  });
});

describe("how bright it is", () => {
  it("is at full when writing at the only speed this page has seen", async () => {
    const { aurora, mountAurora, host, energy } = await world();
    mountAurora(host);
    aurora.phase("writing");
    aurora.rate(6);
    run(4000);
    expect(energy()).toBeCloseTo(1, 2);
  });

  it("sits at its floor while writing with no rate measured yet", async () => {
    const { aurora, mountAurora, host, energy } = await world();
    mountAurora(host);
    aurora.phase("writing");
    run(4000);
    expect(energy()).toBeCloseTo(0.35, 2);
  });

  it("measures a later answer against the fastest steady rate seen", async () => {
    const { aurora, mountAurora, host, energy } = await world();
    mountAurora(host);
    // A first answer at ten tokens a second, long enough for that to count.
    aurora.phase("writing");
    for (let i = 0; i < 40; i++) {
      run(250);
      aurora.rate(10);
    }
    aurora.phase(null);
    run(5000);
    // A second at half that speed is at half the range above the floor.
    aurora.phase("writing");
    aurora.rate(5);
    run(4000);
    expect(energy()).toBeCloseTo(0.35 + 0.65 * 0.5, 2);
  });

  it("does not let the first samples of an answer set the bar", async () => {
    const { aurora, mountAurora, host, energy } = await world();
    mountAurora(host);
    aurora.phase("writing");
    aurora.rate(40); // a burst in the first quarter second
    run(250);
    aurora.phase(null);
    run(5000);
    aurora.phase("writing");
    aurora.rate(5);
    run(4000);
    expect(energy()).toBeCloseTo(1, 2); // 5 is the fastest steady rate there has been
  });

  it("lifts a little as each token lands, up to a limit, and lets go again", async () => {
    const { aurora, mountAurora, host, energy } = await world();
    mountAurora(host);
    aurora.phase("writing");
    run(4000);
    const floor = energy();
    for (let i = 0; i < 20; i++) aurora.pulse();
    run(200);
    const lifted = energy();
    expect(lifted).toBeGreaterThan(floor + 0.03);
    expect(lifted).toBeLessThan(floor + 0.25);
    run(4000);
    expect(energy()).toBeCloseTo(floor, 2);
  });
});

describe("under reduced motion", () => {
  it("is a still tint: shown for the request, with no frames and no shader", async () => {
    const { gl } = fakeGL();
    const { aurora, mountAurora, host, getContext } = await world({ reduce: true, gl });
    mountAurora(host);
    expect(host.dataset.mode).toBe("still");
    idle();
    expect(getContext).not.toHaveBeenCalledWith("webgl2", expect.anything());
    aurora.phase("reading");
    expect(host.hidden).toBe(false);
    aurora.phase("writing");
    expect(host.hidden).toBe(false);
    expect(frameQueue.size).toBe(0);
    aurora.phase(null);
    expect(host.hidden).toBe(true);
  });
});

describe("with WebGL: the shader", () => {
  it("compiles when the page is idle, never at mount, and switches to gl mode", async () => {
    const { gl } = fakeGL();
    const { mountAurora, host } = await world({ gl });
    mountAurora(host);
    expect(gl.createShader).not.toHaveBeenCalled();
    expect(host.dataset.mode).toBe("css");
    idle();
    expect(gl.createShader).toHaveBeenCalledTimes(2);
    expect(host.dataset.mode).toBe("gl");
  });

  it("draws at half resolution with the energy, and still writes --e", async () => {
    const { gl } = fakeGL();
    const { aurora, mountAurora, host, canvas, energy } = await world({ gl });
    mountAurora(host);
    idle();
    aurora.phase("writing");
    aurora.rate(8);
    run(160);
    expect(canvas.width).toBe(400);
    expect(canvas.height).toBe(100);
    expect(gl.viewport).toHaveBeenCalledWith(0, 0, 400, 100);
    expect(gl.uniform2f).toHaveBeenLastCalledWith("u_res", 400, 100);
    expect(gl.drawArrays).toHaveBeenCalledWith(gl.TRIANGLES, 0, 3);
    const sent = gl.uniform1f.mock.calls.filter(([name]) => name === "u_energy").at(-1)?.[1];
    expect(sent).toBeCloseTo(energy(), 2);
    expect(energy()).toBeGreaterThan(0);
  });

  it("does not draw while the canvas has no box", async () => {
    const { gl } = fakeGL();
    const { aurora, mountAurora, host, canvas, energy } = await world({ gl });
    Object.defineProperty(canvas, "clientWidth", { value: 0, configurable: true });
    mountAurora(host);
    idle();
    aurora.phase("writing");
    run(160);
    expect(gl.drawArrays).not.toHaveBeenCalled();
    expect(energy()).toBeGreaterThan(0);
  });

  it("falls back to the CSS strip when the context is lost, and does not rebuild it", async () => {
    const { gl, lost } = fakeGL();
    const { aurora, mountAurora, host, canvas, energy } = await world({ gl });
    mountAurora(host);
    idle();
    aurora.phase("writing");
    run(160);
    const drawn = gl.drawArrays.mock.calls.length;
    expect(drawn).toBeGreaterThan(0);

    lost.value = true;
    const event = new Event("webglcontextlost", { cancelable: true });
    canvas.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(host.dataset.mode).toBe("css");

    const before = energy();
    aurora.pulse();
    run(160);
    expect(gl.drawArrays).toHaveBeenCalledTimes(drawn);
    expect(energy()).not.toBe(before); // the fallback is still being driven
  });

  it("spreads the range differently on a dark sheet, under the same ceiling", async () => {
    const light = fakeGL();
    const a = await world({ gl: light.gl, sheet: [250, 252, 250] });
    a.mountAurora(a.host);
    expect(a.host.style.getPropertyValue("--peak")).toBe("0.24");
    idle();
    a.aurora.phase("writing");
    run(32);
    expect(light.gl.uniform1f).toHaveBeenCalledWith("u_curve", 1);
    expect(light.gl.uniform1f).toHaveBeenCalledWith("u_peak", 0.24);
    expect(light.gl.uniform3f).toHaveBeenLastCalledWith("u_colour", 0, 128 / 255, 64 / 255);
    document.body.innerHTML = "";

    const dark = fakeGL();
    const b = await world({ gl: dark.gl, sheet: [18, 24, 20] });
    b.mountAurora(b.host);
    idle();
    b.aurora.phase("writing");
    run(32);
    expect(dark.gl.uniform1f).toHaveBeenCalledWith("u_curve", 0.55);
    expect(dark.gl.uniform1f).toHaveBeenCalledWith("u_peak", 0.24);
  });

  it("reads the colours again when the scheme changes", async () => {
    const { gl } = fakeGL();
    const { mountAurora, host } = await world({ gl, sheet: [250, 252, 250] });
    mountAurora(host);
    expect(schemeListeners).toHaveLength(1);
    host.style.removeProperty("--peak");
    schemeListeners[0]!();
    expect(host.style.getPropertyValue("--peak")).toBe("0.24");
  });
});

describe("letting go", () => {
  it("stops the frames, puts the strip at rest and stops listening", async () => {
    const { aurora, mountAurora, host, energy } = await world();
    const release = mountAurora(host);
    aurora.phase("writing");
    aurora.rate(10);
    run(500);
    expect(energy()).toBeGreaterThan(0);
    release();
    expect(host.hidden).toBe(true);
    expect(host.style.getPropertyValue("--e")).toBe("0");
    expect(schemeListeners).toHaveLength(0);
    run(64);
    expect(frameQueue.size).toBe(0);
    // and it is inert again
    aurora.phase("writing");
    expect(host.hidden).toBe(true);
  });

  it("cancels a compile that was still waiting for the page to be idle", async () => {
    const { gl } = fakeGL();
    const { mountAurora, host } = await world({ gl });
    mountAurora(host)();
    idle();
    expect(gl.createShader).not.toHaveBeenCalled();
  });

  it("keeps the shader when the same element is mounted again", async () => {
    const { gl } = fakeGL();
    const { aurora, mountAurora, host, getContext } = await world({ gl });
    mountAurora(host)(); // what a development build does: mount, let go, mount
    const release = mountAurora(host);
    idle();
    expect(host.dataset.mode).toBe("gl");
    release();
    mountAurora(host);
    expect(host.dataset.mode).toBe("gl"); // straight away, with nothing recompiled
    idle();
    expect(getContext.mock.calls.filter(([kind]) => kind === "webgl2")).toHaveLength(1);
    expect(gl.createProgram).toHaveBeenCalledTimes(1);
    aurora.phase("writing");
    run(64);
    expect(gl.drawArrays).toHaveBeenCalled();
  });

  it("starts from nothing on a different canvas", async () => {
    const { gl } = fakeGL();
    const { mountAurora, host } = await world({ gl });
    mountAurora(host);
    idle();
    expect(host.dataset.mode).toBe("gl");

    const other = document.createElement("div");
    other.append(document.createElement("canvas"));
    document.body.append(other);
    mountAurora(other); // takes over from the first
    expect(host.hidden).toBe(true);
    expect(other.dataset.mode).toBe("css");
    idle();
    expect(other.dataset.mode).toBe("gl");
    expect(gl.createProgram).toHaveBeenCalledTimes(2);
  });

  it("is harmless to let go twice", async () => {
    const { mountAurora, host } = await world();
    const release = mountAurora(host);
    release();
    const again = mountAurora(host);
    release(); // the stale handle must not undo the new mount
    expect(schemeListeners).toHaveLength(1);
    again();
    expect(schemeListeners).toHaveLength(0);
  });
});
