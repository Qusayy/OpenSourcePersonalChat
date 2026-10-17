import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Curtains = typeof import("./curtains");

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

function fakeGL(renderer = "ANGLE (Some GPU)") {
  const lost = { value: false };
  const loseContext = vi.fn();
  const gl = {
    VERTEX_SHADER: 1,
    FRAGMENT_SHADER: 2,
    COMPILE_STATUS: 3,
    LINK_STATUS: 4,
    COLOR_BUFFER_BIT: 5,
    TRIANGLES: 6,
    RENDERER: 7,
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
    getExtension: vi.fn((name: string) => (name === "WEBGL_lose_context" ? { loseContext } : null)),
    getParameter: vi.fn((p: number) => (p === 7 ? renderer : null)),
    isContextLost: vi.fn(() => lost.value),
    viewport: vi.fn(),
    clear: vi.fn(),
    uniform1f: vi.fn(),
    uniform2f: vi.fn(),
    uniform3f: vi.fn(),
    drawArrays: vi.fn(),
  };
  return { gl, lost, loseContext };
}

interface World {
  /** The visitor has asked for reduced motion. */
  reduce?: boolean;
  /** What `getContext("webgl2")` gives back. Nothing by default: no WebGL here. */
  gl?: ReturnType<typeof fakeGL>["gl"] | null;
  /** The page's background, as the pixel a 2D canvas reads back. Absent: no 2D canvas either. */
  sheet?: [number, number, number];
}

async function world({ reduce = false, gl = null, sheet }: World = {}) {
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
  vi.stubGlobal("requestIdleCallback", (cb: () => void) => idleQueue.push(cb));
  vi.stubGlobal("cancelIdleCallback", (id: number) => {
    idleQueue[id - 1] = null;
  });
  Object.defineProperty(document, "hidden", { value: false, configurable: true });

  // A 2D canvas that reads back the green, then the sheet, then the violet,
  // which is the order resolveColour paints them in.
  let reads = 0;
  const twoD = sheet
    ? {
        fillStyle: "",
        fillRect() {},
        clearRect() {},
        getImageData: () => ({
          data: [
            [0, 128, 64, 255],
            [...sheet, 255],
            [102, 70, 168, 255],
          ][reads++ % 3],
        }),
      }
    : null;
  const getContext = vi.fn((kind: string) =>
    kind === "webgl2" ? gl : kind === "2d" ? twoD : null,
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    getContext as unknown as HTMLCanvasElement["getContext"],
  );

  // The page's sheet: <main>, and the scroller directly inside it.
  const main = document.createElement("main");
  main.id = "main";
  const scroller = document.createElement("div");
  const field = document.createElement("textarea");
  scroller.append(field);
  main.append(scroller);
  document.body.append(main);

  const host = document.createElement("div");
  host.id = "curtains";
  host.hidden = true;
  const canvas = document.createElement("canvas");
  Object.defineProperty(canvas, "clientWidth", { value: 800, configurable: true });
  Object.defineProperty(canvas, "clientHeight", { value: 200, configurable: true });
  Object.defineProperty(host, "clientHeight", { value: 200, configurable: true });
  host.append(canvas);
  document.body.append(host);

  const mod: Curtains = await import("./curtains");
  const energy = () => Number(host.style.getPropertyValue("--e"));
  /** Scroll `el` to `top` and say so, as the browser would. */
  const scroll = (el: HTMLElement, top: number) => {
    el.scrollTop = top;
    el.dispatchEvent(new Event("scroll"));
  };
  return { ...mod, host, canvas, getContext, energy, main, scroller, field, scroll };
}

/** Mounted, given WebGL, compiled and hung. */
async function hung(options: World = {}) {
  const fake = fakeGL();
  const w = await world({ gl: fake.gl, ...options });
  w.mountCurtains(w.host);
  idle();
  w.curtains.rest(true);
  return { ...w, ...fake };
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

  it("takes rest and does nothing with it", async () => {
    const { curtains, host } = await world();
    expect(() => curtains.rest(true)).not.toThrow();
    expect(host.hidden).toBe(true);
    expect(frameQueue.size).toBe(0);
  });
});

