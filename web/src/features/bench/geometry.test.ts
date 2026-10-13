import { describe, expect, it } from "vitest";
import type { BenchReference, BenchRun } from "@/lib/types";
import {
  barShare,
  barTop,
  budgetShare,
  historyGeometry,
  historyPoints,
  parseRange,
  rangeChart,
} from "./geometry";

const run = (id: string, gen_tps: number | null, created_at: number): BenchRun => ({
  id,
  kind: "bench",
  label: "short",
  prompt_tokens: 34,
  gen_tokens: 128,
  ttft_ms: 742,
  prefill_tps: 45.8,
  gen_tps,
  rss_mb: 1380,
  created_at,
});

describe("bars", () => {
  it("measure against a little more than the largest value", () => {
    expect(
      barTop([
        { label: "a", value: 10 },
        { label: "b", value: 25 },
      ]),
    ).toBeCloseTo(28);
    expect(barTop([{ label: "a", value: 10 }], 100)).toBe(100);
  });

  it("are a share of the track to four places", () => {
    expect(barShare(14, 28)).toBe("0.5000");
    expect(barShare(9.84, 11.0208)).toBe("0.8929");
  });

  it("are never quite nothing", () => {
    expect(barShare(0, 100)).toBe("0.0150");
    expect(barShare(0.1, 100)).toBe("0.0150");
  });
});

describe("parseRange", () => {
  it("reads a range written with an en dash or a hyphen", () => {
    expect(parseRange("6–12")).toEqual([6, 12]);
    expect(parseRange("30-55")).toEqual([30, 55]);
    expect(parseRange("0.5 – 1.5")).toEqual([0.5, 1.5]);
  });

  it("reads a single figure as that and a quarter more", () => {
    expect(parseRange("500+")).toEqual([500, 625]);
    expect(parseRange("40")).toEqual([40, 50]);
  });

  it("reads nothing as nothing", () => {
    expect(parseRange("—")).toEqual([0, 0]);
    expect(parseRange(undefined)).toEqual([0, 0]);
  });
});

describe("rangeChart", () => {
  const reference: BenchReference[] = [
    { hw: "2 vCPU shared cloud", threads: 2, prefill: "25–60", gen: "6–12" },
    { hw: "Apple M2 (Metal)", threads: "—", prefill: "500+", gen: "45–70" },
  ];

  it("puts every machine on one scale, a little past the fastest", () => {
    const chart = rangeChart(reference);
    expect(chart.max).toBeCloseTo(75.6);
    expect(Math.round(chart.max)).toBe(76);
    expect(chart.here).toBeNull();
    expect(chart.rows.map((r) => [r.left, r.width])).toEqual([
      ["7.94", "7.94"],
      ["59.52", "33.07"],
    ]);
  });

  it("marks this server where it measured", () => {
    expect(rangeChart(reference, 9.71).here).toBe("12.84");
  });

  it("stretches the scale for a server faster than every reference", () => {
    const chart = rangeChart(reference, 100);
    expect(chart.max).toBeCloseTo(108);
    expect(chart.here).toBe("92.59");
  });

  it("gives a point range a band wide enough to see", () => {
    const chart = rangeChart([{ hw: "x", threads: 1, prefill: "1", gen: "10–10" }], 1000);
    expect(chart.rows[0]?.width).toBe("1.00");
  });
});

describe("the history line", () => {
  it("takes runs that have a rate, oldest first, the last sixty", () => {
    const runs = Array.from({ length: 70 }, (_, i) =>
      run(`r${i}`, i === 3 ? null : 10 + i, 1000 - i),
    );
    const pts = historyPoints(runs);
    expect(pts).toHaveLength(60);
    // Stored newest first; drawn oldest first.
    expect(pts[0]?.id).toBe("r60");
    expect(pts[59]?.id).toBe("r0");
    expect(pts.some((p) => p.id === "r3")).toBe(false);
  });

  it("is nothing with fewer than two points", () => {
    expect(historyGeometry([], 720)).toBeNull();
    expect(historyGeometry([run("a", 10, 1)], 720)).toBeNull();
  });

  it("is drawn in real pixels for the width it is given", () => {
    const g = historyGeometry([run("a", 10, 1), run("b", 20, 2), run("c", 15, 3)], 720);
    expect(g).not.toBeNull();
    if (!g) return;
    expect([g.width, g.height]).toEqual([720, 190]);
    // The tallest point sits below the top by the headroom: 20 of 23.
    expect(g.line).toBe("M42.0 97.7 L373.0 33.3 L704.0 65.5");
    expect(g.area).toBe("M42.0 97.7 L373.0 33.3 L704.0 65.5 L704.0 162 L42 162 Z");
    expect(g.grid.map((t) => t.value)).toEqual(["0", "6", "12", "17", "23"]);
    expect(g.grid[0]).toEqual({ y: "162.0", labelY: "165.0", value: "0" });
    expect(g.points.map((p) => [p.x, p.y, p.run.id])).toEqual([
      ["42.0", "97.7", "a"],
      ["373.0", "33.3", "b"],
      ["704.0", "65.5", "c"],
    ]);
    expect(g.last).toEqual({ x: "698.0", y: "55.5", value: 15 });
    expect([g.left, g.right, g.labelX, g.captionY]).toEqual([42, 704, 34, 184]);
  });

  it("is never drawn narrower than 280", () => {
    const g = historyGeometry([run("a", 10, 1), run("b", 20, 2)], 120);
    expect(g?.width).toBe(280);
    expect(g?.right).toBe(264);
  });
});

describe("budgetShare", () => {
  it("is a percentage to two places", () => {
    expect(budgetShare(990, 1373)).toBe(72.1);
    expect(budgetShare(63, 1373)).toBe(4.59);
    expect(budgetShare(1, 3)).toBe(33.33);
  });
});
