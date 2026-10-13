// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fmt, fmtInt, fmtMs, Meter, type MeterSample, sparkPaths } from "./meter";

describe("Meter", () => {
  beforeEach(() => {
    vi.useFakeTimers({
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "performance", "Date"],
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Ten tokens, one every 100 ms, the last landing a second after the start. */
  const tenTokens = (meter: Meter) => {
    for (let i = 0; i < 10; i++) {
      vi.advanceTimersByTime(100);
      meter.token();
    }
  };

  it("measures the rate over the stretch since the oldest arrival in its window", () => {
    const meter = new Meter();
    meter.start();
    tenTokens(meter);
    // Ten tokens, the first of them 0.9 s ago.
    expect(meter.rate()).toBeCloseTo(10 / 0.9, 6);
    expect(meter.total).toBe(10);
  });

  it("reports no rate from fewer than two arrivals", () => {
    const meter = new Meter();
    meter.start();
    expect(meter.rate()).toBe(0);
    vi.advanceTimersByTime(100);
    meter.token();
    vi.advanceTimersByTime(100);
    expect(meter.rate()).toBe(0);
  });

  it("forgets arrivals older than three seconds", () => {
    const meter = new Meter();
    meter.start();
    tenTokens(meter); // the last one at 1000 ms
    vi.advanceTimersByTime(2900); // 3900: arrivals up to 800 ms have aged out
    expect(meter.rate()).toBeCloseTo(2 / 3, 6); // the two at 900 and 1000, over 3.0 s
    expect(meter.samples).toHaveLength(2);
    vi.advanceTimersByTime(200); // 4100: all of them
    expect(meter.rate()).toBe(0);
    expect(meter.samples).toHaveLength(0);
  });

  it("counts a batch of tokens as one arrival of several", () => {
    const meter = new Meter();
    meter.start();
    vi.advanceTimersByTime(100);
    meter.token(3);
    vi.advanceTimersByTime(500);
    meter.token(2);
    expect(meter.total).toBe(5);
    expect(meter.rate()).toBeCloseTo(5 / 0.5, 6);
  });

  it("samples four times a second, with the time each sample was taken", () => {
    const seen: MeterSample[] = [];
    const meter = new Meter({ onSample: (s) => seen.push({ ...s, history: [...s.history] }) });
    const t0 = performance.now();
    meter.start();
    tenTokens(meter);
    expect(seen).toHaveLength(4);
    expect(seen.map((s) => s.at - t0)).toEqual([250, 500, 750, 1000]);
    // A sample that falls on the same instant as a token is taken first.
    expect(seen.map((s) => s.total)).toEqual([2, 4, 7, 9]);
    // At 250 ms: the arrivals at 100 and 200, the first of them 150 ms ago.
    expect(seen[0]!.rate).toBeCloseTo(2 / 0.15, 6);
    expect(seen[3]!.history).toHaveLength(4);
    expect(seen[3]!.history[3]).toBe(seen[3]!.rate);
  });

  it("keeps the last 48 samples", () => {
    const meter = new Meter();
    meter.start();
    vi.advanceTimersByTime(250 * 60);
    expect(meter.history).toHaveLength(48);
  });

  it("stops sampling, and reports the count and the average over the whole run", () => {
    const onSample = vi.fn();
    const meter = new Meter({ onSample });
    meter.start();
    tenTokens(meter);
    vi.advanceTimersByTime(4000);
    const calls = onSample.mock.calls.length;
    expect(meter.stop()).toEqual({ total: 10, avg: 2 });
    expect(meter.timer).toBeNull();
    vi.advanceTimersByTime(2000);
    expect(onSample).toHaveBeenCalledTimes(calls);
  });

  it("starts again from nothing", () => {
    const onSample = vi.fn();
    const meter = new Meter({ onSample });
    meter.start();
    tenTokens(meter);
    meter.start(); // a second request, without a stop between
    expect(meter.total).toBe(0);
    expect(meter.samples).toEqual([]);
    expect(meter.history).toEqual([]);
    onSample.mockClear();
    vi.advanceTimersByTime(1000);
    expect(onSample).toHaveBeenCalledTimes(4); // one timer, not two
  });

  it("reports an average of zero for a run that took no time", () => {
    const meter = new Meter();
    meter.start();
    expect(meter.stop()).toEqual({ total: 0, avg: 0 });
  });
});

describe("sparkPaths", () => {
  it("draws nothing from fewer than two values", () => {
    expect(sparkPaths([])).toEqual(["", ""]);
    expect(sparkPaths([3])).toEqual(["", ""]);
    expect(sparkPaths(null)).toEqual(["", ""]);
    expect(sparkPaths(undefined)).toEqual(["", ""]);
  });

  it("scales a rate from zero, in a 120 by 38 box with 3 of padding", () => {
    const [line, fill] = sparkPaths([0, 2, 4]);
    expect(line).toBe(
      "M0 35.00 C30.00 35.00, 30.00 19.00, 60.00 19.00 C90.00 19.00, 90.00 3.00, 120.00 3.00",
    );
    expect(fill).toBe(`${line} L120 38 L0 38 Z`);
  });

  it("never scales a rate against less than one token a second", () => {
    const [line] = sparkPaths([0.5, 0.5]);
    expect(line).toBe("M0 19.00 C60.00 19.00, 60.00 19.00, 120.00 19.00");
  });

  it("scales to the data's own range when asked", () => {
    const [line] = sparkPaths([17, 28], 120, 38, { autoMin: true });
    expect(line).toBe("M0 35.00 C60.00 35.00, 60.00 3.00, 120.00 3.00");
  });

  it("draws a flat series along the bottom when scaled to itself", () => {
    const [line] = sparkPaths([5, 5], 120, 38, { autoMin: true });
    expect(line).toBe("M0 35.00 C60.00 35.00, 60.00 35.00, 120.00 35.00");
  });

  it("takes another box", () => {
    const [line, fill] = sparkPaths([0, 1], 200, 20);
    expect(line).toBe("M0 17.00 C100.00 17.00, 100.00 3.00, 200.00 3.00");
    expect(fill).toBe(`${line} L200 20 L0 20 Z`);
  });
});

describe("the formatters the meter's readers use", () => {
  it("are the ones in format.ts", () => {
    expect(fmt(11.26)).toBe("11.3");
    expect(fmtInt(4096)).toBe("4,096");
    expect(fmtMs(412)).toBe("412ms");
  });
});