describe("with WebGL: the shader", () => {
  it("shows nothing until the shader is ready, which is when the page is idle", async () => {
    const { gl } = fakeGL();
    const { curtains, mountCurtains, host } = await world({ gl });
    mountCurtains(host);
    curtains.rest(true);
    expect(host.dataset.mode).toBe("wait");
    expect(host.hidden).toBe(true);
    expect(gl.createShader).not.toHaveBeenCalled();
    idle();
    expect(gl.createShader).toHaveBeenCalledTimes(2);
    expect(host.dataset.mode).toBe("gl");
    expect(host.hidden).toBe(false);
    expect(frameQueue.size).toBe(1);
  });

  it("asks for a low-power context, and none drawn on the processor", async () => {
    const { getContext } = await hung();
    expect(getContext).toHaveBeenCalledWith(
      "webgl2",
      expect.objectContaining({
        alpha: true,
        powerPreference: "low-power",
        failIfMajorPerformanceCaveat: true,
      }),
    );
  });

  it("fades in slowly to its resting brightness, and breathes there", async () => {
    const { energy } = await hung();
    run(500);
    const early = energy();
    expect(early).toBeGreaterThan(0);
    expect(early).toBeLessThan(0.6);
    const seen: number[] = [];
    for (let i = 0; i < 20; i++) {
      run(1000);
      seen.push(energy());
    }
    expect(Math.min(...seen.slice(5))).toBeGreaterThan(0.7);
    expect(Math.max(...seen)).toBeLessThan(0.95);
    expect(Math.max(...seen) - Math.min(...seen.slice(5))).toBeGreaterThan(0.05);
  });

  it("draws at half resolution, with a curtain the same width whatever the band's size", async () => {
    const { gl, canvas } = await hung();
    run(160);
    expect(canvas.width).toBe(400);
    expect(canvas.height).toBe(100);
    expect(gl.viewport).toHaveBeenCalledWith(0, 0, 400, 100);
    expect(gl.uniform2f).toHaveBeenLastCalledWith("u_res", 400, 100);
    expect(gl.uniform1f).toHaveBeenCalledWith("u_unit", 120);
    expect(gl.uniform1f).toHaveBeenCalledWith("u_peak", 0.24);
    expect(gl.drawArrays).toHaveBeenCalledWith(gl.TRIANGLES, 0, 3);
  });

  it("draws about thirty times a second, not on every frame", async () => {
    const { gl } = await hung();
    run(2000);
    gl.drawArrays.mockClear();
    run(1000);
    const drawn = gl.drawArrays.mock.calls.length;
    expect(drawn).toBeGreaterThanOrEqual(28);
    expect(drawn).toBeLessThanOrEqual(32);
  });

  it("does not draw while the canvas has no box", async () => {
    const fake = fakeGL();
    const w = await world({ gl: fake.gl });
    Object.defineProperty(w.canvas, "clientWidth", { value: 0, configurable: true });
    w.mountCurtains(w.host);
    idle();
    w.curtains.rest(true);
    run(160);
    expect(fake.gl.drawArrays).not.toHaveBeenCalled();
    expect(w.energy()).toBeGreaterThan(0);
  });

  it("reads the green and the violet, and spreads the range by the sheet under one ceiling", async () => {
    const light = await hung({ sheet: [250, 252, 250] });
    run(64);
    expect(light.gl.uniform3f).toHaveBeenCalledWith("u_low", 0, 128 / 255, 64 / 255);
    expect(light.gl.uniform3f).toHaveBeenCalledWith("u_high", 102 / 255, 70 / 255, 168 / 255);
    expect(light.gl.uniform1f).toHaveBeenCalledWith("u_curve", 0.8);
    document.body.innerHTML = "";

    const dark = await hung({ sheet: [18, 24, 20] });
    run(64);
    expect(dark.gl.uniform1f).toHaveBeenCalledWith("u_curve", 0.55);
    expect(dark.gl.uniform1f).toHaveBeenCalledWith("u_peak", 0.24);
  });
});

describe("when they go", () => {
  it("fades out when a conversation starts, then hides and asks for no more frames", async () => {
    const { curtains, host, energy } = await hung();
    run(4000);
    curtains.rest(false);
    run(96);
    expect(host.hidden).toBe(false); // still fading
    expect(energy()).toBeGreaterThan(0.004);
    run(3000);
    expect(host.hidden).toBe(true);
    expect(host.style.getPropertyValue("--e")).toBe("0");
    expect(frameQueue.size).toBe(0);
  });

  it("comes back for a new chat", async () => {
    const { curtains, host, energy } = await hung();
    run(4000);
    curtains.rest(false);
    run(3000);
    curtains.rest(true);
    expect(host.hidden).toBe(false);
    run(4000);
    expect(energy()).toBeGreaterThan(0.7);
  });

  it("asks for no frames while the tab is hidden, and carries on when it is back", async () => {
    const { host } = await hung();
    run(500);
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    run(64);
    expect(frameQueue.size).toBe(0);
    expect(host.hidden).toBe(false);
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(frameQueue.size).toBe(1);
  });

  it("fades as the page scrolls away from it, and stops drawing once scrolled past", async () => {
    const { host, scroller, scroll, gl } = await hung();
    run(500);
    scroll(scroller, 50);
    expect(host.style.getPropertyValue("--fade")).toBe("0.750");
    expect(frameQueue.size).toBe(1);
    scroll(scroller, 400);
    expect(host.style.getPropertyValue("--fade")).toBe("0.000");
    run(64);
    expect(frameQueue.size).toBe(0);
    gl.drawArrays.mockClear();
    scroll(scroller, 0);
    run(64);
    expect(gl.drawArrays).toHaveBeenCalled();
  });

  it("takes <main> scrolling as the page too, and nothing deeper", async () => {
    const { host, main, field, scroll } = await hung();
    scroll(field, 300);
    expect(host.style.getPropertyValue("--fade")).toBe("1");
    scroll(main, 100);
    expect(host.style.getPropertyValue("--fade")).toBe("0.500");
  });

  it("is unfaded again whenever the page says what it wants", async () => {
    const { curtains, host, scroller, scroll } = await hung();
    scroll(scroller, 400);
    curtains.rest(true);
    expect(host.style.getPropertyValue("--fade")).toBe("1");
  });
});

