import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ago,
  coreNoun,
  cpuText,
  fmt,
  fmtInt,
  fmtMs,
  gb,
  gbExact,
  loadText,
  num,
  pct,
  took,
  weekdayShort,
} from "./format";

describe("fmt", () => {
  it("prints a dash for a figure that is not there", () => {
    expect(fmt(null)).toBe("—");
    expect(fmt(undefined)).toBe("—");
    expect(fmt(Number.NaN)).toBe("—");
  });

  it("fixes the number of places, one by default", () => {
    expect(fmt(0)).toBe("0.0");
    expect(fmt(11.26)).toBe("11.3");
    expect(fmt(11.26, 2)).toBe("11.26");
    expect(fmt(11.6, 0)).toBe("12");
    expect(fmt(1234567.89, 1)).toBe("1234567.9");
  });
});

describe("fmtInt", () => {
  it("prints a dash for a figure that is not there", () => {
    expect(fmtInt(null)).toBe("—");
    expect(fmtInt(undefined)).toBe("—");
    expect(fmtInt(Number.NaN)).toBe("—");
  });

  it("rounds and groups in a fixed locale", () => {
    expect(fmtInt(0)).toBe("0");
    expect(fmtInt(4096)).toBe("4,096");
    expect(fmtInt(1234567.6)).toBe("1,234,568");
  });
});

describe("fmtMs", () => {
  it("prints a dash for a time that is not there", () => {
    expect(fmtMs(null)).toBe("—");
    expect(fmtMs(undefined)).toBe("—");
  });

  it("is milliseconds below a second and seconds from one", () => {
    expect(fmtMs(0)).toBe("0ms");
    expect(fmtMs(412.4)).toBe("412ms");
    expect(fmtMs(999.4)).toBe("999ms");
    expect(fmtMs(1000)).toBe("1.00s");
    expect(fmtMs(12345)).toBe("12.35s");
  });
});

describe("machine figures", () => {
  it("gb is megabytes as gigabytes to one place", () => {
    expect(gb(0)).toBe("0.0");
    expect(gb(1024)).toBe("1.0");
    expect(gb(3900)).toBe("3.8");
  });

  it("gbExact is the same to two places", () => {
    expect(gbExact(1380)).toBe("1.35");
  });

  it("cpuText drops the decimal from a whole count and keeps one otherwise", () => {
    expect(cpuText(2)).toBe("2");
    expect(cpuText(1.5)).toBe("1.5");
    expect(cpuText(1.25)).toBe("1.3");
  });

  it("coreNoun is singular for exactly one", () => {
    expect(coreNoun(1)).toBe("core");
    expect(coreNoun(2)).toBe("cores");
    expect(coreNoun(1.5)).toBe("cores");
  });

  it("pct rounds", () => {
    expect(pct(0)).toBe("0%");
    expect(pct(36.5)).toBe("37%");
    expect(pct(100)).toBe("100%");
  });

  it("loadText gives each average two places", () => {
    expect(loadText([1.9, 1.2, 0.8])).toBe("1.90  1.20  0.80");
  });
});

describe("num", () => {
  it("prints a dash for what is not a number", () => {
    expect(num(null)).toBe("—");
    expect(num(undefined)).toBe("—");
    expect(num("abc")).toBe("—");
    expect(num(Number.NaN)).toBe("—");
  });

  it("groups, and pads to exactly the places asked for", () => {
    expect(num(0)).toBe("0");
    expect(num(1234567)).toBe("1,234,567");
    expect(num(21.26, 1)).toBe("21.3");
    expect(num(5, 2)).toBe("5.00");
    expect(num("12.5", 1)).toBe("12.5");
  });
});

describe("took", () => {
  it("says under a millisecond rather than zero", () => {
    expect(took(0)).toBe("<1ms");
    expect(took(0.4)).toBe("<1ms");
  });

  it("is otherwise the usual time", () => {
    expect(took(1)).toBe("1ms");
    expect(took(1840)).toBe("1.84s");
  });
});

describe("ago", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const at = (now: number) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(now * 1000);
  };

  it("steps through minutes, hours and days", () => {
    const now = 1_760_000_000;
    at(now);
    expect(ago(now)).toBe("just now");
    expect(ago(now + 30)).toBe("just now"); // a clock ahead of ours is not "in the future"
    expect(ago(now - 59)).toBe("just now");
    expect(ago(now - 60)).toBe("1 min ago");
    expect(ago(now - 3599)).toBe("59 min ago");
    expect(ago(now - 3600)).toBe("1 h ago");
    expect(ago(now - 86399)).toBe("23 h ago");
    expect(ago(now - 86400)).toBe("1 d ago");
    expect(ago(now - 6 * 86400)).toBe("6 d ago");
  });

  it("gives the date once it is a week old", () => {
    const now = 1_760_000_000;
    at(now);
    const then = now - 7 * 86400;
    expect(ago(then)).toBe(
      new Date(then * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" }),
    );
  });
});

describe("weekdayShort", () => {
  it("names the day", () => {
    const date = "2026-10-13";
    expect(weekdayShort(date)).toBe(
      new Date(date).toLocaleDateString(undefined, { weekday: "short" }),
    );
    expect(weekdayShort(date)).not.toBe("");
  });
});