describe("the still tint", () => {
  it("stands in where there is no WebGL2, shown only while the page wants it", async () => {
    const { curtains, mountCurtains, host } = await world({ gl: null });
    mountCurtains(host);
    idle();
    expect(host.dataset.mode).toBe("still");
    curtains.rest(true);
    expect(host.hidden).toBe(false);
    curtains.rest(false);
    expect(host.hidden).toBe(true);
    expect(frameQueue.size).toBe(0);
  });

  it("stands in for a context drawn on the processor, and gives that context back", async () => {
    const { gl, loseContext } = fakeGL("Google SwiftShader");
    const { curtains, mountCurtains, host } = await world({ gl });
    mountCurtains(host);
    idle();
    curtains.rest(true);
    expect(host.dataset.mode).toBe("still");
    expect(loseContext).toHaveBeenCalled();
    expect(gl.createShader).not.toHaveBeenCalled();
    expect(frameQueue.size).toBe(0);
  });

  it("stands in when the shader does not build", async () => {
    const { gl } = fakeGL();
    gl.getShaderParameter.mockReturnValue(false);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { mountCurtains, host } = await world({ gl });
    mountCurtains(host);
    idle();
    expect(host.dataset.mode).toBe("still");
    expect(warn).toHaveBeenCalled();
  });

  it("takes over when the context is lost, and the shader is not rebuilt", async () => {
    const { gl, lost, canvas, host } = await hung();
    run(160);
    const drawn = gl.drawArrays.mock.calls.length;
    expect(drawn).toBeGreaterThan(0);
    lost.value = true;
    const event = new Event("webglcontextlost", { cancelable: true });
    canvas.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(host.dataset.mode).toBe("still");
    expect(host.hidden).toBe(false);
    run(160);
    expect(gl.drawArrays).toHaveBeenCalledTimes(drawn);
    expect(frameQueue.size).toBe(0);
  });

  it("is all there is under reduced motion: no shader and no frames", async () => {
    const { gl } = fakeGL();
    const { curtains, mountCurtains, host, getContext } = await world({ reduce: true, gl });
    mountCurtains(host);
    expect(host.dataset.mode).toBe("still");
    idle();
    expect(getContext).not.toHaveBeenCalledWith("webgl2", expect.anything());
    curtains.rest(true);
    expect(host.hidden).toBe(false);
    expect(frameQueue.size).toBe(0);
    curtains.rest(false);
    expect(host.hidden).toBe(true);
  });
});

describe("letting go", () => {
  it("stops the frames, takes the band away and stops listening", async () => {
    const { curtains, mountCurtains, host, scroller, scroll } = await hung();
    run(500);
    const release = mountCurtains(host);
    release();
    expect(host.hidden).toBe(true);
    expect(schemeListeners).toHaveLength(0);
    run(64);
    expect(frameQueue.size).toBe(0);
    // and it is inert again
    curtains.rest(true);
    scroll(scroller, 100);
    expect(host.hidden).toBe(true);
    expect(frameQueue.size).toBe(0);
  });

  it("keeps the shader when the same element is mounted again", async () => {
    const { gl } = fakeGL();
    const { curtains, mountCurtains, host, getContext } = await world({ gl });
    mountCurtains(host)(); // what a development build does: mount, let go, mount
    const release = mountCurtains(host);
    idle();
    expect(host.dataset.mode).toBe("gl");
    release();
    mountCurtains(host);
    expect(host.dataset.mode).toBe("gl"); // straight away, with nothing recompiled
    idle();
    expect(getContext.mock.calls.filter(([kind]) => kind === "webgl2")).toHaveLength(1);
    expect(gl.createProgram).toHaveBeenCalledTimes(1);
    curtains.rest(true);
    run(64);
    expect(gl.drawArrays).toHaveBeenCalled();
  });

  it("cancels a compile that was still waiting for the page to be idle", async () => {
    const { gl } = fakeGL();
    const { mountCurtains, host } = await world({ gl });
    mountCurtains(host)();
    idle();
    expect(gl.createShader).not.toHaveBeenCalled();
  });
});
